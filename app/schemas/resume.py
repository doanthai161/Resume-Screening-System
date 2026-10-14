from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.models.screening_run import ProcessingStatus


def _object_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    from bson import ObjectId

    if not ObjectId.is_valid(value):
        raise ValueError("Invalid object ID")
    return value


class ResumeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(validation_alias=AliasChoices("id", "_id"))
    company_id: str
    candidate_id: Optional[str] = None
    company_branch_id: Optional[str] = None
    original_filename: str
    file_size: int
    mime_type: str
    checksum: str
    status: str
    uploaded_at: datetime
    processed_at: Optional[datetime] = None
    parse_revision: int = 0
    latest_parse_run_id: Optional[str] = None
    latest_extraction_profile_id: Optional[str] = None

    @field_validator("id", "company_id", "candidate_id", "company_branch_id",
                     "latest_parse_run_id", "latest_extraction_profile_id", mode="before")
    @classmethod
    def stringify_ids(cls, value):
        return str(value) if value is not None else None


class ResumeListResponse(BaseModel):
    items: list[ResumeResponse]
    total: int
    page: int
    size: int


class ParseResumeRequest(BaseModel):
    company_id: str
    parser_model_id: Optional[str] = None
    parser_version: str = Field("1.0.0", min_length=1, max_length=50)

    _ids = field_validator("company_id", "parser_model_id", mode="before")(_object_id)


class ParseRunResponse(BaseModel):
    id: str
    company_id: str
    resume_file_id: str
    parser_model_id: Optional[str]
    status: ProcessingStatus
    attempt: int
    max_attempts: int
    deferred_attempts: int = 0
    next_retry_at: Optional[datetime] = None
    parser_version: str
    queued_at: datetime
    final_provider: Optional[str] = None
    ocr_used: bool = False
    quality_score: Optional[float] = None
    fallback_reason: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
