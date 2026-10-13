"""Versioned BE-to-AI HTTP contract; provider credentials stay in the AI service."""
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.scoring import ScoringSnapshot
from app.schemas.screening_source import ScreeningSourceSnapshot
from app.schemas.worker import ScreeningOutput

SCREENING_PROMPT_VERSION = "screening-v1"


class ScreeningHTTPModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", hide_input_in_errors=True)


class ScreeningModelSpec(ScreeningHTTPModel):
    ai_model_id: PydanticObjectId
    provider: str = Field(..., strict=True, min_length=1, max_length=50)
    model_id: str = Field(..., strict=True, min_length=1, max_length=150)
    version: str = Field(..., strict=True, min_length=1, max_length=150)

    @field_validator("provider", "model_id", "version")
    @classmethod
    def nonblank_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Screening model identifiers must be nonblank")
        return value


class ScreeningHTTPScorecard(ScoringSnapshot):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", hide_input_in_errors=True)
    scorecard_id: PydanticObjectId
    version: int = Field(..., strict=True, ge=1)


class ScreeningServiceRequest(ScreeningHTTPModel):
    contract_version: Literal["1.0"] = "1.0"
    run_id: PydanticObjectId
    company_id: PydanticObjectId
    application_id: PydanticObjectId
    resume_file_id: PydanticObjectId
    input_hash: str = Field(..., strict=True, pattern=r"^[0-9a-f]{64}$")
    source: ScreeningSourceSnapshot
    scorecard: ScreeningHTTPScorecard
    model: ScreeningModelSpec
    prompt_version: Literal["screening-v1"]


class ScreeningServiceResponse(ScreeningHTTPModel):
    contract_version: Literal["1.0"] = "1.0"
    run_id: PydanticObjectId
    input_hash: str = Field(..., strict=True, pattern=r"^[0-9a-f]{64}$")
    source_text_sha256: str = Field(..., strict=True, pattern=r"^[0-9a-f]{64}$")
    resume_profile_id: PydanticObjectId
    profile_version: int = Field(..., strict=True, ge=1)
    scorecard_id: PydanticObjectId
    scorecard_version: int = Field(..., strict=True, ge=1)
    model: ScreeningModelSpec
    prompt_version: Literal["screening-v1"]
    output: ScreeningOutput
