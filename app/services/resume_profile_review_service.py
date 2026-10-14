"""Append-only HR revisions with a shared CV write fence and command replay."""
import hashlib
import json

from beanie import PydanticObjectId
from bson import ObjectId

from app.core.errors import CustomError, ErrorCodes
from app.core.resume_extraction import validate_resume_extraction
from app.core.transactions import current_session, transactional
from app.models.resume_extraction import ResumeExtractionRun, ResumeProfile
from app.models.resume_file import ResumeFile
from app.models.screening_run import ResumeParseRun
from app.schemas.resume_profile_review import HRConfirmedFact, ProfileRevisionCreate
from app.services.extraction_service import ExtractionService, requires_review
from app.utils.time import now_utc


class ResumeProfileReviewService:
    @staticmethod
    @transactional
    async def revise(resume_id: str, data: ProfileRevisionCreate, key: str, caller) -> tuple[ResumeProfile, bool]:
        if not caller.has_permission("resume_profiles:view"):
            raise CustomError(ErrorCodes.FORBIDDEN, "Resume profile access denied", 403)
        resume = await ExtractionService.authorized_resume(resume_id, caller, permission="resume_profiles:edit")
        input_hash = hashlib.sha256(json.dumps(data.model_dump(mode="json"), sort_keys=True,
                                               separators=(",", ":")).encode("utf-8")).hexdigest()
        previous = await ResumeProfile.find_one(
            {"company_id": resume.company_id, "idempotency_key": key}, session=current_session()
        )
        if previous:
            if previous.resume_file_id != resume.id or previous.edit_input_hash != input_hash or str(previous.edited_by) != caller.user_id:
                raise CustomError(ErrorCodes.CONFLICT, "Idempotency key belongs to a different profile revision", 409)
            return previous, resume.status == "parsed" and resume.latest_extraction_profile_id == previous.id
        parent = await ResumeProfile.find_one(
            {"_id": data.base_profile_id, "resume_file_id": resume.id, "company_id": resume.company_id},
            session=current_session(),
        )
        if not parent:
            raise CustomError(ErrorCodes.NOT_FOUND, "Base profile not found", 404)
        if (resume.status != "parsed" or not resume.parsed_data or not resume.parsed_data.raw_text
                or resume.latest_extraction_profile_id != parent.id or parent.version != data.expected_version
                or parent.source_parse_revision != resume.parse_revision
                or parent.source_parse_run_id != resume.latest_parse_run_id):
            raise CustomError(ErrorCodes.CONFLICT, "Profile changed; reload the current profile before editing", 409)
        for model in (ResumeParseRun, ResumeExtractionRun):
            if await model.find_one({"resume_file_id": resume.id, "is_terminal": False}, session=current_session()):
                raise CustomError(ErrorCodes.CONFLICT, "Wait for active parsing or extraction before editing", 409)
        try:
            profile = validate_resume_extraction(data.profile or parent.profile, raw_text=resume.parsed_data.raw_text)
        except ValueError:
            raise CustomError(ErrorCodes.VALIDATION, "Profile corrections require valid CV evidence; use HR confirmations for other facts", 422) from None
        now = now_utc()
        # BSON datetime precision is milliseconds; replay must preserve exactly
        # the same confirmation timestamp as the first successful response.
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        annotations = parent.hr_annotations if data.hr_annotations is None else [
            HRConfirmedFact(**fact.model_dump(), id=ObjectId(), confirmed_by=ObjectId(caller.user_id), confirmed_at=now)
            for fact in data.hr_annotations
        ]
        version = max(resume.extraction_revision, parent.version) + 1
        revision = ResumeProfile(
            id=ObjectId(), company_id=resume.company_id, resume_file_id=resume.id, extraction_run_id=parent.extraction_run_id,
            version=version, source_parse_revision=resume.parse_revision, source_parse_run_id=resume.latest_parse_run_id,
            model_name=parent.model_name, prompt_version=parent.prompt_version, extractor_version=parent.extractor_version,
            profile=profile, review_required=requires_review(profile), origin="hr", parent_profile_id=parent.id,
            edited_by=ObjectId(caller.user_id), edit_reason=data.reason, hr_annotations=annotations,
            idempotency_key=key, edit_input_hash=input_hash,
            created_at=now,
        )
        predicate = {
            "_id": resume.id, "company_id": resume.company_id, "is_deleted": False, "status": "parsed",
            "checksum": resume.checksum, "latest_extraction_profile_id": parent.id,
            "parsed_data.raw_text": resume.parsed_data.raw_text, "latest_parse_run_id": resume.latest_parse_run_id,
            "$and": [
                {"$or": [{"parse_revision": resume.parse_revision}, {"parse_revision": None}]} if not resume.parse_revision else {"parse_revision": resume.parse_revision},
                {"$or": [{"extraction_revision": 0}, {"extraction_revision": None}]} if not resume.extraction_revision else {"extraction_revision": resume.extraction_revision},
            ],
        }
        changed = await ResumeFile.get_motor_collection().update_one(
            predicate, {"$set": {"latest_extraction_profile_id": revision.id, "extraction_revision": version},
                        "$inc": {"screening_fence_version": 1}}, session=current_session(),
        )
        # Pointer and immutable revision are committed together, or both roll back.
        if changed.matched_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Profile changed concurrently; retry with the same key", 409)
        await revision.insert(session=current_session())
        return revision, True

    @staticmethod
    async def history(resume_id: str, caller, page: int = 1, size: int = 20):
        resume = await ExtractionService.authorized_resume(resume_id, caller, permission="resume_profiles:view")
        if page < 1 or not 1 <= size <= 100:
            raise CustomError(ErrorCodes.VALIDATION, "Invalid profile history pagination", 422)
        query = {"resume_file_id": resume.id, "company_id": resume.company_id}
        total = await ResumeProfile.find(query, session=current_session()).count()
        items = await ResumeProfile.find(query, session=current_session()).sort([("version", -1), ("_id", -1)]).skip((page - 1) * size).limit(size).to_list()
        return resume, items, total
