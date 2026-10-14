"""Tenant-scoped extraction commands and fenced worker completion."""
import hashlib
import json
from datetime import timedelta

from bson import ObjectId
from beanie import PydanticObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core import job_queue
from app.core.config import settings
from app.core.errors import CustomError, ErrorCodes
from app.core.resume_extraction import validate_resume_extraction
from app.core.security import CurrentUser
from app.core.tenant_policy import branch_role
from app.core.transactions import current_session, transactional
from app.models.resume_extraction import PROMPT_VERSION, ResumeExtractionRun, ResumeProfile
from app.models.resume_file import ResumeFile
from app.models.screening_run import ProcessingStatus, parse_retry_due_filter
from app.schemas.extraction import ExtractionServiceOutput
from app.schemas.resume_extraction import ExtractedValue, EvidencedRecord
from app.services.tenant_access_service import TenantAccessService
from app.utils.time import now_utc

LEASE = timedelta(minutes=5)


def source_predicate(run: ResumeExtractionRun) -> dict:
    predicate = {
        "_id": run.resume_file_id, "company_id": run.company_id,
        "is_deleted": False, "status": "parsed", "parsed_data.raw_text": run.source_text,
    }
    if run.source_parse_revision:
        predicate["parse_revision"] = run.source_parse_revision
    else:
        predicate["$or"] = [{"parse_revision": 0}, {"parse_revision": None}]
    return predicate


def lease_predicate(run_id: str, worker_id: str, generation: int) -> dict:
    return {"_id": PydanticObjectId(run_id), "status": "running", "worker_id": worker_id,
            "attempt": generation, "lease_expires_at": {"$gt": now_utc()}}


def requires_review(value) -> bool:
    from pydantic import BaseModel
    if isinstance(value, ExtractedValue) and value.status != "extracted":
        return True
    if isinstance(value, EvidencedRecord) and value.review_issues:
        return True
    if isinstance(value, BaseModel):
        if getattr(value, "status", None) == "insufficient_evidence":
            return True
        return any(requires_review(getattr(value, key)) for key in type(value).model_fields)
    return isinstance(value, list) and any(requires_review(item) for item in value)


