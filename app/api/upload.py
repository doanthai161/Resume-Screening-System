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
        data=ParseRunResponse(
            id=str(item.id),
            company_id=str(item.company_id),
            resume_file_id=str(item.resume_file_id),
            parser_model_id=str(item.parser_model_id) if item.parser_model_id else None,
            status=item.status,
            attempt=item.attempt,
            max_attempts=item.max_attempts,
            parser_version=item.parser_version,
            queued_at=item.queued_at,
        ),
        message="Resume parse queued",
    )
