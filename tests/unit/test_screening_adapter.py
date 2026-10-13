import asyncio
import copy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
import httpx
from pydantic import SecretStr
import pytest

from app.core.config import settings
from app.schemas.screening_source import ScreeningSourceSnapshot
from app.workers import screening_adapter
from app.workers.screening_adapter import HTTPScreeningAdapter, ScreeningAdapterError, validate_screening_url


CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs/contracts"
SOURCE = (CONTRACT_DIR / "resume-extraction-v1.source.txt").read_bytes().decode("utf-8")
TOKEN = "synthetic-screening-token-" + "x" * 32


def run():
    source = ScreeningSourceSnapshot(
        resume_profile_id=ObjectId(), profile_version=2, extraction_run_id=ObjectId(),
        source_parse_revision=1, source_parse_run_id=ObjectId(), resume_checksum="a" * 64,
        source_text=SOURCE, source_text_sha256=sha256(SOURCE.encode()).hexdigest(),
        profile=json.loads((CONTRACT_DIR / "resume-extraction-v1.example.json").read_text(encoding="utf-8")),
        extraction_model_name="extractor-model", extraction_prompt_version="resume-extract-v1",
        extractor_version="http-extractor-1.0",
    )
    company_id, application_id, resume_id, scorecard_id, ai_model_id, job_id = [ObjectId() for _ in range(6)]
    card = {
        "id": str(scorecard_id), "version": 3,
        "company_id": str(company_id), "job_requirement_id": str(job_id),
        "criteria": [{"code": "python", "name": "Python", "weight": 1.0}], "pass_threshold": 70,
    }
    model = {"ai_model_id": str(ai_model_id), "provider": "custom", "model_id": "test-model", "version": "2.0"}
    input_data = {
        "application_id": str(application_id), "application_revision": 1,
        "resume_checksum": source.resume_checksum,
        "resume_profile_id": str(source.resume_profile_id), "profile_version": source.profile_version,
        "source_parse_revision": source.source_parse_revision, "source_text_sha256": source.source_text_sha256,
        "scorecard_id": str(scorecard_id), "scorecard_version": card["version"],
        "ai_model_id": str(ai_model_id), "ai_model_version": model["version"],
        "screening_prompt_version": "screening-v1",
    }
    return SimpleNamespace(
        id=ObjectId(), company_id=company_id, application_id=application_id, resume_file_id=resume_id,
        scorecard_id=scorecard_id, ai_model_id=ai_model_id, job_requirement_id=job_id,
        input_hash=sha256(json.dumps(input_data, sort_keys=True).encode()).hexdigest(),
        source_snapshot=source, config_snapshot={
            "scorecard": card, "model": model, "screening_prompt_version": "screening-v1", "input": input_data,
        },
    )


def response_payload(request):
    return {
        "contract_version": "1.0", "run_id": request["run_id"], "input_hash": request["input_hash"],
        "source_text_sha256": request["source"]["source_text_sha256"],
        "resume_profile_id": request["source"]["resume_profile_id"], "profile_version": request["source"]["profile_version"],
        "scorecard_id": request["scorecard"]["scorecard_id"], "scorecard_version": request["scorecard"]["version"],
        "model": copy.deepcopy(request["model"]), "prompt_version": request["prompt_version"],
        "output": {"criteria_evaluations": {"python": {
            "status": "evaluated", "score": 80, "reason": "Có Python trong CV", "evidence": [{"text": "Python"}],
        }}},
    }


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SCREENING_REQUEST_TIMEOUT_SECONDS", 240)


@pytest.mark.asyncio
async def test_valid_screening_request_uses_fixed_endpoint_stable_key_and_bound_evidence():
    item, calls = run(), []
    original_source = item.source_snapshot.model_dump(mode="json")

    def service(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["source"]["profile"] == original_source["profile"]
        assert "model_config" not in payload and "api_key" not in request.content.decode()
        return httpx.Response(200, json=response_payload(payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        adapter = HTTPScreeningAdapter(client, "http://screener:8003/", TOKEN)
        first = await adapter.screen(item)
        second = await adapter.screen(item)
    assert len(calls) == 2  # Exactly one HTTP request per attempt; no adapter retries.
    assert all(str(request.url) == "http://screener:8003/v1/screen" for request in calls)
    assert all(request.headers["Idempotency-Key"] == str(item.id) for request in calls)
    assert all(request.headers["Authorization"] == "Bearer " + TOKEN for request in calls)
    assert all(request.headers["Accept-Encoding"] == "identity" for request in calls)
    span = first.criteria_evaluations["python"].evidence[0]
    assert SOURCE[span.start:span.end] == "Python"
    assert first == second and item.source_snapshot.model_dump(mode="json") == original_source


@pytest.mark.asyncio
@pytest.mark.parametrize("status,retryable", [
    (408, True), (429, True), (500, True), (503, True),
    (400, False), (401, False), (403, False), (413, False), (422, False), (302, False), (202, False),
])
async def test_http_errors_are_classified_without_redirects_or_sensitive_output(status, retryable):
    calls = []

    def service(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://untrusted.example"}, text="synthetic private upstream key and CV")

    async with httpx.AsyncClient(transport=httpx.MockTransport(service), follow_redirects=True) as client:
        adapter = HTTPScreeningAdapter(client, "http://screener", TOKEN)
        with pytest.raises(ScreeningAdapterError) as caught:
            await adapter.screen(run())
    assert caught.value.retryable is retryable
    assert len(calls) == 1
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "run_id", "input_hash", "source_text_sha256", "resume_profile_id", "profile_version",
    "scorecard_id", "scorecard_version", "model_id", "provider", "model_version", "ai_model_id", "prompt_version",
])
async def test_response_from_another_request_snapshot_or_model_is_rejected(field):
    def service(request):
        data = response_payload(json.loads(request.content))
        if field in {"model_id", "provider", "model_version", "ai_model_id"}:
            key = "version" if field == "model_version" else field
            data["model"][key] = str(ObjectId()) if field == "ai_model_id" else "other"
        elif field in {"profile_version", "scorecard_version"}:
            data[field] += 1
        elif field in {"input_hash", "source_text_sha256"}:
            data[field] = "b" * 64
        elif field == "prompt_version":
            data[field] = "screening-other"
        else:
            data[field] = str(ObjectId())
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(ScreeningAdapterError) as caught:
            await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run())
    assert caught.value.code == "screening_invalid_output" and not caught.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["aggregate", "unknown_code", "quote", "page", "span", "duplicate_key", "bad_json"])
