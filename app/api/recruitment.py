from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request, status

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, require_permission
from app.models.job_application import ApplicationStage
from app.repositories.recruitment_repository import RecruitmentRepository
from app.schemas.recruitment import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationReviewCreate,
    ApplicationReviewResponse,
    ScorecardCreate,
    ScorecardResponse,
    ScreeningRunResponse,
    ScreeningStartRequest,
    StageTransitionRequest,
)
from app.schemas.response import ApiResponse
from app.services.recruitment_service import (
    ApplicationService,
    ReviewService,
    ScorecardService,
    ScreeningService,
)
from app.services.tenant_access_service import TenantAccessService

router = APIRouter()


def _application_response(item) -> ApplicationResponse:
    return ApplicationResponse(
        id=str(item.id),
        company_id=str(item.company_id),
        company_branch_id=str(item.company_branch_id),
        candidate_id=str(item.candidate_id),
        resume_file_id=str(item.resume_file_id),
        job_requirement_id=str(item.job_requirement_id),
        applicant_id=str(item.applicant_id) if item.applicant_id else None,
        applied_by=str(item.applied_by),
        current_stage=item.current_stage,
        status=item.status,
        source=item.source,
        assigned_recruiter_id=str(item.assigned_recruiter_id) if item.assigned_recruiter_id else None,
        latest_screening_result_id=(
            str(item.latest_screening_result_id) if item.latest_screening_result_id else None
        ),
        applied_at=item.applied_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _scorecard_response(item) -> ScorecardResponse:
    return ScorecardResponse(
        id=str(item.id),
        company_id=str(item.company_id),
        job_requirement_id=str(item.job_requirement_id),
        version=item.version,
        criteria=item.criteria,
        pass_threshold=item.pass_threshold,
        is_active=item.is_active,
        created_by=str(item.created_by),
        created_at=item.created_at,
    )


def _run_response(item) -> ScreeningRunResponse:
    return ScreeningRunResponse(
        id=str(item.id),
        company_id=str(item.company_id),
        application_id=str(item.application_id),
        resume_file_id=str(item.resume_file_id),
        job_requirement_id=str(item.job_requirement_id),
        scorecard_id=str(item.scorecard_id),
        ai_model_id=str(item.ai_model_id),
        status=item.status,
        attempt=item.attempt,
        max_attempts=item.max_attempts,
        queued_at=item.queued_at,
        started_at=item.started_at,
        finished_at=item.finished_at,
    )


def _review_response(item) -> ApplicationReviewResponse:
    return ApplicationReviewResponse(
        id=str(item.id),
        company_id=str(item.company_id),
        application_id=str(item.application_id),
        reviewer_id=str(item.reviewer_id),
        review_round=item.review_round,
        score=item.score,
        criteria_scores=item.criteria_scores,
        recommendation=item.recommendation,
        summary=item.summary,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.post("/applications", response_model=ApiResponse[ApplicationResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def create_application(
    request: Request,
    data: ApplicationCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current_user: CurrentUser = Depends(require_permission("job_applications:create")),
):
    application = await ApplicationService.create(data, idempotency_key, current_user)
    return ApiResponse(success=True, data=_application_response(application), message="Application accepted")


@router.get("/applications", response_model=ApiResponse[ApplicationListResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_applications(
    request: Request,
    company_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=500),
    job_requirement_id: Optional[str] = None,
    stage: Optional[ApplicationStage] = None,
    current_user: CurrentUser = Depends(require_permission("job_applications:list")),
):
    items, total = await ApplicationService.list(
        company_id, page, size, current_user, job_requirement_id, stage
    )
    return ApiResponse(
        success=True,
        data=ApplicationListResponse(
            items=[_application_response(item) for item in items],
            total=total,
            page=page,
            size=size,
        ),
    )


@router.get("/applications/{application_id}", response_model=ApiResponse[ApplicationResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_application(
    request: Request,
    application_id: str,
    company_id: str,
    current_user: CurrentUser = Depends(require_permission("job_applications:view")),
):
    item = await ApplicationService.get(application_id, company_id, current_user)
    return ApiResponse(success=True, data=_application_response(item))


@router.post("/applications/{application_id}/stage", response_model=ApiResponse[ApplicationResponse])
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def transition_application_stage(
    request: Request,
    application_id: str,
    company_id: str,
    data: StageTransitionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current_user: CurrentUser = Depends(require_permission("application_stage_events:create")),
):
    item = await ApplicationService.transition_stage(
        application_id, company_id, data, idempotency_key, current_user
    )
    return ApiResponse(success=True, data=_application_response(item), message="Application stage updated")


@router.post("/scorecards", response_model=ApiResponse[ScorecardResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def create_scorecard_version(
    request: Request,
    data: ScorecardCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current_user: CurrentUser = Depends(require_permission("job_scorecards:create")),
):
    item = await ScorecardService.create_version(data, idempotency_key, current_user)
    return ApiResponse(success=True, data=_scorecard_response(item), message="Scorecard version activated")


@router.get("/scorecards/active", response_model=ApiResponse[ScorecardResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_active_scorecard(
    request: Request,
    company_id: str,
    job_requirement_id: str,
    current_user: CurrentUser = Depends(require_permission("job_scorecards:view")),
):
    item = await ScorecardService.get_active(job_requirement_id, company_id, current_user)
    return ApiResponse(success=True, data=_scorecard_response(item))


@router.post("/screenings", response_model=ApiResponse[ScreeningRunResponse], status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.RATE_LIMIT_SCREENING)
async def start_screening(
    request: Request,
    data: ScreeningStartRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current_user: CurrentUser = Depends(require_permission("screening_runs:create")),
):
    item = await ScreeningService.start(data, idempotency_key, current_user)
    return ApiResponse(success=True, data=_run_response(item), message="Screening queued")


@router.get("/screenings/{run_id}", response_model=ApiResponse[ScreeningRunResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_screening_run(
    request: Request,
    run_id: str,
    company_id: str,
    current_user: CurrentUser = Depends(require_permission("screening_runs:view")),
):
    await TenantAccessService.require_company_access(current_user, company_id)
    item = await RecruitmentRepository.get_screening_run(run_id, company_id)
    if not item:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Screening run not found")
    return ApiResponse(success=True, data=_run_response(item))


@router.post("/reviews", response_model=ApiResponse[ApplicationReviewResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def create_review(
    request: Request,
    data: ApplicationReviewCreate,
    current_user: CurrentUser = Depends(require_permission("application_reviews:create")),
):
    item = await ReviewService.create(data, current_user)
    return ApiResponse(success=True, data=_review_response(item), message="Review submitted")


@router.get("/applications/{application_id}/reviews", response_model=ApiResponse[list[ApplicationReviewResponse]])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_reviews(
    request: Request,
    application_id: str,
    company_id: str,
    current_user: CurrentUser = Depends(require_permission("application_reviews:list")),
):
    await TenantAccessService.require_company_access(current_user, company_id)
    items = await RecruitmentRepository.list_reviews(application_id, company_id)
    return ApiResponse(success=True, data=[_review_response(item) for item in items])
