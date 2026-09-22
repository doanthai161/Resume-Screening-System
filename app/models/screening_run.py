from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class ProcessingStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScreeningRun(Document):
    company_id: PydanticObjectId
    application_id: PydanticObjectId
    resume_file_id: PydanticObjectId
    job_requirement_id: PydanticObjectId
    scorecard_id: PydanticObjectId
    ai_model_id: PydanticObjectId
    triggered_by: PydanticObjectId
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    input_hash: str = Field(..., min_length=32, max_length=128)
    config_snapshot: Dict[str, Any] = Field(default_factory=dict)
    status: ProcessingStatus = ProcessingStatus.QUEUED
    is_terminal: bool = False
    attempt: int = Field(0, ge=0)
    max_attempts: int = Field(3, ge=1, le=10)
    error_code: Optional[str] = Field(None, max_length=100)
    error_message: Optional[str] = Field(None, max_length=2000)
    worker_id: Optional[str] = Field(None, max_length=200)
    lease_expires_at: Optional[datetime] = None
    queue_message_id: Optional[str] = Field(None, max_length=200)
    last_enqueued_at: Optional[datetime] = None
    result_id: Optional[PydanticObjectId] = None
    queued_at: datetime = Field(default_factory=now_utc)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "screening_runs"
        indexes = [
            IndexModel(
                [("company_id", ASCENDING), ("idempotency_key", ASCENDING)],
                unique=True,
                name="uq_screening_run_idempotency",
            ),
            IndexModel([("application_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel(
                [("application_id", ASCENDING), ("is_terminal", ASCENDING)],
                unique=True,
                name="uq_active_screening_application",
                partialFilterExpression={"is_terminal": False},
            ),
            IndexModel(
                [("company_id", ASCENDING), ("input_hash", ASCENDING), ("is_terminal", ASCENDING)],
                unique=True,
                name="uq_active_screening_input",
                partialFilterExpression={"is_terminal": False},
            ),
            IndexModel([("company_id", ASCENDING), ("status", ASCENDING), ("queued_at", ASCENDING)]),
            IndexModel([("input_hash", ASCENDING), ("created_at", DESCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True


class ResumeParseRun(Document):
    company_id: PydanticObjectId
    resume_file_id: PydanticObjectId
    parser_model_id: Optional[PydanticObjectId] = None
    triggered_by: PydanticObjectId
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    input_hash: str = Field(..., min_length=32, max_length=128)
    parser_version: str = Field(..., min_length=1, max_length=50)
    status: ProcessingStatus = ProcessingStatus.QUEUED
    is_terminal: bool = False
    attempt: int = Field(0, ge=0)
    max_attempts: int = Field(3, ge=1, le=10)
    error_code: Optional[str] = Field(None, max_length=100)
    error_message: Optional[str] = Field(None, max_length=2000)
    worker_id: Optional[str] = Field(None, max_length=200)
    lease_expires_at: Optional[datetime] = None
    queue_message_id: Optional[str] = Field(None, max_length=200)
    last_enqueued_at: Optional[datetime] = None
    queued_at: datetime = Field(default_factory=now_utc)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "resume_parse_runs"
        indexes = [
            IndexModel(
                [("company_id", ASCENDING), ("idempotency_key", ASCENDING)],
                unique=True,
                name="uq_parse_run_idempotency",
            ),
            IndexModel([("resume_file_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel(
                [("company_id", ASCENDING), ("input_hash", ASCENDING), ("is_terminal", ASCENDING)],
                unique=True,
                name="uq_active_parse_input",
                partialFilterExpression={"is_terminal": False},
            ),
            IndexModel([("company_id", ASCENDING), ("status", ASCENDING), ("queued_at", ASCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
