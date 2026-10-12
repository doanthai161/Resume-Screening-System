import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.resume_extraction import bind_extraction_source, load_extraction_json
from app.extraction_service import app as extractor
from app.models.resume_file import ResumeFile
from app.models.resume_extraction import PROMPT_VERSION, EXTRACTOR_VERSION
from app.workers.extraction_adapter import HTTPExtractionAdapter, ExtractionAdapterError

FOLDER = Path(__file__).resolve().parents[2] / "docs/contracts"
SOURCE = (FOLDER / "resume-extraction-v1.source.txt").read_bytes().decode("utf-8")


def model_payload():
    data = json.loads((FOLDER / "resume-extraction-v1.example.json").read_text(encoding="utf-8"))
    data.pop("schema_version")
    data.pop("source_text_sha256")
    def visit(value):
        if isinstance(value, dict):
            if "quote" in value:
                value.pop("start", None)
                value.pop("end", None)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(data)
    return data


def envelope(payload=None, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(payload if payload is not None else model_payload(), ensure_ascii=False)}}]}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "EXTRACTION_API_URL", "http://extractor:8002")
    monkeypatch.setattr(settings, "EXTRACTION_SERVICE_TOKEN", SecretStr("test-service-token-" + "x" * 32))
    monkeypatch.setattr(settings, "EXTRACTION_MODEL_NAME", "test-model")
    monkeypatch.setattr(settings, "EXTRACTION_LLM_CHAT_URL", "http://model/v1/chat/completions")
    monkeypatch.setattr(settings, "EXTRACTION_LLM_API_KEY", None)
    monkeypatch.setattr(extractor.state, "slots", asyncio.Semaphore(2))


def service_headers():
    return {"Authorization": "Bearer " + settings.EXTRACTION_SERVICE_TOKEN.get_secret_value()}


def request_payload():
    return {"raw_text": SOURCE, "model_name": "test-model", "prompt_version": PROMPT_VERSION}


def test_bind_source_resolves_quotes_and_never_changes_model_input():
    data = model_payload()
    original = copy.deepcopy(data)
    result = bind_extraction_source(data, raw_text=SOURCE)
    assert data == original
    assert result.skills.items[0].name.evidence[0].start == SOURCE.index("Python")


def test_model_prompt_contract_uses_quotes_and_leaves_metadata_to_be():
    from app.workers.extraction_prompt import system_prompt
    schema = json.loads(system_prompt().split("\nModel output contract:\n", 1)[1])
    assert "source_text_sha256" not in schema["properties"]
    assert "schema_version" not in schema["required"]
    assert schema["$defs"]["SourceEvidence"]["required"] == ["quote"]
    assert "review_issues" not in schema["$defs"]["Skill"]["properties"]


@pytest.mark.parametrize("source", [SOURCE.replace("Python", "Ruby"), SOURCE + "Python\n"])
def test_quote_resolution_rejects_missing_or_ambiguous_quotes(source):
    with pytest.raises(ValueError):
        bind_extraction_source(model_payload(), raw_text=source)


@pytest.mark.parametrize("data", ['{"a":1,"a":2}', "[]", "null", "broken"])
def test_json_contract_rejects_ambiguous_and_non_object_output(data):
    with pytest.raises(ValueError):
        load_extraction_json(data)


def test_extreme_nesting_is_rejected_without_recursion_failure():
    with pytest.raises(ValueError):
        load_extraction_json('{"x":' + '[' * 2000 + '0' + ']' * 2000 + '}')
    data = value = {}
    for _ in range(40):
        value["x"] = {}
        value = value["x"]
    with pytest.raises(ValueError, match="nesting"):
        bind_extraction_source(data, raw_text=SOURCE)


@pytest.mark.asyncio
async def test_inference_service_produces_verified_vi_en_profile(configured, monkeypatch):
    calls = []
    def model(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=envelope())
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as llm:
        monkeypatch.setattr(extractor.state, "llm_client", llm, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=extractor), base_url="http://extractor") as client:
            response = await client.post("/v1/extract", headers=service_headers(), json=request_payload())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["profile"]["personal_info"]["full_name"]["value"] == "Nguyễn An"
    assert data["profile"]["languages"]["items"][0]["proficiency"]["value"] == "B2"
    assert data["extractor_version"] == EXTRACTOR_VERSION
    assert calls[0]["temperature"] == 0 and "untrusted" in calls[0]["messages"][0]["content"]
    assert calls[0][settings.EXTRACTION_LLM_TOKEN_PARAMETER] == settings.EXTRACTION_LLM_MAX_TOKENS


