"""Durable extraction input and immutable, versioned profile results."""
from datetime import datetime
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.screening_run import ProcessingStatus
from app.schemas.resume_extraction import ResumeExtractionOutput
from app.utils.time import now_utc

PROMPT_VERSION = "resume-extract-v1"
EXTRACTOR_VERSION = "http-extractor-1.0"


class ResumeExtractionRun(Document):
    company_id: PydanticObjectId
    resume_file_id: PydanticObjectId
    triggered_by: PydanticObjectId
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    input_hash: str = Field(..., min_length=64, max_length=64)
    source_text: str = Field(..., min_length=1, max_length=1_000_000)
    source_text_sha256: str = Field(..., min_length=64, max_length=64)
    source_parse_revision: int = Field(..., ge=0)
    source_parse_run_id: Optional[PydanticObjectId] = None
    model_name: str = Field(..., min_length=1, max_length=150)
    prompt_version: str = PROMPT_VERSION
    schema_version: str = "1.0"
    status: ProcessingStatus = ProcessingStatus.QUEUED
    is_terminal: bool = False
    attempt: int = Field(0, ge=0)
    max_attempts: int = Field(3, ge=1, le=10)
    next_retry_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    queue_message_id: Optional[str] = None
    last_enqueued_at: Optional[datetime] = None
    result_id: Optional[PydanticObjectId] = None
    error_code: Optional[str] = Field(None, max_length=100)
    error_message: Optional[str] = Field(None, max_length=2000)
    queued_at: datetime = Field(default_factory=now_utc)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "resume_extraction_runs"
        indexes = [
            IndexModel([("company_id", ASCENDING), ("idempotency_key", ASCENDING)], unique=True),
            IndexModel([("resume_file_id", ASCENDING)], unique=True,
                       partialFilterExpression={"is_terminal": False}, name="uq_active_extraction_resume"),
            IndexModel([("status", ASCENDING), ("next_retry_at", ASCENDING), ("lease_expires_at", ASCENDING)]),
            IndexModel([("resume_file_id", ASCENDING), ("queued_at", DESCENDING)]),
        ]


class ResumeProfile(Document):
    company_id: PydanticObjectId
    resume_file_id: PydanticObjectId
    extraction_run_id: PydanticObjectId
    version: int = Field(..., ge=1)
    source_parse_revision: int = Field(..., ge=0)
    source_parse_run_id: Optional[PydanticObjectId] = None
    model_name: str
    prompt_version: str
    extractor_version: str
    profile: ResumeExtractionOutput
    review_required: bool
    created_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "resume_profiles"
        indexes = [
            IndexModel([("extraction_run_id", ASCENDING)], unique=True),
            IndexModel([("resume_file_id", ASCENDING), ("version", DESCENDING)], unique=True),
            IndexModel([("company_id", ASCENDING), ("resume_file_id", ASCENDING)]),
        ]