async def test_bad_contract_scoring_or_evidence_is_permanent(problem):
    def service(request):
        data = response_payload(json.loads(request.content))
        if problem == "aggregate":
            data["output"]["overall_score"] = 100
        elif problem == "unknown_code":
            data["output"]["criteria_evaluations"]["invented"] = data["output"]["criteria_evaluations"].pop("python")
        elif problem == "quote":
            data["output"]["criteria_evaluations"]["python"]["evidence"][0]["text"] = "invented private CV data"
        elif problem == "page":
            data["output"]["criteria_evaluations"]["python"]["evidence"][0]["page"] = 1
        elif problem == "span":
            data["output"]["criteria_evaluations"]["python"]["evidence"][0].update(start=0, end=6)
        elif problem == "duplicate_key":
            body = json.dumps(data)
            return httpx.Response(200, headers={"Content-Type": "application/json"},
                                  content=body[:-1] + ', "input_hash":"' + data["input_hash"] + '"}')
        else:
            return httpx.Response(200, headers={"Content-Type": "application/json"}, content="not JSON: private CV")
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(ScreeningAdapterError) as caught:
            await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run())
    assert caught.value.code == "screening_invalid_output" and not caught.value.retryable
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["missing_source", "mutated_hash", "missing_model", "model_credentials"])
async def test_invalid_input_is_rejected_before_outbound(problem):
    item = run()
    if problem == "missing_source":
        item.source_snapshot = None
    elif problem == "mutated_hash":
        item.source_snapshot.source_text_sha256 = "b" * 64
    elif problem == "missing_model":
        item.config_snapshot.pop("model")
    else:
        item.config_snapshot["model"]["api_key"] = "synthetic-private-key"
    service = Mock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(ScreeningAdapterError) as caught:
            await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(item)
    assert caught.value.code == "screening_invalid_input" and not caught.value.retryable
    service.assert_not_called()
    assert "private" not in str(caught.value)


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, gate=None):
        self.chunks, self.gate, self.closed = chunks, gate, False
        self.chunks_read = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.chunks_read += 1
            yield chunk
        if self.gate is not None:
            self.gate.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("problem,code", [
    ("chunks", "screening_response_too_large"), ("length", "screening_response_too_large"),
    ("compression", "screening_compressed_response"), ("invalid_length", "screening_invalid_output"),
])
async def test_response_limits_reject_early_and_close_stream(monkeypatch, problem, code):
    monkeypatch.setattr(screening_adapter, "MAX_RESPONSE_BYTES", 64)
    headers, chunks = {"Content-Type": "application/json"}, [b"x" * 40, b"x" * 40]
    if problem == "length":
        headers["Content-Length"] = "100"
    elif problem == "compression":
        headers["Content-Encoding"] = "gzip"
    elif problem == "invalid_length":
        headers["Content-Length"] = "invalid"
    stream = TrackedStream(chunks)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, stream=stream))) as client:
        with pytest.raises(ScreeningAdapterError) as caught:
            await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run())
    assert caught.value.code == code and not caught.value.retryable
    assert stream.closed
    assert stream.chunks_read == (2 if problem == "chunks" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [
    None, "text/plain", "text/html", "application/octet-stream", "text/json", "application/+json",
    "application/jsonx", "application/json, text/html",
])
async def test_response_media_type_is_required_and_rejected_before_read(content_type):
    headers = {"Content-Type": content_type} if content_type is not None else {}
    stream = TrackedStream([b"synthetic private CV body"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, stream=stream))) as client:
        with pytest.raises(ScreeningAdapterError) as caught:
            await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run())
    assert caught.value.code == "screening_invalid_output" and not caught.value.retryable
    assert stream.closed and stream.chunks_read == 0
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [
    "application/json", "APPLICATION/JSON; charset=utf-8", "application/problem+json",
    "application/vnd.ai.screening+json; charset=UTF-8",
])
async def test_json_response_media_types_accept_parameters_case_and_vendor_suffix(content_type):
    def service(request):
        return httpx.Response(200, headers={"Content-Type": content_type}, json=response_payload(json.loads(request.content)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        output = await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run())
    assert output.criteria_evaluations["python"].score == 80


@pytest.mark.asyncio
async def test_request_limit_is_checked_before_outbound(monkeypatch):
    monkeypatch.setattr(screening_adapter, "MAX_REQUEST_BYTES", 10)
    service = Mock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(ScreeningAdapterError) as caught:
            await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run())
    assert caught.value.code == "screening_request_too_large" and not caught.value.retryable
    service.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["transport", "total"])
