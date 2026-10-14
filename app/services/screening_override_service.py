"""Tenant/branch scoped HR overrides, with versioned writes and stable replay."""
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.errors import CustomError, ErrorCodes
from app.core.screening_evidence import validate_screening_source
from app.core.security import CurrentUser
from app.core.transactions import current_session, transactional
from app.models.job_application import JobApplication
from app.models.screening_override import ScreeningOverride
from app.models.screening_result import ScreeningResult
from app.schemas.recruitment import ScreeningResultResponse
from app.schemas.screening_override import (
    ScreeningOverrideAIBase, ScreeningOverrideCreate, ScreeningOverrideResponse,
    ScreeningOverrideHistoryResponse, EffectiveScreeningAssessment, ScreeningAssessmentResponse,
)
from app.services.extraction_service import ExtractionService
from app.services.recruitment_service import ScreeningService
from app.services.screening_source_service import fence_screening_source
from app.utils.time import ensure_utc, now_utc


def override_response(item: ScreeningOverride) -> ScreeningOverrideResponse:
    return ScreeningOverrideResponse(
        id=str(item.id), company_id=str(item.company_id), application_id=str(item.application_id),
        screening_result_id=str(item.screening_result_id), screening_run_id=str(item.screening_run_id),
        resume_file_id=str(item.resume_file_id), version=item.version, base_version=item.base_version,
        reviewer_id=str(item.reviewer_id), reason=item.reason, overall_score=item.overall_score,
        decision=item.decision, comments=item.comments, created_at=ensure_utc(item.created_at), ai_base=item.ai_base,
    )


