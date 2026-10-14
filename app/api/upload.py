from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
import anyio
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from urllib.parse import quote

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, require_permission
from app.schemas.response import ApiResponse
from app.schemas.resume import ParseResumeRequest, ParseRunResponse, ResumeListResponse, ResumeResponse
from app.services.resume_service import ResumeService
from app.core.errors import CustomError, ErrorCodes
from app.services.extraction_service import ExtractionService
from app.schemas.extraction import StartExtractionRequest, ExtractionRunResponse, ProfileResponse

router = APIRouter()
_download_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="resume-download")
_download_slots = threading.BoundedSemaphore(4)


async def _open_download_managed(item):
    # Reserve before submit: both queued and native work are bounded. Cancellation
    # never releases the permit while the OS read/hash is still running.
    slots = _download_slots
    if not slots.acquire(blocking=False):
        raise CustomError(ErrorCodes.RATE_LIMIT, "Resume download is busy", 429)
    lock = threading.Lock()
    state = {"abandoned": False, "handle": None}

    def native_open():
        handle = ResumeService._open_download(item)
        with lock:
            if state["abandoned"]:
                handle.close()
                return None
            state["handle"] = handle
        return handle

    try:
        future = _download_executor.submit(native_open)
    except BaseException:
        slots.release()
        raise
    future.add_done_callback(lambda _: slots.release())
    try:
        return await asyncio.wrap_future(future)
    except BaseException:
        with lock:
            state["abandoned"] = True
            handle = state["handle"]
            state["handle"] = None
        if handle is not None:
            handle.close()
        future.cancel()  # Cancels queued work; running work closes its late handle.
        raise


def _extraction_run_response(item) -> ExtractionRunResponse:
    return ExtractionRunResponse(
        id=str(item.id), resume_file_id=str(item.resume_file_id), status=item.status,
        attempt=item.attempt, max_attempts=item.max_attempts,
        source_parse_revision=item.source_parse_revision, model_name=item.model_name,
        prompt_version=item.prompt_version, queued_at=item.queued_at, next_retry_at=item.next_retry_at,
        result_id=str(item.result_id) if item.result_id else None,
        error_code=item.error_code, error_message=item.error_message,
    )


def _profile_response(item, is_current: bool) -> ProfileResponse:
    return ProfileResponse(
        id=str(item.id), extraction_run_id=str(item.extraction_run_id), resume_file_id=str(item.resume_file_id),
        version=item.version, source_parse_revision=item.source_parse_revision, model_name=item.model_name,
        prompt_version=item.prompt_version, extractor_version=item.extractor_version,
        review_required=item.review_required, is_current=is_current, created_at=item.created_at, profile=item.profile,
        origin=item.origin,
        parent_profile_id=str(item.parent_profile_id) if item.parent_profile_id else None,
        edited_by=str(item.edited_by) if item.edited_by else None,
        edit_reason=item.edit_reason, hr_annotations=item.hr_annotations,
    )


@router.post("/{resume_id}/extraction-runs", response_model=ApiResponse[ExtractionRunResponse], status_code=202)
@limiter.limit(settings.RATE_LIMIT_SCREENING)
async def start_extraction_run(
    request: Request, resume_id: str, data: StartExtractionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current_user: CurrentUser = Depends(require_permission("resume_files:extract")),
):
    run = await ExtractionService.start(resume_id, data.company_id, idempotency_key, current_user)
    return ApiResponse(success=True, data=_extraction_run_response(run), message="Resume extraction queued")


