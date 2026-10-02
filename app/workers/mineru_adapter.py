"""MinerU 4 V1 HTTP adapter for the resume-parse queue.

The service converts a document into Markdown. Structured CV extraction is a
separate step: no personal details or assessment are inferred here.
"""

import asyncio
import json
import unicodedata
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.models.resume_file import ParsedResumeData
from app.models.screening_run import ResumeParseRun
from app.schemas.worker import ParseOutput
from app.workers.document import (
    SUPPORTED_MIME_TYPES,
    MAX_EXTRACTED_CHARACTERS,
    DocumentAnalysis,
    ResumeDocument,
    analyze_document,
    load_resume_document,
    pdf_ocr_mode,
)
from app.workers.errors import (
    PermanentParseError,
    ProviderResponseError,
    TransientParseError,
)
from app.workers.quality import _visible_text, evaluate_text_quality

_MAX_MARKDOWN_BYTES = 1_000_000
_MAX_JSON_BYTES = 1_000_000


def _restore_docx_margins(text: str, analysis: DocumentAnalysis) -> tuple[str, bool]:
    """Keep actual DOCX header/footer text that the provider omitted."""
    def normalized(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    present = normalized(_visible_text(text))
    restored = []
    for margin in (analysis.header_text, analysis.footer_text):
        missing = []
        for line in margin.splitlines():
            line = line.strip()
            key = normalized(line)
            if key and key not in present:
                missing.append(line)
                present += " " + key
        restored.append("\n".join(missing))
    if not any(restored):
        return text, False
    combined = "\n\n".join(part for part in (restored[0], text, restored[1]) if part)
    if len(combined) > MAX_EXTRACTED_CHARACTERS:
        raise ProviderResponseError(
            "DOCX text including margins exceeds the resume text limit",
            code="mineru_markdown_too_large",
        )
    return combined, True


def _required_string(data: dict, name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value or len(value) > 256 or value in {".", ".."}:
        raise ProviderResponseError(
            "MinerU returned an incomplete response",
            code="mineru_invalid_response",
        )
    return value


class MinerUAdapter:
    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")
        base = httpx.URL(self.base_url)
        if (base.scheme not in {"http", "https"} or not base.host or base.path != "/"
                or base.userinfo or base.query or base.fragment):
            raise ValueError("MINERU_API_URL must be an http(s) service root without path or credentials")
        self.origin = (base.scheme, base.host, base.port)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v1/{path}"

    def _upload_url(self, url: str) -> str:
        # The worker deliberately supports the self-hosted, same-origin upload
        # flow only. A service response cannot redirect CV bytes to another host.
        target = httpx.URL(self.base_url).join(url)
        if (target.scheme, target.host, target.port) != self.origin or target.userinfo:
            raise PermanentParseError(
                "MinerU upload URL must be same-origin",
                code="mineru_unsafe_upload_url",
            )
        return str(target)

    @staticmethod
    def _raise_http_error(exc: httpx.HTTPStatusError, *, known_resource: bool = False) -> None:
        status_code = exc.response.status_code
        if status_code == 404 and known_resource:
            # Resource IDs are process-local in MinerU. Let the durable worker
            # retry the full upload/job cycle, bounded by the run retry budget.
            raise TransientParseError(
                "MinerU lost a previously created resource",
                code="mineru_resource_lost",
            ) from exc
        if status_code in {408, 425, 429} or status_code >= 500:
            raise TransientParseError(
                "MinerU is temporarily unavailable",
                code=f"mineru_http_{status_code}",
            ) from exc
        raise PermanentParseError(
            "MinerU rejected the parse request",
            code=f"mineru_http_{status_code}",
        ) from exc

    async def _json(self, method: str, url: str, *, known_resource: bool = False, **kwargs) -> dict:
        try:
            async with self.client.stream(method, url, **kwargs) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_JSON_BYTES:
                        raise ProviderResponseError(
                            "MinerU JSON response is too large",
                            code="mineru_response_too_large",
                        )
        except httpx.HTTPStatusError as exc:
            self._raise_http_error(exc, known_resource=known_resource)
        except httpx.TransportError as exc:
            raise TransientParseError(
                "MinerU request failed",
                code="mineru_connection_failed",
            ) from exc
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError(
                "MinerU returned invalid JSON",
                code="mineru_invalid_json",
            ) from exc
        if not isinstance(data, dict):
            raise ProviderResponseError(
                "MinerU returned invalid JSON",
                code="mineru_invalid_json",
            )
        return data

    async def _put(self, url: str, content: bytes, headers: dict[str, str]) -> None:
        try:
            # No output body is needed for PUT. Do not buffer a potentially
            # unbounded server body just to confirm the status code.
            async with self.client.stream("PUT", url, content=content, headers=headers) as response:
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._raise_http_error(exc, known_resource=True)
        except httpx.TransportError as exc:
            raise TransientParseError(
                "MinerU upload failed",
                code="mineru_upload_failed",
            ) from exc

    async def _markdown(self, file_id: str) -> str:
        try:
            async with self.client.stream(
                "GET", self._url(f"files/{quote(file_id, safe='')}/content")
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_MARKDOWN_BYTES:
                        raise ProviderResponseError(
                            "MinerU Markdown exceeds the resume text limit",
                            code="mineru_markdown_too_large",
                        )
        except httpx.HTTPStatusError as exc:
            self._raise_http_error(exc, known_resource=True)
        except httpx.TransportError as exc:
            raise TransientParseError(
                "MinerU result download failed",
                code="mineru_download_failed",
            ) from exc
        try:
            text = data.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise ProviderResponseError(
                "MinerU Markdown is not valid UTF-8",
                code="mineru_invalid_markdown",
            ) from exc
        if not text:
            raise ProviderResponseError(
                "MinerU returned empty Markdown",
                code="mineru_empty_markdown",
            )
        return text

    async def parse_document(
        self,
        document: ResumeDocument,
        analysis: DocumentAnalysis,
        *,
        tier: str,
        ocr_mode: str | None,
        fallback_reason: str | None = None,
    ) -> ParseOutput:
        content = document.content
        extension = document.extension
        upload = await self._json("POST", self._url("uploads"), json={
            "filename": document.filename,
            "bytes": len(content),
            "mime_type": SUPPORTED_MIME_TYPES[extension],
            "purpose": "parse",
            "sha256sum": document.resume.checksum,
        })
        status = _required_string(upload, "status")
        upload_id = _required_string(upload, "id")
        if status == "pending":
            upload_url = self._upload_url(_required_string(upload, "upload_url"))
            if upload.get("upload_method", "PUT") != "PUT":
                raise ProviderResponseError(
                    "MinerU returned an unsupported upload method",
                    code="mineru_invalid_upload_method",
                )
            headers = upload.get("upload_headers") or {}
            if not isinstance(headers, dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                or k.lower() in {"authorization", "host", "cookie", "content-length", "transfer-encoding", "connection", "proxy-authorization"}
                or "\r" in k + v or "\n" in k + v for k, v in headers.items()
            ):
                raise ProviderResponseError(
                    "MinerU returned invalid upload headers",
                    code="mineru_invalid_upload_headers",
                )
            await self._put(upload_url, content, headers)
            upload = await self._json(
                "POST", self._url(f"uploads/{quote(upload_id, safe='')}/complete"),
                known_resource=True,
            )
            if upload.get("status") != "completed":
                raise ProviderResponseError(
                    "MinerU upload did not complete",
                    code="mineru_upload_incomplete",
                )
        elif status != "completed":
            raise ProviderResponseError(
                "MinerU returned an unsupported upload status",
                code="mineru_invalid_upload_status",
            )
        file_data = upload.get("file")
        if not isinstance(file_data, dict):
            raise ProviderResponseError(
                "MinerU upload has no file",
                code="mineru_file_missing",
            )
        file_id = _required_string(file_data, "id")
        request_data = {
            "files": [{"source": {"type": "file_id", "file_id": file_id}}],
            "tier": tier,
            "output_formats": ["markdown"],
        }
        if extension == ".pdf" and ocr_mode:
            request_data["ocr_mode"] = ocr_mode
        job = await self._json("POST", self._url("parse/jobs"), json=request_data)
        job_id = _required_string(job, "job_id")
        polls = 0
        while True:
            job_status = _required_string(job, "status")
            if job_status == "completed":
                break
            if job_status in {"failed", "partial", "canceled"}:
                raise PermanentParseError(
                    "MinerU rejected the document",
                    code="mineru_job_failed",
                )
            if job_status not in {"queued", "running"}:
                raise ProviderResponseError("MinerU returned an unknown job status", code="mineru_invalid_status")
            if polls >= settings.MINERU_MAX_POLLS:
                raise TransientParseError(
                    "MinerU polling budget was exhausted",
                    code="mineru_poll_timeout",
                )
            await asyncio.sleep(settings.MINERU_POLL_INTERVAL_SECONDS)
            job = await self._json(
                "GET", self._url(f"parse/jobs/{quote(job_id, safe='')}"),
                known_resource=True,
            )
            polls += 1
        files = job.get("files")
        if (not isinstance(files, list) or len(files) != 1
                or not isinstance(files[0], dict) or files[0].get("status") != "completed"):
            raise ProviderResponseError(
                "MinerU parse result is incomplete",
                code="mineru_result_incomplete",
            )
        outputs = files[0].get("output_files")
        markdown = outputs.get("markdown") if isinstance(outputs, dict) else None
        if not isinstance(markdown, dict):
            raise ProviderResponseError(
                "MinerU Markdown artifact is missing",
                code="mineru_artifact_missing",
            )
        text = await self._markdown(_required_string(markdown, "file_id"))
        margins_restored = False
        if extension == ".docx":
            text, margins_restored = _restore_docx_margins(text, analysis)
        quality = evaluate_text_quality(text, analysis.page_count)
        mode_name = ocr_mode or "native"
        parser_version = f"mineru-v1-{tier}-{mode_name}"
        if margins_restored:
            parser_version += "+docx-margins"
        return ParseOutput(
            parsed_data=ParsedResumeData(
                raw_text=text,
                parser_version=parser_version,
                confidence_score=quality.score,
            ),
            provider="mineru",
            ocr_used=ocr_mode == "ocr",
            quality_score=quality.score,
            fallback_reason=fallback_reason,
        )

    async def parse(self, run: ResumeParseRun) -> ParseOutput:
        document = await load_resume_document(run)
        analysis = await analyze_document(document)
        tier = settings.MINERU_PDF_TIER if document.extension == ".pdf" else "flash"
        ocr_mode = None
        if document.extension == ".pdf":
            ocr_mode = pdf_ocr_mode(analysis)
        return await self.parse_document(
            document,
            analysis,
            tier=tier,
            ocr_mode=ocr_mode,
        )

    async def aclose(self) -> None:
        await self.client.aclose()


def create_mineru_adapter() -> MinerUAdapter:
    if not settings.MINERU_API_URL:
        raise ValueError("MINERU_API_URL is required for the MinerU parse adapter")
    base_url = settings.MINERU_API_URL.rstrip("/")
    # No automatic redirects: do not forward credentials or CV bytes to a
    # location returned by the remote service.
    headers = {}
    if settings.MINERU_API_KEY:
        headers["Authorization"] = f"Bearer {settings.MINERU_API_KEY.get_secret_value()}"
    client = httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(settings.MINERU_REQUEST_TIMEOUT_SECONDS, connect=10),
        follow_redirects=False,
    )
    return MinerUAdapter(client, base_url)


def create_adapter():
    """Create the resilient parse pipeline used by the resume-parse worker."""
    from app.workers.parse_pipeline import ParsePipelineAdapter

    return ParsePipelineAdapter(create_mineru_adapter())
