from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile, status

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, require_permission
from app.schemas.response import ApiResponse
from app.schemas.resume import ParseResumeRequest, ParseRunResponse, ResumeResponse
from app.services.resume_service import ResumeService

router = APIRouter()


def _resume_response(item) -> ResumeResponse:
    return ResumeResponse(
        id=str(item.id),
        company_id=str(item.company_id),
        candidate_id=str(item.candidate_id),
        original_filename=item.original_filename,
        file_size=item.file_size,
        mime_type=item.mime_type,
        checksum=item.checksum,
        status=item.status,
        uploaded_at=item.uploaded_at,
        processed_at=item.processed_at,
    )


def _parse_run_response(item) -> ParseRunResponse:
    return ParseRunResponse(
        id=str(item.id),
        company_id=str(item.company_id),
        resume_file_id=str(item.resume_file_id),
        parser_model_id=str(item.parser_model_id) if item.parser_model_id else None,
        status=item.status,
        attempt=item.attempt,
        max_attempts=item.max_attempts,
        deferred_attempts=item.deferred_attempts,
        next_retry_at=item.next_retry_at,
        parser_version=item.parser_version,
        queued_at=item.queued_at,
        final_provider=item.final_provider,
        ocr_used=item.ocr_used,
        quality_score=item.quality_score,
        fallback_reason=item.fallback_reason,
        error_code=item.error_code,
        error_message=item.error_message,
    )


@router.post("", response_model=ApiResponse[ResumeResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload_resume(
    request: Request,
    company_id: str = Form(...),
    candidate_id: str = Form(...),
    company_branch_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_permission("resume_files:upload")),
):
    item = await ResumeService.upload(
        company_id, candidate_id, company_branch_id, file, current_user
    )
    return ApiResponse(success=True, data=_resume_response(item), message="Resume uploaded")


@router.post("/{resume_id}/parse-runs", response_model=ApiResponse[ParseRunResponse], status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.RATE_LIMIT_SCREENING)
async def start_parse_run(
    request: Request,
    resume_id: str,
    data: ParseResumeRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current_user: CurrentUser = Depends(require_permission("resume_files:parse")),
):
    item = await ResumeService.start_parse(resume_id, data, idempotency_key, current_user)
    return ApiResponse(
        success=True,
        data=_parse_run_response(item),
        message="Resume parse queued",
    )


@router.get(
    "/{resume_id}/parse-runs/{run_id}",
    response_model=ApiResponse[ParseRunResponse],
)
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_parse_run(
    request: Request,
    resume_id: str,
    run_id: str,
    current_user: CurrentUser = Depends(require_permission("resume_files:view")),
):
    item = await ResumeService.get_parse_run(resume_id, run_id, current_user)
    return ApiResponse(
        success=True,
        data=_parse_run_response(item),
        message="Resume parse run retrieved",
    )
