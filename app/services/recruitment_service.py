import hashlib
import json
import logging
import uuid
from typing import Optional

from beanie import PydanticObjectId
from fastapi import status
from pymongo.errors import DuplicateKeyError

from app.core import cache
from app.core import job_queue
from app.core.config import settings
from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser
from app.models.ai_model import AIModel
from app.models.application_review import ApplicationReview
from app.models.application_stage_event import ApplicationStageEvent
from app.models.candidate import Candidate
from app.models.company_branch import CompanyBranch
from app.models.company import Company
from app.models.job_application import ApplicationStage, ApplicationStatus, JobApplication
from app.models.job_requirement import JobRequirement, JobStatus
from app.models.job_scorecard import JobScorecard
from app.models.resume_file import ResumeFile
from app.models.screening_run import ScreeningRun
from app.models.user import User
from app.models.user_company import UserCompany
from app.repositories.recruitment_repository import RecruitmentRepository
from app.schemas.recruitment import (
    ApplicationCreate,
    ApplicationReviewCreate,
    ScorecardCreate,
    ScreeningStartRequest,
    StageTransitionRequest,
)
from app.services.tenant_access_service import TenantAccessService
from app.utils.time import ensure_utc, now_utc

logger = logging.getLogger(__name__)


ALLOWED_STAGE_TRANSITIONS: dict[ApplicationStage, set[ApplicationStage]] = {
    ApplicationStage.APPLIED: {ApplicationStage.SCREENING, ApplicationStage.REJECTED, ApplicationStage.WITHDRAWN},
    ApplicationStage.SCREENING: {ApplicationStage.SCREENED, ApplicationStage.REJECTED, ApplicationStage.WITHDRAWN},
    ApplicationStage.SCREENED: {ApplicationStage.INTERVIEW, ApplicationStage.REJECTED, ApplicationStage.WITHDRAWN},
    ApplicationStage.INTERVIEW: {
        ApplicationStage.INTERVIEW,
        ApplicationStage.OFFER,
        ApplicationStage.REJECTED,
        ApplicationStage.WITHDRAWN,
    },
    ApplicationStage.OFFER: {ApplicationStage.HIRED, ApplicationStage.REJECTED, ApplicationStage.WITHDRAWN},
    ApplicationStage.HIRED: set(),
    ApplicationStage.REJECTED: set(),
    ApplicationStage.WITHDRAWN: set(),
}


def _terminal_status(stage: ApplicationStage) -> ApplicationStatus:
    return {
        ApplicationStage.HIRED: ApplicationStatus.HIRED,
        ApplicationStage.REJECTED: ApplicationStatus.REJECTED,
        ApplicationStage.WITHDRAWN: ApplicationStatus.WITHDRAWN,
    }.get(stage, ApplicationStatus.ACTIVE)


def _application_matches_request(
    application: JobApplication,
    data: ApplicationCreate,
    assigned_recruiter_id: Optional[PydanticObjectId],
) -> bool:
    return (
        str(application.candidate_id) == data.candidate_id
        and str(application.resume_file_id) == data.resume_file_id
        and str(application.job_requirement_id) == data.job_requirement_id
        and application.source == data.source
        and (
            str(application.assigned_recruiter_id) if application.assigned_recruiter_id else None
        )
        == (str(assigned_recruiter_id) if assigned_recruiter_id else None)
    )


def _raise_idempotency_mismatch() -> None:
    raise CustomError(
        ErrorCodes.CONFLICT,
        "Idempotency key was already used for a different request",
        status.HTTP_409_CONFLICT,
    )


async def _load_job_company(job_requirement_id: str, company_id: str) -> tuple[JobRequirement, CompanyBranch]:
    job = await JobRequirement.get(PydanticObjectId(job_requirement_id))
    if not job or not job.is_active:
        raise CustomError(ErrorCodes.NOT_FOUND, "Job requirement not found", status.HTTP_404_NOT_FOUND)
    branch = await CompanyBranch.get(job.company_branch_id)
    if not branch or not branch.is_active or str(branch.company_id) != company_id:
        raise CustomError(ErrorCodes.FORBIDDEN, "Job does not belong to this company", status.HTTP_403_FORBIDDEN)
    company = await Company.get(branch.company_id)
    if not company or not company.is_active:
        raise CustomError(ErrorCodes.NOT_FOUND, "Company not found", status.HTTP_404_NOT_FOUND)
    return job, branch


