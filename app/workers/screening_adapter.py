"""BE adapter for the fixed screening HTTP service; no provider inference here."""
import asyncio
import ipaddress
import json
import re
import unicodedata
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.resume_extraction import load_extraction_json
from app.core.scoring import calculate_screening
from app.core.screening_evidence import bind_screening_output
from app.core.screening_http import build_screening_request
from app.models.screening_run import ScreeningRun
from app.schemas.screening_http import ScreeningServiceRequest, ScreeningServiceResponse
from app.schemas.worker import ScreeningOutput

MAX_REQUEST_BYTES = 6_500_000
MAX_RESPONSE_BYTES = 2_200_000


class ScreeningAdapterError(ValueError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__("Screening service request failed")
        self.code, self.retryable = code, retryable


def validate_screening_url(url: str) -> str:
    """Validate before constructing a client; callers never select the endpoint."""
    try:
        if not isinstance(url, str) or not url or any(
            character.isspace() or unicodedata.category(character) == "Cc" for character in url
        ):
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "?" in url or "#" in url):
            raise ValueError()
        if parsed.netloc.endswith(":") or (parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
        host = parsed.hostname
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            ascii_host = host.encode("idna").decode("ascii").rstrip(".")
            labels = ascii_host.split(".")
            if len(ascii_host) > 253 or any(
                not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in labels
            ):
                raise ValueError()
        if settings.ENVIRONMENT == "production" and parsed.scheme == "http":
            if host not in {"screener", "localhost"} and not (address and address.is_loopback):
                raise ValueError()
        # Validate URL syntax with the client library too, without doing I/O.
        httpx.URL(url)
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise ValueError("Configure a valid HTTP(S) screening service URL without credentials") from None
    return url.rstrip("/")


def _validate_token(token: str) -> None:
    if (not isinstance(token, str) or len(token) < 32 or not token.isascii()
            or any(character.isspace() or unicodedata.category(character) == "Cc" for character in token)):
        raise ValueError("Configure a screening service token of at least 32 characters")


async def _read_response(response: httpx.Response) -> bytes:
    media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    if not re.fullmatch(r"application/(?:json|[!#$%&'*+.^_`|~0-9a-z-]+\+json)", media_type):
        raise ScreeningAdapterError("screening_invalid_output", retryable=False)
    # Reject before reading/decompression; a decoded-size check alone does not
    # prevent a compressed chunk from allocating an oversized intermediate value.
    if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
        raise ScreeningAdapterError("screening_compressed_response", retryable=False)
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            length = int(length)
            if length < 0:
                raise ValueError()
        except ValueError:
            raise ScreeningAdapterError("screening_invalid_output", retryable=False) from None
        if length > MAX_RESPONSE_BYTES:
            raise ScreeningAdapterError("screening_response_too_large", retryable=False)
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise ScreeningAdapterError("screening_response_too_large", retryable=False)
        chunks.append(chunk)
    return b"".join(chunks)


def _verify_echo(request: ScreeningServiceRequest, response: ScreeningServiceResponse) -> None:
    if (
        response.contract_version != request.contract_version
        or response.run_id != request.run_id or response.input_hash != request.input_hash
        or response.source_text_sha256 != request.source.source_text_sha256
        or response.resume_profile_id != request.source.resume_profile_id
        or response.profile_version != request.source.profile_version
        or response.scorecard_id != request.scorecard.scorecard_id
        or response.scorecard_version != request.scorecard.version
        or response.model != request.model or response.prompt_version != request.prompt_version
    ):
        raise ValueError("Screening service response does not match its immutable request")


class HTTPScreeningAdapter:
    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str):
        _validate_token(token)
        self.base_url = validate_screening_url(base_url)
        self.client, self.token = client, token

    async def screen(self, run: ScreeningRun) -> ScreeningOutput:
        try:
            request = build_screening_request(run)
            body = json.dumps(request.model_dump(mode="json", warnings=False), ensure_ascii=False,
                              allow_nan=False, separators=(",", ":")).encode("utf-8")
        except ValueError:
            raise ScreeningAdapterError("screening_invalid_input", retryable=False) from None
        if len(body) > MAX_REQUEST_BYTES:
            raise ScreeningAdapterError("screening_request_too_large", retryable=False)
        try:
            async with asyncio.timeout(settings.SCREENING_REQUEST_TIMEOUT_SECONDS):
                async with self.client.stream(
                    "POST", f"{self.base_url}/v1/screen", follow_redirects=False,
                    headers={"Authorization": f"Bearer {self.token}", "Idempotency-Key": str(request.run_id),
                             "Content-Type": "application/json", "Accept-Encoding": "identity"},
                    content=body,
                ) as response:
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        raise ScreeningAdapterError("screening_service_unavailable", retryable=True)
                    if response.status_code != 200:
                        raise ScreeningAdapterError("screening_service_rejected", retryable=False)
                    data = await _read_response(response)
            result = ScreeningServiceResponse.model_validate(load_extraction_json(data))
            _verify_echo(request, result)
            output = bind_screening_output(result.output, request.source)
            calculate_screening(request.scorecard.model_dump(mode="python"), output.criteria_evaluations)
            return output
        except ScreeningAdapterError:
            raise
        except (httpx.TransportError, TimeoutError):
            raise ScreeningAdapterError("screening_service_timeout", retryable=True) from None
        except ValueError:
            raise ScreeningAdapterError("screening_invalid_output", retryable=False) from None

    async def aclose(self):
        await self.client.aclose()


def create_adapter() -> HTTPScreeningAdapter:
    if not settings.SCREENING_API_URL or not settings.SCREENING_SERVICE_TOKEN:
        raise ValueError("Configure SCREENING_API_URL and SCREENING_SERVICE_TOKEN")
    if settings.WORKER_JOB_TIMEOUT_SECONDS <= settings.SCREENING_REQUEST_TIMEOUT_SECONDS:
        raise ValueError("WORKER_JOB_TIMEOUT_SECONDS must exceed SCREENING_REQUEST_TIMEOUT_SECONDS for screening")
    token = settings.SCREENING_SERVICE_TOKEN.get_secret_value()
    _validate_token(token)
    base_url = validate_screening_url(settings.SCREENING_API_URL)
    client = httpx.AsyncClient(timeout=settings.SCREENING_REQUEST_TIMEOUT_SECONDS, trust_env=False,
                              limits=httpx.Limits(max_connections=2, max_keepalive_connections=1))
    return HTTPScreeningAdapter(client, base_url, token)
