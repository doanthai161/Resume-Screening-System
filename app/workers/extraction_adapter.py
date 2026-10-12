"""HTTP adapter for the separate extractor service, with bounded response reads."""
import asyncio
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.core.resume_extraction import load_extraction_json, validate_resume_extraction
from app.models.resume_extraction import ResumeExtractionRun
from app.models.resume_file import ResumeFile
from app.schemas.extraction import ExtractionServiceOutput
from app.services.extraction_service import source_predicate

MAX_RESPONSE_BYTES = 2_200_000


class ExtractionAdapterError(ValueError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__("Resume extraction failed")
        self.code, self.retryable = code, retryable


def validate_service_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Configure a plain HTTP(S) service URL without credentials")
    if settings.ENVIRONMENT == "production" and parsed.scheme != "https" and parsed.hostname not in {"extractor", "localhost", "127.0.0.1"}:
        raise ValueError("External extraction services require HTTPS in production")
    return url.rstrip("/")


async def read_bounded(response: httpx.Response) -> bytes:
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise ExtractionAdapterError("extraction_response_too_large", retryable=False)
        chunks.append(chunk)
    return b"".join(chunks)


class HTTPExtractionAdapter:
    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str):
        if not token:
            raise ValueError("Extraction service token is required")
        self.client, self.base_url, self.token = client, validate_service_url(base_url), token

    async def extract(self, run: ResumeExtractionRun) -> ExtractionServiceOutput:
        if not await ResumeFile.find_one(source_predicate(run)):
            raise ExtractionAdapterError("extraction_source_changed", retryable=False)
        try:
            async with asyncio.timeout(settings.EXTRACTION_REQUEST_TIMEOUT_SECONDS):
                async with self.client.stream(
                    "POST", f"{self.base_url}/v1/extract", follow_redirects=False,
                    headers={"Authorization": f"Bearer {self.token}", "Idempotency-Key": str(run.id)},
                    json={"raw_text": run.source_text, "model_name": run.model_name, "prompt_version": run.prompt_version},
                ) as response:
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        raise ExtractionAdapterError("extraction_service_unavailable", retryable=True)
                    if response.status_code != 200:
                        raise ExtractionAdapterError("extraction_service_rejected", retryable=False)
                    data = await read_bounded(response)
            output = ExtractionServiceOutput.model_validate(load_extraction_json(data))
            if output.model_name != run.model_name or output.prompt_version != run.prompt_version:
                raise ValueError("Extraction configuration mismatch")
            validate_resume_extraction(output.profile, raw_text=run.source_text)
            return output
        except ExtractionAdapterError:
            raise
        except (httpx.TransportError, TimeoutError):
            raise ExtractionAdapterError("extraction_service_timeout", retryable=True) from None
        except (ValueError, ValidationError):
            raise ExtractionAdapterError("extraction_invalid_output", retryable=False) from None

    async def aclose(self):
        await self.client.aclose()


def create_adapter() -> HTTPExtractionAdapter:
    if not settings.EXTRACTION_API_URL or not settings.EXTRACTION_SERVICE_TOKEN:
        raise ValueError("Configure EXTRACTION_API_URL and EXTRACTION_SERVICE_TOKEN")
    if settings.WORKER_JOB_TIMEOUT_SECONDS <= settings.EXTRACTION_REQUEST_TIMEOUT_SECONDS:
        raise ValueError("WORKER_JOB_TIMEOUT_SECONDS must exceed EXTRACTION_REQUEST_TIMEOUT_SECONDS for extraction")
    base_url = validate_service_url(settings.EXTRACTION_API_URL)
    return HTTPExtractionAdapter(
        httpx.AsyncClient(timeout=settings.EXTRACTION_REQUEST_TIMEOUT_SECONDS, trust_env=False,
                          limits=httpx.Limits(max_connections=2, max_keepalive_connections=1)),
        base_url, settings.EXTRACTION_SERVICE_TOKEN.get_secret_value(),
    )