@pytest.mark.asyncio
@pytest.mark.parametrize("problem,expected", [("auth", 401), ("body", 422), ("context", 413), ("model", 422), ("busy", 429)])
async def test_service_rejects_bad_requests_before_calling_model(configured, monkeypatch, problem, expected):
    model = AsyncMock()
    monkeypatch.setattr("app.extraction_service.call_model", model)
    data, headers = request_payload(), service_headers()
    if problem == "auth":
        headers = {}
    elif problem == "body":
        data["company_id"] = "forbidden"
    elif problem == "context":
        monkeypatch.setattr(settings, "EXTRACTION_MAX_SOURCE_CHARACTERS", 10)
    elif problem == "model":
        data["model_name"] = "other-model"
    else:
        monkeypatch.setattr(extractor.state, "slots", asyncio.Semaphore(0))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=extractor), base_url="http://extractor") as client:
        response = await client.post("/v1/extract", headers=headers, json=data)
    assert response.status_code == expected
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem,expected", [("bad_json", 422), ("length", 422), ("timeout", 503), ("throttled", 503)])
async def test_model_failures_are_bounded_sanitized_and_release_slot(configured, monkeypatch, problem, expected):
    calls = []
    def model(request):
        calls.append(request)
        if problem == "timeout":
            raise httpx.ReadTimeout("secret CV body should not escape")
        if problem == "throttled":
            return httpx.Response(429, text="private upstream key")
        return httpx.Response(200, json=envelope({"secret": "private CV body"}, finish="length" if problem == "length" else "stop"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as llm:
        monkeypatch.setattr(extractor.state, "llm_client", llm, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=extractor), base_url="http://extractor") as client:
            response = await client.post("/v1/extract", headers=service_headers(), json=request_payload())
    assert response.status_code == expected
    assert len(calls) == (2 if problem in {"bad_json", "length"} else 1)
    assert "private" not in response.text and "secret" not in response.text
    assert extractor.state.slots._value == 2


@pytest.mark.asyncio
async def test_model_invalid_output_can_be_repaired_once(configured, monkeypatch):
    calls = []
    def model(request):
        calls.append(request)
        return httpx.Response(200, json=envelope({} if len(calls) == 1 else model_payload()))
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as llm:
        monkeypatch.setattr(extractor.state, "llm_client", llm, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=extractor), base_url="http://extractor") as client:
            response = await client.post("/v1/extract", headers=service_headers(), json=request_payload())
    assert response.status_code == 200 and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (401, False), (422, False), (302, False)])
async def test_http_adapter_classifies_errors_without_redirecting(configured, monkeypatch, status, retryable):
    monkeypatch.setattr(ResumeFile, "find_one", AsyncMock(return_value=object()))
    run = SimpleNamespace(id="test", resume_file_id="test", company_id="test", source_text=SOURCE,
                          source_parse_revision=1, model_name="test-model", prompt_version=PROMPT_VERSION)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        adapter = HTTPExtractionAdapter(client, "http://extractor", "test-token")
        with pytest.raises(ExtractionAdapterError) as caught:
            await adapter.extract(run)
    assert caught.value.retryable == retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [False, True])
async def test_readiness_checks_registered_model(configured, monkeypatch, available):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"data": [{"id": "test-model"}] if available else []},
    ))) as llm:
        monkeypatch.setattr(extractor.state, "llm_client", llm, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=extractor), base_url="http://extractor") as client:
            response = await client.get("/ready")
    assert response.status_code == (200 if available else 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("url,expected", [
    ("http://model-gateway.internal/v1/chat/completions", 503),
    ("https://model-gateway.internal/v1/chat/completions", 200),
    ("https://model-gateway.internal/v1/not-chat-completions", 503),
])
async def test_production_model_endpoint_policy_applies_to_readiness_and_inference(configured, monkeypatch, url, expected):
    from app.extraction_service import call_model

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "EXTRACTION_LLM_CHAT_URL", url)
    inference = AsyncMock(wraps=call_model)
    monkeypatch.setattr("app.extraction_service.call_model", inference)
    calls = []

    def model(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})
        return httpx.Response(200, json=envelope())

    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as llm:
        monkeypatch.setattr(extractor.state, "llm_client", llm, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=extractor), base_url="http://extractor") as client:
            readiness = await client.get("/ready")
            response = await client.post("/v1/extract", headers=service_headers(), json=request_payload())

    assert readiness.status_code == response.status_code == expected
    assert extractor.state.slots._value == 2
    assert inference.await_count == 1  # Configuration errors must never trigger output repair.
    if expected == 200:
        assert [request.method for request in calls] == ["GET", "POST"]
        assert str(calls[0].url) == "https://model-gateway.internal/v1/models"
        assert str(calls[1].url) == url
        assert response.json()["profile"]["personal_info"]["full_name"]["value"] == "Nguyễn An"
    else:
        assert calls == []
        assert "model-gateway.internal" not in readiness.text + response.text
        assert "http://" not in readiness.text + response.text
