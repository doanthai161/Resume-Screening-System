from unittest.mock import Mock

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, settings
from app.workers import extraction_adapter


@pytest.fixture
def make_settings(monkeypatch):
    # Disable the local env file and unset timeout environment overrides so these
    # checks use declared defaults rather than the developer's runtime settings.
    for name in (
        "EXTRACTION_LLM_TIMEOUT_SECONDS",
        "EXTRACTION_REQUEST_TIMEOUT_SECONDS",
        "WORKER_JOB_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    def create(**overrides):
        values = {
            "ENVIRONMENT": "development",
            "DEBUG": False,
            "CREATE_FIRST_SUPERUSER": False,
            "REFRESH_COOKIE_SECURE": False,
            "REFRESH_COOKIE_SAMESITE": "lax",
            "MINERU_PDF_TIER": "basic",
            "MINERU_FALLBACK_TIER": "standard",
            "EXTRACTION_API_URL": None,
            "EXTRACTION_SERVICE_TOKEN": None,
            "EXTRACTION_MODEL_NAME": "",
            "EXTRACTION_LLM_CHAT_URL": None,
            "EXTRACTION_LLM_API_KEY": None,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return create


def test_default_extraction_timeouts_allow_initial_call_and_repair(make_settings):
    config = make_settings(EXTRACTION_LLM_CHAT_URL="https://model.example/v1/chat/completions")
    assert config.EXTRACTION_LLM_TIMEOUT_SECONDS == 90
    assert config.EXTRACTION_REQUEST_TIMEOUT_SECONDS == 240
    assert config.WORKER_JOB_TIMEOUT_SECONDS == 900


@pytest.mark.parametrize("llm_timeout,request_timeout", [(90, 209), (180, 240), (300, 600)])
def test_model_gateway_rejects_insufficient_request_budget(make_settings, llm_timeout, request_timeout):
    with pytest.raises(ValidationError, match="2 \\* EXTRACTION_LLM_TIMEOUT_SECONDS \\+ 30"):
        make_settings(
            EXTRACTION_LLM_CHAT_URL="https://model.example/v1/chat/completions",
            EXTRACTION_LLM_TIMEOUT_SECONDS=llm_timeout,
            EXTRACTION_REQUEST_TIMEOUT_SECONDS=request_timeout,
        )


def test_invalid_timeout_configuration_never_formats_secret_inputs(make_settings):
    api_key = "synthetic-api-key-" + "a" * 40
    service_token = "synthetic-service-token-" + "b" * 40
    jwt_secret = "synthetic-jwt-secret-" + "c" * 40
    mongo_uri = "mongodb://synthetic-user:synthetic-password@localhost:27017/test"
    with pytest.raises(ValidationError) as caught:
        make_settings(
            EXTRACTION_LLM_CHAT_URL="https://model.example/v1/chat/completions",
            EXTRACTION_LLM_TIMEOUT_SECONDS=180,
            EXTRACTION_REQUEST_TIMEOUT_SECONDS=240,
            EXTRACTION_LLM_API_KEY=api_key,
            EXTRACTION_SERVICE_TOKEN=service_token,
            SECRET_KEY=jwt_secret,
            MONGODB_URI=mongo_uri,
        )
    message = str(caught.value)
    assert "EXTRACTION_REQUEST_TIMEOUT_SECONDS" in message
    for secret in (api_key, service_token, jwt_secret, mongo_uri, "synthetic-password"):
        assert secret not in message
    assert "input_value" not in message


@pytest.mark.parametrize("llm_timeout,request_timeout", [(90, 210), (180, 390), (285, 600)])
def test_model_gateway_accepts_boundary_and_large_legal_budget(make_settings, llm_timeout, request_timeout):
    config = make_settings(
        EXTRACTION_LLM_CHAT_URL="https://model.example/v1/chat/completions",
        EXTRACTION_LLM_TIMEOUT_SECONDS=llm_timeout,
        EXTRACTION_REQUEST_TIMEOUT_SECONDS=request_timeout,
    )
    assert config.EXTRACTION_REQUEST_TIMEOUT_SECONDS == request_timeout


def test_external_extractor_without_local_llm_keeps_its_own_timeout_budget(make_settings):
    config = make_settings(
        EXTRACTION_API_URL="https://extractor.example",
        EXTRACTION_LLM_CHAT_URL=None,
        EXTRACTION_LLM_TIMEOUT_SECONDS=300,
        EXTRACTION_REQUEST_TIMEOUT_SECONDS=10,
        WORKER_JOB_TIMEOUT_SECONDS=20,
    )
    assert config.EXTRACTION_LLM_CHAT_URL is None
    assert config.EXTRACTION_REQUEST_TIMEOUT_SECONDS == 10


def test_non_extraction_worker_keeps_independent_job_timeout(make_settings):
    config = make_settings(
        EXTRACTION_LLM_CHAT_URL=None,
        WORKER_ADAPTER_FACTORY="app.workers.mineru_adapter:create_adapter",
        WORKER_JOB_TIMEOUT_SECONDS=1,
    )
    assert config.WORKER_JOB_TIMEOUT_SECONDS == 1
    assert config.EXTRACTION_REQUEST_TIMEOUT_SECONDS == 240


@pytest.fixture
def configured_factory(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "EXTRACTION_API_URL", "http://extractor:8002/")
    monkeypatch.setattr(settings, "EXTRACTION_SERVICE_TOKEN", SecretStr("test-service-token-" + "x" * 32))
    monkeypatch.setattr(settings, "EXTRACTION_REQUEST_TIMEOUT_SECONDS", 240)
    monkeypatch.setattr(settings, "WORKER_JOB_TIMEOUT_SECONDS", 900)


@pytest.mark.parametrize("job_timeout", [180, 240])
def test_extraction_factory_rejects_short_worker_timeout_before_creating_client(configured_factory, monkeypatch, job_timeout):
    monkeypatch.setattr(settings, "WORKER_JOB_TIMEOUT_SECONDS", job_timeout)
    create_client = Mock()
    monkeypatch.setattr(extraction_adapter.httpx, "AsyncClient", create_client)
    with pytest.raises(ValueError, match="WORKER_JOB_TIMEOUT_SECONDS must exceed"):
        extraction_adapter.create_adapter()
    create_client.assert_not_called()


def test_extraction_factory_rejects_bad_endpoint_without_leaking_client(configured_factory, monkeypatch):
    monkeypatch.setattr(settings, "EXTRACTION_API_URL", "not-a-url")
    create_client = Mock()
    monkeypatch.setattr(extraction_adapter.httpx, "AsyncClient", create_client)
    with pytest.raises(ValueError, match="plain HTTP"):
        extraction_adapter.create_adapter()
    create_client.assert_not_called()


@pytest.mark.asyncio
async def test_valid_extraction_factory_can_close_its_http_client(configured_factory):
    adapter = extraction_adapter.create_adapter()
    try:
        assert adapter.base_url == "http://extractor:8002"
        assert adapter.client.timeout.read == 240
        assert not adapter.client.is_closed
    finally:
        await adapter.aclose()
    assert adapter.client.is_closed
