"""MinerU 4 V1 HTTP adapter for the resume-parse queue.

The service converts a document into Markdown. Structured CV extraction is a
separate step: no personal details or assessment are inferred here.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.models.resume_file import ParsedResumeData, ResumeFile
from app.models.screening_run import ResumeParseRun
from app.schemas.worker import ParseOutput

_MAX_MARKDOWN_BYTES = 1_000_000
_MAX_JSON_BYTES = 1_000_000
_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _required_string(data: dict, name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"MinerU response missing {name}")
    return value


def _read_local_resume(resume: ResumeFile) -> bytes:
    if resume.storage_provider != "local":
        raise ValueError("MinerU adapter supports local resume storage only")
    root = settings.resume_upload_path.resolve()
    path = Path(resume.file_path).resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() not in _MIME:
        raise ValueError("Resume path is outside the upload directory or has unsupported format")
    if resume.mime_type != _MIME[path.suffix.lower()]:
        raise ValueError("Resume MIME type does not match the file extension")
    if not 0 < resume.file_size <= settings.MAX_RESUME_SIZE:
        raise ValueError("Resume size is invalid")
    with path.open("rb") as handle:
        content = handle.read(settings.MAX_RESUME_SIZE + 1)
    if len(content) != resume.file_size or hashlib.sha256(content).hexdigest() != resume.checksum:
        raise ValueError("Resume content does not match stored metadata")
    return content


class MinerUAdapter:
    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")
        base = httpx.URL(self.base_url)
        if base.scheme not in {"http", "https"} or not base.host or base.path != "/" or base.userinfo:
            raise ValueError("MINERU_API_URL must be an http(s) service root without path or credentials")
        self.origin = (base.scheme, base.host, base.port)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/v1/{path}"

    def _upload_url(self, url: str) -> str:
        # The worker deliberately supports the self-hosted, same-origin upload
        # flow only. A service response cannot redirect CV bytes to another host.
        target = httpx.URL(self.base_url).join(url)
        if (target.scheme, target.host, target.port) != self.origin or target.userinfo:
            raise ValueError("MinerU upload URL must be same-origin")
        return str(target)

    async def _json(self, method: str, url: str, **kwargs) -> dict:
        async with self.client.stream(method, url, **kwargs) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_JSON_BYTES:
                    raise ValueError("MinerU JSON response is too large")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("MinerU returned invalid JSON")
        return data

    async def _markdown(self, file_id: str) -> str:
        async with self.client.stream("GET", self._url(f"files/{quote(file_id, safe='')}/content")) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > _MAX_MARKDOWN_BYTES:
                    raise ValueError("MinerU Markdown exceeds resume text limit")
        text = data.decode("utf-8", errors="strict").strip()
        if not text:
            raise ValueError("MinerU returned empty Markdown")
        return text

    async def parse(self, run: ResumeParseRun) -> ParseOutput:
        resume = await ResumeFile.find_one({
            "_id": run.resume_file_id, "company_id": run.company_id, "is_deleted": False,
        })
        if resume is None:
            raise ValueError("Resume is missing or does not belong to this company")
        content = await asyncio.to_thread(_read_local_resume, resume)
        extension = Path(resume.file_path).suffix.lower()
        filename = Path(resume.file_path).name
        upload = await self._json("POST", self._url("uploads"), json={
            "filename": filename,
            "bytes": len(content),
            "mime_type": _MIME[extension],
            "purpose": "parse",
            "sha256sum": resume.checksum,
        })
        status = _required_string(upload, "status")
        upload_id = _required_string(upload, "id")
        if status == "pending":
            upload_url = self._upload_url(_required_string(upload, "upload_url"))
            if upload.get("upload_method", "PUT") != "PUT":
                raise ValueError("Unsupported MinerU upload method")
            headers = upload.get("upload_headers") or {}
            if not isinstance(headers, dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                or k.lower() in {"authorization", "host", "cookie"}
                or "\r" in k + v or "\n" in k + v for k, v in headers.items()
            ):
                raise ValueError("Invalid MinerU upload headers")
            response = await self.client.put(upload_url, content=content, headers=headers)
            response.raise_for_status()
            upload = await self._json("POST", self._url(f"uploads/{quote(upload_id, safe='')}/complete"))
            if upload.get("status") != "completed":
                raise ValueError("MinerU upload did not complete")
        elif status != "completed":
            raise ValueError("Unsupported MinerU upload status")
        file_data = upload.get("file")
        if not isinstance(file_data, dict):
            raise ValueError("MinerU upload has no file")
        file_id = _required_string(file_data, "id")
        tier = settings.MINERU_PDF_TIER if extension == ".pdf" else "flash"
        job = await self._json("POST", self._url("parse/jobs"), json={
            "files": [{"source": {"type": "file_id", "file_id": file_id}}],
            "tier": tier,
            "output_formats": ["markdown"],
        })
        job_id = _required_string(job, "job_id")
        for _ in range(settings.MINERU_MAX_POLLS + 1):
            job_status = _required_string(job, "status")
            if job_status == "completed":
                break
            if job_status not in {"queued", "running"}:
                raise ValueError("MinerU parse job did not complete")
            await asyncio.sleep(settings.MINERU_POLL_INTERVAL_SECONDS)
            job = await self._json("GET", self._url(f"parse/jobs/{quote(job_id, safe='')}"))
        else:
            raise TimeoutError("MinerU polling budget exhausted")
        files = job.get("files")
        if not isinstance(files, list) or len(files) != 1 or files[0].get("status") != "completed":
            raise ValueError("MinerU parse result is incomplete")
        outputs = files[0].get("output_files")
        markdown = outputs.get("markdown") if isinstance(outputs, dict) else None
        if not isinstance(markdown, dict):
            raise ValueError("MinerU Markdown artifact is missing")
        text = await self._markdown(_required_string(markdown, "file_id"))
        return ParseOutput(parsed_data=ParsedResumeData(
            raw_text=text, parser_version=f"mineru-v1-{tier}",
        ))

    async def aclose(self) -> None:
        await self.client.aclose()


def create_adapter() -> MinerUAdapter:
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