class ExtractionService:
    @staticmethod
    async def authorized_resume(resume_id: str, current_user: CurrentUser, *, permission: str) -> ResumeFile:
        from app.services.resume_service import ResumeService
        return await ResumeService.authorized_resume(resume_id, current_user, permission=permission)

    @staticmethod
    @transactional
    async def start(resume_id: str, company_id: str, idempotency_key: str, current_user: CurrentUser) -> ResumeExtractionRun:
        resume = await ExtractionService.authorized_resume(resume_id, current_user, permission="resume_files:extract")
        if str(resume.company_id) != company_id:
            raise CustomError(ErrorCodes.FORBIDDEN, "Resume does not belong to this company", 403)
        existing = await ResumeExtractionRun.find_one(
            {"company_id": resume.company_id, "idempotency_key": idempotency_key}, session=current_session())
        if existing:
            if existing.resume_file_id != resume.id:
                raise CustomError(ErrorCodes.CONFLICT, "Idempotency key belongs to another resume", 409)
            return existing
        raw_text = resume.parsed_data.raw_text if resume.parsed_data else None
        if resume.status != "parsed" or not raw_text or not raw_text.strip():
            raise CustomError(ErrorCodes.CONFLICT, "Resume must finish parsing before extraction", 409)
        if len(raw_text) > settings.EXTRACTION_MAX_SOURCE_CHARACTERS:
            raise CustomError(ErrorCodes.VALIDATION, "Resume text exceeds extraction context limit", 422)
        if not settings.EXTRACTION_API_URL or not settings.EXTRACTION_MODEL_NAME or not settings.EXTRACTION_SERVICE_TOKEN:
            raise CustomError(ErrorCodes.INTERNAL, "Extraction service is not configured", 503)
        source_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        input_hash = hashlib.sha256(json.dumps([
            str(resume.id), resume.parse_revision, source_hash, settings.EXTRACTION_MODEL_NAME, PROMPT_VERSION,
        ]).encode()).hexdigest()
        active = await ResumeExtractionRun.find_one({"resume_file_id": resume.id, "is_terminal": False}, session=current_session())
        if active:
            # The original key is handled above. A different key must not be
            # accepted without a durable mapping to the returned run.
            raise CustomError(ErrorCodes.CONFLICT, "Another extraction is active; retry with its original idempotency key", 409)
        run = ResumeExtractionRun(
            company_id=resume.company_id, resume_file_id=resume.id, triggered_by=ObjectId(current_user.user_id),
            idempotency_key=idempotency_key, input_hash=input_hash,
            source_text=raw_text, source_text_sha256=source_hash, source_parse_revision=resume.parse_revision,
            source_parse_run_id=resume.latest_parse_run_id, model_name=settings.EXTRACTION_MODEL_NAME,
        )
        # Start and completion use the same zero/null compatibility policy for
        # resumes predating parse_revision, without weakening nonzero revisions.
        predicate = source_predicate(run)
        predicate["latest_extraction_profile_id"] = resume.latest_extraction_profile_id
        changed = await ResumeFile.get_motor_collection().update_one(
            predicate,
            {"$inc": {"screening_fence_version": 1}}, session=current_session(),
        )
        if changed.matched_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Resume changed concurrently; retry with the same key", 409)
        try:
            await run.insert(session=current_session())
        except DuplicateKeyError:
            raise CustomError(ErrorCodes.CONFLICT, "Concurrent extraction; retry with the same idempotency key", 409) from None
        await job_queue.enqueue("resume-extract", str(run.id), str(run.company_id))
        return run

    @staticmethod
    async def get_run(resume_id: str, run_id: str, current_user: CurrentUser) -> ResumeExtractionRun:
        resume = await ExtractionService.authorized_resume(resume_id, current_user, permission="resume_files:view")
        if not ObjectId.is_valid(run_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid extraction run ID", 422)
        run = await ResumeExtractionRun.find_one({"_id": ObjectId(run_id), "resume_file_id": resume.id, "company_id": resume.company_id})
        if not run:
            raise CustomError(ErrorCodes.NOT_FOUND, "Extraction run not found", 404)
        return run

    @staticmethod
    async def get_profile(resume_id: str, current_user: CurrentUser, profile_id: str | None = None):
        resume = await ExtractionService.authorized_resume(resume_id, current_user, permission="resume_profiles:view")
        if profile_id and not ObjectId.is_valid(profile_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid profile ID", 422)
        target = ObjectId(profile_id) if profile_id else resume.latest_extraction_profile_id
        result = await ResumeProfile.find_one({"_id": target, "resume_file_id": resume.id, "company_id": resume.company_id}) if target else None
        if not result:
            raise CustomError(ErrorCodes.NOT_FOUND, "Resume profile not found", 404)
        current = (resume.status == "parsed" and resume.latest_extraction_profile_id == result.id
                   and resume.parse_revision == result.source_parse_revision)
        return result, current

    @staticmethod
    async def claim(run_id: str, worker_id: str):
        now = now_utc()
        document = await ResumeExtractionRun.get_motor_collection().find_one_and_update({
            "_id": ObjectId(run_id), "status": "queued", "$expr": {"$lt": ["$attempt", "$max_attempts"]},
            **parse_retry_due_filter(now),
        }, {"$set": {"status": "running", "worker_id": worker_id, "started_at": now,
                    "lease_expires_at": now + LEASE, "updated_at": now}, "$inc": {"attempt": 1}},
            return_document=ReturnDocument.AFTER, session=current_session())
        return ResumeExtractionRun.model_validate(document) if document is not None else None

    @staticmethod
    async def renew(run_id: str, worker_id: str, *, generation: int) -> bool:
        result = await ResumeExtractionRun.find_one(lease_predicate(run_id, worker_id, generation)).update(
            {"$set": {"lease_expires_at": now_utc() + LEASE, "updated_at": now_utc()}})
        # Claim and preflight can share the same BSON millisecond: a no-op
        # renewal still owns the live lease when the fenced predicate matches.
        return bool(result and result.matched_count == 1)

    @staticmethod
    @transactional
    async def complete(run_id: str, worker_id: str, output: ExtractionServiceOutput, *, generation: int) -> bool:
        run = await ResumeExtractionRun.find_one(lease_predicate(run_id, worker_id, generation), session=current_session())
        if not run:
            return False
        if output.model_name != run.model_name or output.prompt_version != run.prompt_version:
            raise CustomError(ErrorCodes.VALIDATION, "Extraction configuration does not match run", 422)
        try:
            profile = validate_resume_extraction(output.profile, raw_text=run.source_text)
        except ValueError:
            raise CustomError(ErrorCodes.VALIDATION, "Invalid extraction evidence or data", 422) from None
        resume = await ResumeFile.find_one(source_predicate(run), session=current_session())
        if not resume:
            raise CustomError(ErrorCodes.VALIDATION, "Extraction source has changed or was deleted", 422)
        result = ResumeProfile(
            id=ObjectId(), company_id=run.company_id, resume_file_id=run.resume_file_id, extraction_run_id=run.id,
            version=resume.extraction_revision + 1, source_parse_revision=run.source_parse_revision,
            source_parse_run_id=run.source_parse_run_id, model_name=run.model_name,
            prompt_version=run.prompt_version, extractor_version=output.extractor_version,
            profile=profile, review_required=requires_review(profile),
        )
        updated = await ResumeFile.find_one(source_predicate(run), session=current_session()).update({
            "$set": {"latest_extraction_profile_id": result.id}, "$inc": {"extraction_revision": 1},
        }, session=current_session())
        if not updated or updated.modified_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Extraction source changed concurrently", 409)
        await result.insert(session=current_session())
        updated = await ResumeExtractionRun.find_one(lease_predicate(run_id, worker_id, generation), session=current_session()).update({
            "$set": {"status": "completed", "is_terminal": True, "result_id": result.id,
                     "finished_at": now_utc(), "updated_at": now_utc(), "lease_expires_at": None,
                     "error_code": None, "error_message": None, "next_retry_at": None},
        }, session=current_session())
        if not updated or updated.modified_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Extraction worker lease lost", 409)
        return True

    @staticmethod
    @transactional
    async def fail(run_id: str, worker_id: str, error_code: str, error_message: str, *, generation: int, retryable: bool = True) -> bool:
        run = await ResumeExtractionRun.find_one(lease_predicate(run_id, worker_id, generation), session=current_session())
        if not run:
            return False
        terminal = not retryable or run.attempt >= run.max_attempts
        result = await ResumeExtractionRun.find_one(lease_predicate(run_id, worker_id, generation), session=current_session()).update({
            "$set": {"status": "failed" if terminal else "queued", "is_terminal": terminal,
                     "error_code": error_code[:100], "error_message": "Resume extraction failed",
                     "worker_id": None, "lease_expires_at": None, "queue_message_id": None, "last_enqueued_at": None,
                     "finished_at": now_utc() if terminal else None,
                     "next_retry_at": None if terminal else now_utc() + timedelta(seconds=min(60, 5 * 2 ** (run.attempt - 1))),
                     "updated_at": now_utc()},
        }, session=current_session())
        if not result or result.modified_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Extraction worker lease lost", 409)
        # Maintenance republishes queued runs after next_retry_at; Mongo is the outbox.
        return True
