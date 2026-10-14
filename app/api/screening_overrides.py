"""HR assessment endpoints; AI results and prior overrides remain immutable."""
from fastapi import APIRouter, Depends, Header, Query, Request, status

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, require_permission
from app.schemas.response import ApiResponse
from app.schemas.screening_override import (
    ScreeningOverrideCreate, ScreeningOverrideResponse, ScreeningOverrideHistoryResponse, ScreeningAssessmentResponse,
)
from app.services.screening_override_service import ScreeningOverrideService, override_response

router = APIRouter()


@router.post("/screenings/{run_id}/overrides", response_model=ApiResponse[ScreeningOverrideResponse],
             status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def create_screening_override(
    request: Request, run_id: str, data: ScreeningOverrideCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current_user: CurrentUser = Depends(require_permission("screening_results:override")),
):
    item = await ScreeningOverrideService.create(run_id, data, idempotency_key, current_user)
    return ApiResponse(success=True, data=override_response(item), message="HR screening assessment recorded")


@router.get("/screenings/{run_id}/overrides", response_model=ApiResponse[ScreeningOverrideHistoryResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_screening_overrides(
    request: Request, run_id: str, company_id: str,
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(require_permission("screening_runs:view")),
):
    items = await ScreeningOverrideService.history(run_id, company_id, page, size, current_user)
    return ApiResponse(success=True, data=items)


@router.get("/screenings/{run_id}/assessment", response_model=ApiResponse[ScreeningAssessmentResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_screening_assessment(
    request: Request, run_id: str, company_id: str,
    current_user: CurrentUser = Depends(require_permission("screening_runs:view")),
):
    item = await ScreeningOverrideService.assessment(run_id, company_id, current_user)
    return ApiResponse(success=True, data=item)
