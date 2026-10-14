from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, require_permission
from app.schemas.extraction import ProfileResponse
from app.schemas.response import ApiResponse
from app.schemas.resume_profile_review import ProfileRevisionCreate
from app.services.resume_profile_review_service import ResumeProfileReviewService

router = APIRouter()


class ProfileHistoryResponse(BaseModel):
    items: list[ProfileResponse]
    total: int
    page: int
    size: int


def profile_response(item, is_current: bool) -> ProfileResponse:
    return ProfileResponse(
        id=str(item.id), extraction_run_id=str(item.extraction_run_id), resume_file_id=str(item.resume_file_id),
        version=item.version, source_parse_revision=item.source_parse_revision, model_name=item.model_name,
        prompt_version=item.prompt_version, extractor_version=item.extractor_version,
        review_required=item.review_required, is_current=is_current, created_at=item.created_at,
        profile=item.profile, origin=item.origin,
        parent_profile_id=str(item.parent_profile_id) if item.parent_profile_id else None,
        edited_by=str(item.edited_by) if item.edited_by else None,
        edit_reason=item.edit_reason, hr_annotations=item.hr_annotations,
    )


@router.post("/{resume_id}/profile-revisions", response_model=ApiResponse[ProfileResponse], status_code=201)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def revise_profile(
    request: Request, resume_id: str, data: ProfileRevisionCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    caller: CurrentUser = Depends(require_permission("resume_profiles:edit")),
):
    item, current = await ResumeProfileReviewService.revise(resume_id, data, idempotency_key, caller)
    return ApiResponse.ok(profile_response(item, current), "HR profile revision saved")


@router.get("/{resume_id}/profiles", response_model=ApiResponse[ProfileHistoryResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_profile_history(
    request: Request, resume_id: str, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    caller: CurrentUser = Depends(require_permission("resume_profiles:view")),
):
    resume, items, total = await ResumeProfileReviewService.history(resume_id, caller, page, size)
    return ApiResponse.ok(ProfileHistoryResponse(items=[profile_response(item, resume.status == "parsed" and resume.latest_extraction_profile_id == item.id and resume.parse_revision == item.source_parse_revision) for item in items], total=total, page=page, size=size))