class ApplicationService:
    @staticmethod
    async def create(
        data: ApplicationCreate,
        idempotency_key: str,
        current_user: CurrentUser,
    ) -> JobApplication:
        candidate = await Candidate.find_one(
            {
                "_id": PydanticObjectId(data.candidate_id),
                "company_id": PydanticObjectId(data.company_id),
                "is_deleted": False,
            }
        )
        if not candidate:
            raise CustomError(ErrorCodes.NOT_FOUND, "Candidate not found", status.HTTP_404_NOT_FOUND)

        job, branch = await _load_job_company(data.job_requirement_id, data.company_id)
        is_self_service = (
            current_user.is_candidate
            and not current_user.is_recruiter
            and not current_user.is_admin
            and candidate.user_id is not None
            and str(candidate.user_id) == current_user.user_id
        )
        if is_self_service:
            if job.status != JobStatus.PUBLISHED:
                raise CustomError(ErrorCodes.FORBIDDEN, "Job is not open for applications", status.HTTP_403_FORBIDDEN)
            if job.expiration_time and ensure_utc(job.expiration_time) < now_utc():
                raise CustomError(ErrorCodes.BAD_REQUEST, "Job application period has expired", status.HTTP_400_BAD_REQUEST)
        else:
            await TenantAccessService.require_company_access(current_user, data.company_id)

        assigned_recruiter_id = None
        if data.assigned_recruiter_id:
            assigned_recruiter = await User.find_one(
                {"_id": PydanticObjectId(data.assigned_recruiter_id), "is_active": True}
            )
            if not assigned_recruiter:
                raise CustomError(ErrorCodes.NOT_FOUND, "Assigned recruiter not found", status.HTTP_404_NOT_FOUND)
            company = await Company.get(branch.company_id)
            membership_time = now_utc()
            has_branch_membership = await UserCompany.find_one(
                {
                    "user_id": assigned_recruiter.id,
                    "company_branch_id": branch.id,
                    "is_active": True,
                    "$and": [
                        {"$or": [{"start_date": None}, {"start_date": {"$lte": membership_time}}]},
                        {"$or": [{"end_date": None}, {"end_date": {"$gte": membership_time}}]},
                    ],
                }
            )
            if not (
                assigned_recruiter.is_superuser
                or (company and str(company.user_id) == str(assigned_recruiter.id))
                or has_branch_membership
            ):
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Assigned recruiter does not belong to the job branch",
                    status.HTTP_400_BAD_REQUEST,
                )
            assigned_recruiter_id = assigned_recruiter.id

        resume = await ResumeFile.find_one(
            {
                "_id": PydanticObjectId(data.resume_file_id),
                "company_id": PydanticObjectId(data.company_id),
                "candidate_id": candidate.id,
                "is_deleted": False,
            }
        )
        if not resume:
            raise CustomError(ErrorCodes.NOT_FOUND, "Candidate resume not found", status.HTTP_404_NOT_FOUND)

        existing_id = await cache.get_idempotency_result("application", data.company_id, idempotency_key)
        if existing_id:
            existing = await RecruitmentRepository.get_application(existing_id, data.company_id)
            if existing:
                if not _application_matches_request(existing, data, assigned_recruiter_id):
                    _raise_idempotency_mismatch()
                return existing
        existing = await JobApplication.find_one(
            {"company_id": PydanticObjectId(data.company_id), "idempotency_key": idempotency_key}
        )
        if existing:
            if not _application_matches_request(existing, data, assigned_recruiter_id):
                _raise_idempotency_mismatch()
            return existing

        owner_token = uuid.uuid4().hex
        reserved = await cache.reserve_idempotency(
            "application", data.company_id, idempotency_key, owner_token
        )
        if not reserved:
            raise CustomError(ErrorCodes.CONFLICT, "Application request is already processing", status.HTTP_409_CONFLICT)

        application: Optional[JobApplication] = None
        try:
            application = JobApplication(
                company_id=PydanticObjectId(data.company_id),
                company_branch_id=branch.id,
                candidate_id=candidate.id,
                resume_file_id=resume.id,
                job_requirement_id=job.id,
                applicant_id=candidate.user_id,
                applied_by=PydanticObjectId(current_user.user_id),
                source=data.source,
                assigned_recruiter_id=assigned_recruiter_id,
                idempotency_key=idempotency_key,
            )
            await application.insert()
            await ApplicationStageEvent(
                company_id=PydanticObjectId(data.company_id),
                application_id=application.id,
                from_stage=None,
                to_stage=ApplicationStage.APPLIED,
                changed_by=PydanticObjectId(current_user.user_id),
                reason="Application created",
                idempotency_key=f"create:{idempotency_key}",
            ).insert()
            await cache.complete_idempotency(
                "application", data.company_id, idempotency_key, str(application.id), owner_token
            )
            await cache.delete_pattern(cache.cache_key("application-list", data.company_id, "*"))
            return application
        except DuplicateKeyError:
            existing = await JobApplication.find_one(
                {
                    "company_id": PydanticObjectId(data.company_id),
                    "$or": [
                        {"idempotency_key": idempotency_key},
                        {"job_requirement_id": job.id, "candidate_id": candidate.id},
                    ],
                }
            )
            if existing:
                if existing.idempotency_key == idempotency_key and not _application_matches_request(
                    existing, data, assigned_recruiter_id
                ):
                    _raise_idempotency_mismatch()
                await cache.complete_idempotency(
                    "application", data.company_id, idempotency_key, str(existing.id), owner_token
                )
                return existing
            raise
        except Exception:
            if application and application.id:
                # Compensate only the brand-new write if its initial event failed.
                await application.delete()
            await cache.release_idempotency(
                "application", data.company_id, idempotency_key, owner_token
            )
            raise

    @staticmethod
    async def get(application_id: str, company_id: str, current_user: CurrentUser) -> JobApplication:
        if not PydanticObjectId.is_valid(company_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid company ID", status.HTTP_422_UNPROCESSABLE_ENTITY)
        application = await RecruitmentRepository.get_application(application_id, company_id)
        if not application:
            raise CustomError(ErrorCodes.NOT_FOUND, "Application not found", status.HTTP_404_NOT_FOUND)
        is_owner = (
            current_user.is_candidate
            and not current_user.is_recruiter
            and not current_user.is_admin
            and application.applicant_id is not None
            and str(application.applicant_id) == current_user.user_id
        )
        if not is_owner:
            await TenantAccessService.require_company_access(current_user, company_id)
        return application

    @staticmethod
    async def list(
        company_id: str,
        page: int,
        size: int,
        current_user: CurrentUser,
        job_requirement_id: Optional[str] = None,
        stage: Optional[ApplicationStage] = None,
    ) -> tuple[list[JobApplication], int]:
        if not PydanticObjectId.is_valid(company_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid company ID", status.HTTP_422_UNPROCESSABLE_ENTITY)
        is_candidate_self_service = (
            current_user.is_candidate and not current_user.is_recruiter and not current_user.is_admin
        )
        if not is_candidate_self_service:
            await TenantAccessService.require_company_access(current_user, company_id)
        size = min(size, settings.MAX_PAGE_SIZE)
        if job_requirement_id and not PydanticObjectId.is_valid(job_requirement_id):
            raise CustomError(
                ErrorCodes.VALIDATION,
                "Invalid job requirement ID",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        key = cache.cache_key(
            "application-list",
            company_id,
            page,
            size,
            job_requirement_id or "all",
            stage.value if stage else "all",
            current_user.user_id if is_candidate_self_service else "tenant",
        )
        cached = await cache.get_json(key)
        if cached is not None:
            try:
                return [JobApplication.model_validate(item) for item in cached["items"]], int(cached["total"])
            except (KeyError, TypeError, ValueError):
                await cache.delete_keys(key)
        items, total = await RecruitmentRepository.list_applications(
            company_id,
            page,
            size,
            job_requirement_id,
            stage.value if stage else None,
            current_user.user_id if is_candidate_self_service else None,
        )
        await cache.set_json(
            key,
            {"items": [item.model_dump(by_alias=True) for item in items], "total": total},
            settings.APPLICATION_CACHE_TTL,
        )
        return items, total

    @staticmethod
    async def transition_stage(
        application_id: str,
        company_id: str,
        data: StageTransitionRequest,
        idempotency_key: str,
        current_user: CurrentUser,
    ) -> JobApplication:
        await TenantAccessService.require_company_access(current_user, company_id)
        existing_event = await ApplicationStageEvent.find_one(
            {"company_id": PydanticObjectId(company_id), "idempotency_key": idempotency_key}
        )
        if existing_event:
            if str(existing_event.application_id) != application_id or existing_event.to_stage != data.to_stage:
                _raise_idempotency_mismatch()
            application = await RecruitmentRepository.get_application(
                str(existing_event.application_id), company_id
            )
            if application:
                return application

        application = await RecruitmentRepository.get_application(application_id, company_id)
        if not application:
            raise CustomError(ErrorCodes.NOT_FOUND, "Application not found", status.HTTP_404_NOT_FOUND)
        if data.to_stage not in ALLOWED_STAGE_TRANSITIONS[application.current_stage]:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                f"Invalid transition from {application.current_stage.value} to {data.to_stage.value}",
                status.HTTP_400_BAD_REQUEST,
            )

        owner_token = uuid.uuid4().hex
        if not await cache.reserve_idempotency("stage", company_id, idempotency_key, owner_token):
            raise CustomError(ErrorCodes.CONFLICT, "Stage transition is already processing", status.HTTP_409_CONFLICT)

        old_stage = application.current_stage
        old_revision = application.revision
        now = now_utc()
        set_fields: dict = {
            "current_stage": data.to_stage.value,
            "status": _terminal_status(data.to_stage).value,
            "updated_at": now,
        }
        if data.to_stage == ApplicationStage.HIRED:
            set_fields["hired_at"] = now
        elif data.to_stage == ApplicationStage.REJECTED:
            set_fields["rejected_at"] = now
            set_fields["rejection_reason"] = data.reason
        elif data.to_stage == ApplicationStage.WITHDRAWN:
            set_fields["withdrawn_at"] = now

        try:
            update_result = await JobApplication.find_one(
                {
                    "_id": application.id,
                    "company_id": PydanticObjectId(company_id),
                    "current_stage": old_stage.value,
                    "revision": old_revision,
                }
            ).update({"$set": set_fields, "$inc": {"revision": 1}})
            if not update_result or getattr(update_result, "modified_count", 0) != 1:
                raise CustomError(
                    ErrorCodes.CONFLICT,
                    "Application changed concurrently; reload and retry",
                    status.HTTP_409_CONFLICT,
                )

            try:
                await ApplicationStageEvent(
                    company_id=PydanticObjectId(company_id),
                    application_id=application.id,
                    from_stage=old_stage,
                    to_stage=data.to_stage,
                    changed_by=PydanticObjectId(current_user.user_id),
                    reason=data.reason,
                    idempotency_key=idempotency_key,
                ).insert()
            except Exception:
                # Roll back only if no later transition has changed the row.
                rollback: dict = {
                    "$set": {
                        "current_stage": old_stage.value,
                        "status": application.status.value,
                        "updated_at": application.updated_at,
                    },
                    "$inc": {"revision": -1},
                }
                if data.to_stage == ApplicationStage.HIRED:
                    rollback["$unset"] = {"hired_at": ""}
                elif data.to_stage == ApplicationStage.REJECTED:
                    rollback["$unset"] = {"rejected_at": "", "rejection_reason": ""}
                elif data.to_stage == ApplicationStage.WITHDRAWN:
                    rollback["$unset"] = {"withdrawn_at": ""}
                await JobApplication.find_one(
                    {"_id": application.id, "revision": old_revision + 1, "current_stage": data.to_stage.value}
                ).update(rollback)
                raise

            updated = await RecruitmentRepository.get_application(application_id, company_id)
            await cache.complete_idempotency(
                "stage", company_id, idempotency_key, str(application.id), owner_token
            )
            await cache.delete_pattern(cache.cache_key("application-list", company_id, "*"))
            return updated
        except Exception:
            await cache.release_idempotency("stage", company_id, idempotency_key, owner_token)
            raise


class ScorecardService:
    @staticmethod
    async def create_version(
        data: ScorecardCreate,
        idempotency_key: str,
        current_user: CurrentUser,
    ) -> JobScorecard:
        await TenantAccessService.require_company_access(current_user, data.company_id)
        job, _ = await _load_job_company(data.job_requirement_id, data.company_id)
        existing_request = await JobScorecard.find_one(
            {"company_id": PydanticObjectId(data.company_id), "idempotency_key": idempotency_key}
        )
        if existing_request:
            request_criteria = [criterion.model_dump(mode="json") for criterion in data.criteria]
            stored_criteria = [criterion.model_dump(mode="json") for criterion in existing_request.criteria]
            if (
                str(existing_request.job_requirement_id) != data.job_requirement_id
                or existing_request.pass_threshold != data.pass_threshold
                or stored_criteria != request_criteria
            ):
                _raise_idempotency_mismatch()
            return existing_request

        owner_token = uuid.uuid4().hex
        if not await cache.reserve_idempotency(
            "scorecard", data.company_id, idempotency_key, owner_token
        ):
            raise CustomError(
                ErrorCodes.CONFLICT,
                "Scorecard request is already processing",
                status.HTTP_409_CONFLICT,
            )

        latest_items = await JobScorecard.find(
            JobScorecard.job_requirement_id == job.id
        ).sort("-version").limit(1).to_list()
        latest = latest_items[0] if latest_items else None
        version = (latest.version + 1) if latest else 1
        active = await RecruitmentRepository.get_active_scorecard(data.job_requirement_id, data.company_id)
        if active:
            active.is_active = False
            active.deactivated_at = now_utc()
            await active.save()

        scorecard = JobScorecard(
            company_id=PydanticObjectId(data.company_id),
            job_requirement_id=job.id,
            version=version,
            criteria=data.criteria,
            pass_threshold=data.pass_threshold,
            is_active=True,
            idempotency_key=idempotency_key,
            created_by=PydanticObjectId(current_user.user_id),
        )
        try:
            await scorecard.insert()
        except DuplicateKeyError as exc:
            duplicate = await JobScorecard.find_one(
                {
                    "company_id": PydanticObjectId(data.company_id),
                    "idempotency_key": idempotency_key,
                }
            )
            if duplicate:
                request_criteria = [criterion.model_dump(mode="json") for criterion in data.criteria]
                stored_criteria = [criterion.model_dump(mode="json") for criterion in duplicate.criteria]
                if (
                    str(duplicate.job_requirement_id) != data.job_requirement_id
                    or duplicate.pass_threshold != data.pass_threshold
                    or stored_criteria != request_criteria
                ):
                    await cache.release_idempotency(
                        "scorecard", data.company_id, idempotency_key, owner_token
                    )
                    _raise_idempotency_mismatch()
                await cache.complete_idempotency(
                    "scorecard", data.company_id, idempotency_key, str(duplicate.id), owner_token
                )
                return duplicate
            if active and not await RecruitmentRepository.get_active_scorecard(
                data.job_requirement_id, data.company_id
            ):
                active.is_active = True
                active.deactivated_at = None
                await active.save()
            await cache.release_idempotency(
                "scorecard", data.company_id, idempotency_key, owner_token
            )
            raise CustomError(
                ErrorCodes.CONFLICT,
                "Scorecard was changed concurrently; retry with a new idempotency key",
                status.HTTP_409_CONFLICT,
            ) from exc
        except Exception:
            if active and not await RecruitmentRepository.get_active_scorecard(
                data.job_requirement_id, data.company_id
            ):
                active.is_active = True
                active.deactivated_at = None
                await active.save()
            await cache.release_idempotency(
                "scorecard", data.company_id, idempotency_key, owner_token
            )
            raise

        job.active_scorecard_id = scorecard.id
        job.screening_threshold = scorecard.pass_threshold
        job.version += 1
        job.updated_by = PydanticObjectId(current_user.user_id)
        job.updated_at = now_utc()
        await job.save()
        await cache.delete_pattern(cache.cache_key("scorecard", data.company_id, data.job_requirement_id, "*"))
        await cache.complete_idempotency(
            "scorecard", data.company_id, idempotency_key, str(scorecard.id), owner_token
        )
        return scorecard

    @staticmethod
    async def get_active(job_requirement_id: str, company_id: str, current_user: CurrentUser) -> JobScorecard:
        await TenantAccessService.require_company_access(current_user, company_id)
        key = cache.cache_key("scorecard", company_id, job_requirement_id, "active")
        cached = await cache.get_json(key)
        if cached:
            try:
                return JobScorecard.model_validate(cached)
            except Exception:
                await cache.delete_keys(key)
        scorecard = await RecruitmentRepository.get_active_scorecard(job_requirement_id, company_id)
        if not scorecard:
            raise CustomError(ErrorCodes.NOT_FOUND, "Active scorecard not found", status.HTTP_404_NOT_FOUND)
        await cache.set_json(key, scorecard.model_dump(by_alias=True), settings.SCORECARD_CACHE_TTL)
        return scorecard


class ScreeningService:
    @staticmethod
    async def start(
        data: ScreeningStartRequest,
        idempotency_key: str,
        current_user: CurrentUser,
    ) -> ScreeningRun:
        await TenantAccessService.require_company_access(current_user, data.company_id)
        application = await RecruitmentRepository.get_application(data.application_id, data.company_id)
        if not application:
            raise CustomError(ErrorCodes.NOT_FOUND, "Application not found", status.HTTP_404_NOT_FOUND)
        scorecard = await RecruitmentRepository.get_active_scorecard(
            str(application.job_requirement_id), data.company_id
        )
        if not scorecard:
            raise CustomError(ErrorCodes.BAD_REQUEST, "Job has no active scorecard", status.HTTP_400_BAD_REQUEST)
        ai_model = await AIModel.find_one(
            {
                "_id": PydanticObjectId(data.ai_model_id),
                "model_type": {"$in": ["scoring", "skill_matcher"]},
                "is_active": True,
            }
        )
        if not ai_model:
            raise CustomError(ErrorCodes.NOT_FOUND, "AI model not found", status.HTTP_404_NOT_FOUND)
        resume = await ResumeFile.find_one(
            {"_id": application.resume_file_id, "company_id": PydanticObjectId(data.company_id), "is_deleted": False}
        )
        if not resume:
            raise CustomError(ErrorCodes.NOT_FOUND, "Resume not found", status.HTTP_404_NOT_FOUND)

        input_payload = {
            "application_id": str(application.id),
            "application_revision": application.revision,
            "resume_checksum": resume.checksum,
            "scorecard_id": str(scorecard.id),
            "scorecard_version": scorecard.version,
            "ai_model_id": str(ai_model.id),
            "ai_model_version": ai_model.version,
        }
        input_hash = hashlib.sha256(
            json.dumps(input_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

        existing_id = await cache.get_idempotency_result("screening", data.company_id, idempotency_key)
        if existing_id:
            existing = await RecruitmentRepository.get_screening_run(existing_id, data.company_id)
            if existing:
                if existing.input_hash != input_hash:
                    _raise_idempotency_mismatch()
                return existing
        existing = await ScreeningRun.find_one(
            {
                "company_id": PydanticObjectId(data.company_id),
                "$or": [
                    {"idempotency_key": idempotency_key},
                    {"input_hash": input_hash, "is_terminal": False},
                    {"application_id": application.id, "is_terminal": False},
                ],
            }
        )
        if existing:
            if existing.idempotency_key == idempotency_key and existing.input_hash != input_hash:
                _raise_idempotency_mismatch()
            return existing

        owner_token = uuid.uuid4().hex
        if not await cache.reserve_idempotency("screening", data.company_id, idempotency_key, owner_token):
            raise CustomError(ErrorCodes.CONFLICT, "Screening request is already processing", status.HTTP_409_CONFLICT)
        try:
            run = ScreeningRun(
                company_id=PydanticObjectId(data.company_id),
                application_id=application.id,
                resume_file_id=resume.id,
                job_requirement_id=application.job_requirement_id,
                scorecard_id=scorecard.id,
                ai_model_id=ai_model.id,
                triggered_by=PydanticObjectId(current_user.user_id),
                idempotency_key=idempotency_key,
                input_hash=input_hash,
                config_snapshot={
                    "input": input_payload,
                    "scorecard": scorecard.model_dump(mode="json"),
                    "model_config": ai_model.config,
                },
            )
            await run.insert()
            if application.current_stage == ApplicationStage.APPLIED:
                try:
                    await ApplicationService.transition_stage(
                        str(application.id),
                        data.company_id,
                        StageTransitionRequest(
                            to_stage=ApplicationStage.SCREENING,
                            reason="Screening queued",
                        ),
                        f"screen-start:{run.id}",
                        current_user,
                    )
                except Exception:
                    # The run is durable and the completion worker can reconcile
                    # the application stage; do not enqueue duplicate AI work.
                    logger.warning("Could not move application to screening stage", exc_info=True)
            message_id = await job_queue.enqueue("screening", str(run.id), data.company_id)
            if message_id:
                await ScreeningRun.find_one(
                    {"_id": run.id, "status": "queued"}
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now_utc(),
                            "updated_at": now_utc(),
                        }
                    }
                )
            await cache.complete_idempotency(
                "screening", data.company_id, idempotency_key, str(run.id), owner_token
            )
            return run
        except DuplicateKeyError:
            existing = await ScreeningRun.find_one(
                {
                    "company_id": PydanticObjectId(data.company_id),
                    "$or": [
                        {"idempotency_key": idempotency_key},
                        {"input_hash": input_hash, "is_terminal": False},
                        {"application_id": application.id, "is_terminal": False},
                    ],
                }
            )
            if existing:
                if existing.idempotency_key == idempotency_key and existing.input_hash != input_hash:
                    _raise_idempotency_mismatch()
                await cache.complete_idempotency(
                    "screening", data.company_id, idempotency_key, str(existing.id), owner_token
                )
                return existing
            raise
        except Exception:
            await cache.release_idempotency("screening", data.company_id, idempotency_key, owner_token)
            raise


class ReviewService:
    @staticmethod
    async def create(data: ApplicationReviewCreate, current_user: CurrentUser) -> ApplicationReview:
        await TenantAccessService.require_company_access(current_user, data.company_id)
        application = await RecruitmentRepository.get_application(data.application_id, data.company_id)
        if not application:
            raise CustomError(ErrorCodes.NOT_FOUND, "Application not found", status.HTTP_404_NOT_FOUND)
        review = ApplicationReview(
            company_id=PydanticObjectId(data.company_id),
            application_id=application.id,
            reviewer_id=PydanticObjectId(current_user.user_id),
            review_round=data.review_round,
            score=data.score,
            criteria_scores=data.criteria_scores,
            recommendation=data.recommendation,
            summary=data.summary,
        )
        try:
            await review.insert()
            return review
        except DuplicateKeyError as exc:
            raise CustomError(
                ErrorCodes.CONFLICT,
                "This reviewer already submitted a review for the round",
                status.HTTP_409_CONFLICT,
            ) from exc