@router.get("/{resume_id}/extraction-runs/{run_id}", response_model=ApiResponse[ExtractionRunResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_extraction_run(
    request: Request, resume_id: str, run_id: str,
    current_user: CurrentUser = Depends(require_permission("resume_files:view")),
):
    run = await ExtractionService.get_run(resume_id, run_id, current_user)
    return ApiResponse(success=True, data=_extraction_run_response(run), message="Extraction run retrieved")


@router.get("/{resume_id}/profiles/latest", response_model=ApiResponse[ProfileResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_latest_profile(
    request: Request, resume_id: str,
    current_user: CurrentUser = Depends(require_permission("resume_profiles:view")),
):
    item, current = await ExtractionService.get_profile(resume_id, current_user)
    return ApiResponse(success=True, data=_profile_response(item, current), message="Resume profile retrieved")


@router.get("/{resume_id}/profiles/{profile_id}", response_model=ApiResponse[ProfileResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_profile_version(
    request: Request, resume_id: str, profile_id: str,
    current_user: CurrentUser = Depends(require_permission("resume_profiles:view")),
):
    item, current = await ExtractionService.get_profile(resume_id, current_user, profile_id)
    return ApiResponse(success=True, data=_profile_response(item, current), message="Resume profile version retrieved")


def _resume_response(item) -> ResumeResponse:
    return ResumeResponse(
        id=str(item.id),
        company_id=str(item.company_id),
        candidate_id=str(item.candidate_id) if item.candidate_id else None,
        company_branch_id=str(item.company_branch_id) if item.company_branch_id else None,
        original_filename=item.original_filename,
        file_size=item.file_size,
        mime_type=item.mime_type,
        checksum=item.checksum,
        status=item.status,
        uploaded_at=item.uploaded_at,
        processed_at=item.processed_at,
        parse_revision=item.parse_revision,
        latest_parse_run_id=str(item.latest_parse_run_id) if item.latest_parse_run_id else None,
        latest_extraction_profile_id=str(item.latest_extraction_profile_id) if item.latest_extraction_profile_id else None,
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


@router.get("", response_model=ApiResponse[ResumeListResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_resumes(
    request: Request, company_id: str, candidate_id: Optional[str] = None,
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(require_permission("resume_files:view")),
):
    data = await ResumeService.list_resumes(company_id, current_user, page=page, size=size, candidate_id=candidate_id)
    return ApiResponse(success=True, data=data, message="Resumes retrieved")


@router.get("/{resume_id}", response_model=ApiResponse[ResumeResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_resume(
    request: Request, resume_id: str,
    current_user: CurrentUser = Depends(require_permission("resume_files:view")),
):
    item = await ResumeService.authorized_resume(resume_id, current_user)
    return ApiResponse(success=True, data=_resume_response(item), message="Resume retrieved")


class _ResumeDownloadResponse(StreamingResponse):
    def __init__(self, handle, item):
        self.handle = handle
        filename = Path(item.original_filename.replace("\\", "/")).name
        filename = "".join(character for character in filename if ord(character) >= 32 and ord(character) != 127) or "resume"

        async def content():
            remaining = item.file_size
            while remaining:
                chunk = await anyio.to_thread.run_sync(handle.read, min(64 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("Resume file changed during download")
                remaining -= len(chunk)
                yield chunk

        media_type = item.mime_type if item.mime_type in settings.allowed_resume_mime_types else "application/octet-stream"
        super().__init__(content(), media_type=media_type, headers={
            "Content-Disposition": "attachment; filename*=utf-8''" + quote(filename, safe=""),
            "Content-Length": str(item.file_size), "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also closes when disconnected before the generator's first read.
            # anyio keeps native reads joined before releasing the file handle.
            self.handle.close()


@router.get("/{resume_id}/download", response_class=StreamingResponse)
@limiter.limit(settings.RATE_LIMIT_READ)
async def download_resume(
    request: Request, resume_id: str,
    current_user: CurrentUser = Depends(require_permission("resume_files:view")),
):
    item = await ResumeService.authorized_resume(resume_id, current_user)
    # File hashing/opening happen off the event loop and outside a DB transaction.
    handle = await _open_download_managed(item)
    try:
        return _ResumeDownloadResponse(handle, item)
    except BaseException:
        handle.close()
        raise


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