async def test_transport_and_total_timeouts_are_retryable_and_do_not_retry(monkeypatch, kind):
    monkeypatch.setattr(settings, "SCREENING_REQUEST_TIMEOUT_SECONDS", .01)
    gate, calls = asyncio.Event(), []
    stream = TrackedStream([b"{"], gate=gate)

    def service(request):
        calls.append(request)
        if kind == "transport":
            raise httpx.ReadTimeout("synthetic private upstream connection")
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        with pytest.raises(ScreeningAdapterError) as caught:
            await HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run())
    assert caught.value.code == "screening_service_timeout" and caught.value.retryable
    assert len(calls) == 1
    assert "private" not in str(caught.value)
    if kind == "total":
        assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_closes_response_without_conversion_or_retry():
    gate = asyncio.Event()
    stream = TrackedStream([b"{"], gate=gate)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream))) as client:
        task = asyncio.create_task(HTTPScreeningAdapter(client, "http://screener", TOKEN).screen(run()))
        try:
            await asyncio.wait_for(gate.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert stream.closed


@pytest.mark.parametrize("url", [
    "file:///tmp/screener", "http://", "http://user:pass@screener", "http://@screener",
    "http://screener?", "http://screener?q=secret", "http://screener#", "http://screener#secret",
    "http://screener:0", "http://screener:65536", "http://screener:bad", "http://screener:",
    "http://screen er", "http://screener\n", "http://screener\x00", "http://screener\\evil",
    "http://bad_host", "https://bad%20host", " http://screener", "http://-screener",
])
def test_invalid_service_urls_are_rejected_without_disclosing_the_input(url):
    with pytest.raises(ValueError) as caught:
        validate_screening_url(url)
    assert url not in str(caught.value)


@pytest.mark.parametrize("url,allowed", [
    ("http://screener:8003", True), ("http://localhost:8003", True),
    ("http://127.0.0.1:8003", True), ("http://[::1]:8003", True),
    ("http://external.example", False), ("http://screener.evil.example", False),
    ("http://10.0.0.1", False), ("https://external.example:443/service", True),
])
def test_production_http_policy_allows_only_internal_screener_or_loopback(monkeypatch, url, allowed):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    if allowed:
        assert validate_screening_url(url) == url.rstrip("/")
    else:
        with pytest.raises(ValueError):
            validate_screening_url(url)


@pytest.mark.parametrize("problem", ["url", "token", "unicode_token", "budget"])
def test_factory_validates_before_creating_client(monkeypatch, problem):
    monkeypatch.setattr(settings, "SCREENING_API_URL", "http://screener:8003")
    monkeypatch.setattr(settings, "SCREENING_SERVICE_TOKEN", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "WORKER_JOB_TIMEOUT_SECONDS", 900)
    if problem == "url":
        monkeypatch.setattr(settings, "SCREENING_API_URL", "http://user:private-key@screener")
    elif problem == "token":
        monkeypatch.setattr(settings, "SCREENING_SERVICE_TOKEN", SecretStr("short"))
    elif problem == "unicode_token":
        monkeypatch.setattr(settings, "SCREENING_SERVICE_TOKEN", SecretStr("á" * 32))
    else:
        monkeypatch.setattr(settings, "WORKER_JOB_TIMEOUT_SECONDS", 240)
    create_client = Mock()
    monkeypatch.setattr(screening_adapter.httpx, "AsyncClient", create_client)
    with pytest.raises(ValueError):
        screening_adapter.create_adapter()
    create_client.assert_not_called()


@pytest.mark.asyncio
async def test_factory_uses_declared_timeout_and_closes_client(monkeypatch):
    monkeypatch.setattr(settings, "SCREENING_API_URL", "http://screener:8003/")
    monkeypatch.setattr(settings, "SCREENING_SERVICE_TOKEN", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "WORKER_JOB_TIMEOUT_SECONDS", 900)
    adapter = screening_adapter.create_adapter()
    try:
        assert adapter.base_url == "http://screener:8003"
        assert adapter.client.timeout.read == 240 and not adapter.client.is_closed
        assert adapter.client._trust_env is False
    finally:
        await adapter.aclose()
    assert adapter.client.is_closed
