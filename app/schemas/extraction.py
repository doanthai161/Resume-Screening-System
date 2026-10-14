from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.screening_run import ProcessingStatus
from app.schemas.resume import _object_id
from app.schemas.resume_extraction import ResumeExtractionOutput
from app.schemas.resume_profile_review import HRConfirmedFact
from pydantic import field_validator
from app.utils.time import ensure_utc


class StartExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str
    _ids = field_validator("company_id", mode="before")(_object_id)


class ExtractionRunResponse(BaseModel):
    id: str
    resume_file_id: str
    status: ProcessingStatus
    attempt: int
    max_attempts: int
    source_parse_revision: int
    model_name: str
    prompt_version: str
    queued_at: datetime
    next_retry_at: Optional[datetime]
    result_id: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]


class ExtractionServiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    raw_text: str = Field(..., strict=True, min_length=1, max_length=1_000_000)
    model_name: str = Field(..., min_length=1, max_length=150)
    prompt_version: Literal["resume-extract-v1"]


class ExtractionServiceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    profile: ResumeExtractionOutput
    model_name: str = Field(..., min_length=1, max_length=150)
    prompt_version: Literal["resume-extract-v1"]
    extractor_version: Literal["http-extractor-1.0"]


class ProfileResponse(BaseModel):
    id: str
    extraction_run_id: str
    resume_file_id: str
    version: int
    source_parse_revision: int
    model_name: str
    prompt_version: str
    extractor_version: str
    review_required: bool
    is_current: bool
    created_at: datetime
    profile: ResumeExtractionOutput
    origin: Literal["ai", "hr"] = "ai"
    parent_profile_id: Optional[str] = None
    edited_by: Optional[str] = None
    edit_reason: Optional[str] = None
    hr_annotations: list[HRConfirmedFact] = Field(default_factory=list)
    _created_utc = field_validator("created_at")(ensure_utc)
