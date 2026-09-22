from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.screening_run import ProcessingStatus


def _object_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    from bson import ObjectId

    if not ObjectId.is_valid(value):
        raise ValueError("Invalid object ID")
    return value


class ResumeResponse(BaseModel):
    id: str
    company_id: str
    candidate_id: str
    original_filename: str
    file_size: int
    mime_type: str
    checksum: str
    status: str
    uploaded_at: datetime
    processed_at: Optional[datetime] = None


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
    parser_version: str
    queued_at: datetime
