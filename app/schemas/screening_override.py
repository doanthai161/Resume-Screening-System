"""Explicit HR assessments alongside immutable AI results."""
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.models.screening_result import ScreeningDecision
from app.schemas.recruitment import ScreeningResultResponse
from app.schemas.resume import _object_id
from app.utils.time import ensure_utc

Reason = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)]
HRScore = Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)]


class ScreeningOverrideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    company_id: str
    expected_version: int = Field(..., strict=True, ge=0)
    overall_score: Optional[HRScore] = Field(...)
    decision: ScreeningDecision
    reason: Reason
    comments: Optional[str] = Field(None, strict=True, max_length=5000)

    _company_id = field_validator("company_id", mode="before")(_object_id)

    @model_validator(mode="after")
    def require_score_for_final_decision(self):
        if self.decision != ScreeningDecision.HOLD and self.overall_score is None:
            raise ValueError("A pass or fail HR decision requires a score")
        return self


class ScreeningOverrideAIBase(BaseModel):
    """Small provenance snapshot; raw CV/source and provider secrets are excluded."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    screening_result_id: str
    screening_run_id: str
    scoring_version: int
    overall_score: Optional[float]
    decision: ScreeningDecision
    evaluated_at: datetime
    resume_profile_id: str
    profile_version: int
    source_parse_revision: int
    source_text_sha256: str
    scorecard_id: Optional[str] = None
    scorecard_version: Optional[int] = None
    ai_model_id: Optional[str] = None
    ai_model_used: Optional[str] = None
    ai_model_version: Optional[str] = None

    _evaluated_at_utc = field_validator("evaluated_at")(ensure_utc)


class ScreeningOverrideResponse(BaseModel):
    id: str
    company_id: str
    application_id: str
    screening_result_id: str
    screening_run_id: str
    resume_file_id: str
    version: int
    base_version: int
    reviewer_id: str
    reason: str
    overall_score: Optional[float]
    decision: ScreeningDecision
    comments: Optional[str]
    created_at: datetime
    ai_base: ScreeningOverrideAIBase


class ScreeningOverrideHistoryResponse(BaseModel):
    items: list[ScreeningOverrideResponse]
    total: int
    page: int
    size: int


class EffectiveScreeningAssessment(BaseModel):
    source: Literal["ai", "hr_override"]
    overall_score: Optional[float]
    decision: ScreeningDecision
    reason: Optional[str] = None
    comments: Optional[str] = None
    override_id: Optional[str] = None
    override_version: Optional[int] = None
    reviewer_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class ScreeningAssessmentResponse(BaseModel):
    """Effective assessment applies only to this result, including historical reads."""
    ai: ScreeningResultResponse
    effective: EffectiveScreeningAssessment
    latest_override: Optional[ScreeningOverrideResponse]
    override_revision: int
    source_is_current: Optional[bool]
    is_latest_result: bool
    review_required: bool