class ScreeningOverrideService:
    @staticmethod
    async def authorized_result(run_id: str, company_id: str, current_user: CurrentUser) -> ScreeningResult:
        # Shared policy checks current company, application branch and original
        # resume branch before reading history or replaying any stored command.
        result = await ScreeningService.get_result(run_id, company_id, current_user)
        scoped = await ScreeningResult.find_one({
            "_id": result.id, "company_id": result.company_id,
            "application_id": result.application_id, "screening_run_id": result.screening_run_id,
        }, session=current_session())
        if not scoped:
            raise CustomError(ErrorCodes.NOT_FOUND, "Screening result not available", 404)
        return scoped

    @staticmethod
    @transactional
    async def create(run_id: str, data: ScreeningOverrideCreate, idempotency_key: str,
                     current_user: CurrentUser) -> ScreeningOverride:
        if not current_user.has_permission("screening_results:override"):
            raise CustomError(ErrorCodes.FORBIDDEN, "Screening override permission denied", 403)
        result = await ScreeningOverrideService.authorized_result(run_id, data.company_id, current_user)
        await ExtractionService.authorized_resume(str(result.resume_file_id), current_user,
                                                  permission="screening_runs:view")
        existing = await ScreeningOverride.find_one(
            {"company_id": result.company_id, "idempotency_key": idempotency_key}, session=current_session())
        if existing:
            if (existing.screening_result_id != result.id or str(existing.reviewer_id) != current_user.user_id
                    or existing.base_version != data.expected_version or existing.overall_score != data.overall_score
                    or existing.decision != data.decision or existing.reason != data.reason
                    or existing.comments != data.comments):
                raise CustomError(ErrorCodes.CONFLICT, "Override idempotency key belongs to another command", 409)
            return existing
        if result.override_revision != data.expected_version:
            raise CustomError(ErrorCodes.CONFLICT, "Screening override version changed", 409)
        try:
            source = validate_screening_source(result.source_snapshot)
        except ValueError:
            raise CustomError(ErrorCodes.CONFLICT, "Screening source cannot be reviewed as current", 409) from None
        application = await JobApplication.find_one({
            "_id": result.application_id, "company_id": result.company_id,
            "resume_file_id": result.resume_file_id, "job_requirement_id": result.job_requirement_id,
            "latest_screening_result_id": result.id,
        }, session=current_session())
        if not application:
            raise CustomError(ErrorCodes.CONFLICT, "Only the application's current screening result can be overridden", 409)
        try:
            await fence_screening_source(result.resume_file_id, result.company_id, source)
        except CustomError as exc:
            if exc.status_code != 422:
                raise
            raise CustomError(ErrorCodes.CONFLICT, "Screening source is no longer current; screen the revised profile first", 409) from None
        # Share an application write with rescreening/pointer changes, so a
        # concurrent new result or rebinding cannot leave an override on a stale base.
        fenced = await JobApplication.get_motor_collection().update_one({
            "_id": application.id, "company_id": result.company_id,
            "resume_file_id": result.resume_file_id, "job_requirement_id": result.job_requirement_id,
            "latest_screening_result_id": result.id, "revision": application.revision,
        }, {"$inc": {"revision": 1}}, session=current_session())
        if fenced.matched_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Screening application changed concurrently", 409)
        # First response and Mongo replay must expose identical timestamps.
        created_at = now_utc()
        created_at = created_at.replace(microsecond=(created_at.microsecond // 1000) * 1000)
        override = ScreeningOverride(
            id=ObjectId(), company_id=result.company_id, application_id=result.application_id,
            screening_result_id=result.id, screening_run_id=result.screening_run_id,
            resume_file_id=result.resume_file_id, version=data.expected_version + 1, base_version=data.expected_version,
            idempotency_key=idempotency_key, reviewer_id=ObjectId(current_user.user_id),
            reason=data.reason, overall_score=data.overall_score, decision=data.decision, comments=data.comments,
            created_at=created_at,
            ai_base=ScreeningOverrideAIBase(
                screening_result_id=str(result.id), screening_run_id=str(result.screening_run_id),
                scoring_version=result.scoring_version, overall_score=result.overall_score,
                decision=result.decision, evaluated_at=ensure_utc(result.evaluated_at),
                resume_profile_id=str(source.resume_profile_id), profile_version=source.profile_version,
                source_parse_revision=source.source_parse_revision, source_text_sha256=source.source_text_sha256,
                scorecard_id=str(result.scorecard_id) if result.scorecard_id else None,
                scorecard_version=result.scorecard_snapshot.get("version"),
                ai_model_id=str(result.ai_model_id) if result.ai_model_id else None,
                ai_model_used=result.ai_model_used, ai_model_version=result.ai_model_version,
            ),
        )
        predicate = {"_id": result.id, "company_id": result.company_id,
                     "application_id": result.application_id, "screening_run_id": result.screening_run_id}
        if data.expected_version == 0:
            predicate["$or"] = [{"override_revision": 0}, {"override_revision": {"$exists": False}}]
        else:
            predicate["override_revision"] = data.expected_version
        updated = await ScreeningResult.get_motor_collection().update_one(
            predicate, {"$inc": {"override_revision": 1}, "$set": {"latest_override_id": override.id}},
            session=current_session(),
        )
        if updated.matched_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Screening override version changed concurrently", 409)
        try:
            await override.insert(session=current_session())
        except DuplicateKeyError:
            raise CustomError(ErrorCodes.CONFLICT, "Concurrent override; retry with the same idempotency key", 409) from None
        return override

    @staticmethod
    async def history(run_id: str, company_id: str, page: int, size: int,
                      current_user: CurrentUser) -> ScreeningOverrideHistoryResponse:
        result = await ScreeningOverrideService.authorized_result(run_id, company_id, current_user)
        if page < 1 or size < 1:
            raise CustomError(ErrorCodes.VALIDATION, "Invalid override history pagination", 422)
        size = min(size, settings.MAX_PAGE_SIZE, 100)
        query = {"company_id": result.company_id, "application_id": result.application_id,
                 "screening_result_id": result.id, "screening_run_id": result.screening_run_id}
        total = await ScreeningOverride.find(query, session=current_session()).count()
        items = await ScreeningOverride.find(query, session=current_session()).sort("-version").skip(
            (page - 1) * size).limit(size).to_list()
        return ScreeningOverrideHistoryResponse(items=[override_response(item) for item in items],
                                                total=total, page=page, size=size)

    @staticmethod
    async def assessment(run_id: str, company_id: str, current_user: CurrentUser) -> ScreeningAssessmentResponse:
        result = await ScreeningOverrideService.authorized_result(run_id, company_id, current_user)
        source_current = await ScreeningService.source_is_current(result)
        application = await JobApplication.find_one({"_id": result.application_id, "company_id": result.company_id},
                                                     session=current_session())
        latest_result = bool(application and application.latest_screening_result_id == result.id)
        override = None
        if result.latest_override_id:
            override = await ScreeningOverride.find_one({
                "_id": result.latest_override_id, "company_id": result.company_id,
                "application_id": result.application_id, "screening_result_id": result.id,
                "screening_run_id": result.screening_run_id, "version": result.override_revision,
            }, session=current_session())
            if not override:
                raise CustomError(ErrorCodes.CONFLICT, "Screening override history is inconsistent", 409)
        elif result.override_revision:
            raise CustomError(ErrorCodes.CONFLICT, "Screening override history is inconsistent", 409)
        metadata = {"source_is_current": source_current}
        if result.source_snapshot:
            source = result.source_snapshot
            metadata.update(resume_profile_id=str(source.resume_profile_id), profile_version=source.profile_version,
                            source_parse_revision=source.source_parse_revision, source_text_sha256=source.source_text_sha256)
        ai = ScreeningResultResponse.model_validate(result).model_copy(update=metadata)
        effective = EffectiveScreeningAssessment(source="ai", overall_score=result.overall_score, decision=result.decision)
        if override:
            effective = EffectiveScreeningAssessment(
                source="hr_override", overall_score=override.overall_score, decision=override.decision,
                reason=override.reason, comments=override.comments, override_id=str(override.id),
                override_version=override.version, reviewer_id=str(override.reviewer_id), reviewed_at=ensure_utc(override.created_at),
            )
        return ScreeningAssessmentResponse(
            ai=ai, effective=effective, latest_override=override_response(override) if override else None,
            override_revision=result.override_revision, source_is_current=source_current,
            is_latest_result=latest_result,
            review_required=source_current is not True or not latest_result or effective.decision == "hold",
        )

