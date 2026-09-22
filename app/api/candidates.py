from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, require_permission
from app.schemas.candidate import (
    CandidateCreate,
    CandidateListResponse,
    CandidateResponse,
    CandidateUpdate,
)
from app.schemas.response import ApiResponse
from app.services.candidate_service import CandidateService

router = APIRouter()


def _response(candidate) -> CandidateResponse:
    return CandidateResponse(
        id=str(candidate.id),
        company_id=str(candidate.company_id),
        user_id=str(candidate.user_id) if candidate.user_id else None,
        full_name=candidate.full_name,
        email=candidate.email,
        phone_number=candidate.phone_number,
        location=candidate.location,
        source=candidate.source,
        status=candidate.status,
        tags=candidate.tags,
        metadata=candidate.metadata,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


@router.post("", response_model=ApiResponse[CandidateResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def create_candidate(
    request: Request,
    data: CandidateCreate,
    current_user: CurrentUser = Depends(require_permission("candidates:create")),
):
    candidate = await CandidateService.create(data, current_user)
    return ApiResponse(success=True, data=_response(candidate), message="Candidate created")


@router.get("", response_model=ApiResponse[CandidateListResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_candidates(
    request: Request,
    company_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=500),
    search: Optional[str] = Query(None, max_length=100),
    current_user: CurrentUser = Depends(require_permission("candidates:list")),
):
    items, total = await CandidateService.list(company_id, page, size, search, current_user)
    return ApiResponse(
        success=True,
        data=CandidateListResponse(items=[_response(item) for item in items], total=total, page=page, size=size),
    )


@router.get("/{candidate_id}", response_model=ApiResponse[CandidateResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_candidate(
    request: Request,
    candidate_id: str,
    company_id: str,
    current_user: CurrentUser = Depends(require_permission("candidates:view")),
):
    candidate = await CandidateService.get(candidate_id, company_id, current_user)
    return ApiResponse(success=True, data=_response(candidate))


@router.patch("/{candidate_id}", response_model=ApiResponse[CandidateResponse])
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def update_candidate(
    request: Request,
    candidate_id: str,
    company_id: str,
    data: CandidateUpdate,
    current_user: CurrentUser = Depends(require_permission("candidates:edit")),
):
    candidate = await CandidateService.update(candidate_id, company_id, data, current_user)
    return ApiResponse(success=True, data=_response(candidate), message="Candidate updated")
