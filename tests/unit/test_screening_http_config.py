"""Screening settings remain optional until the HTTP worker is enabled."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**overrides):
    return Settings(_env_file=None, ENVIRONMENT="development", DEBUG=False,
                    CREATE_FIRST_SUPERUSER=False, EXTRACTION_LLM_CHAT_URL=None, **overrides)


def test_screening_settings_can_be_left_unconfigured():
    config = make_settings(SCREENING_API_URL="", SCREENING_SERVICE_TOKEN="")
    assert config.SCREENING_API_URL is None and config.SCREENING_SERVICE_TOKEN is None
    assert config.SCREENING_REQUEST_TIMEOUT_SECONDS == 240


@pytest.mark.parametrize("changes", [
    {"SCREENING_SERVICE_TOKEN": "short"},
    {"SCREENING_REQUEST_TIMEOUT_SECONDS": 9},
    {"SCREENING_REQUEST_TIMEOUT_SECONDS": 601},
    {"SCREENING_API_URL": "ftp://service.example"},
    {"SCREENING_API_URL": "https://service.example?key=synthetic-secret"},
])
def test_invalid_screening_settings_do_not_display_secret_inputs(changes):
    with pytest.raises(ValidationError) as caught:
        make_settings(**changes)
    assert "input_value" not in str(caught.value)
    assert "synthetic-secret" not in str(caught.value)


def test_screening_settings_preserve_independent_parse_worker_budget():
    config = make_settings(
        SCREENING_API_URL="https://service.example", SCREENING_SERVICE_TOKEN="synthetic-" + "x" * 32,
        WORKER_JOB_TIMEOUT_SECONDS=1,
    )
    assert config.WORKER_JOB_TIMEOUT_SECONDS == 1
    # The relationship is checked only by the screening factory, not other workers.
    assert config.SCREENING_REQUEST_TIMEOUT_SECONDS == 240
