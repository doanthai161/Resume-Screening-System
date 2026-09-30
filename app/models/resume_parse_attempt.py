from datetime import datetime
from enum import Enum
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class ParseAttemptStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ResumeParseAttempt(Document):
    """A sanitized record of one provider invocation within a parse run."""

    run_id: PydanticObjectId
    company_id: PydanticObjectId
    run_attempt: int = Field(..., ge=1)
    sequence: int = Field(..., ge=1, le=20)
    provider: str = Field(..., min_length=1, max_length=50)
    mode: Optional[str] = Field(None, max_length=30)
    tier: Optional[str] = Field(None, max_length=30)
    status: ParseAttemptStatus = ParseAttemptStatus.RUNNING
    page_count: int = Field(0, ge=0, le=10000)
    output_characters: int = Field(0, ge=0, le=2_000_000)
    quality_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    quality_reason: Optional[str] = Field(None, max_length=100)
    error_code: Optional[str] = Field(None, max_length=100)
    duration_ms: Optional[int] = Field(None, ge=0)
    started_at: datetime = Field(default_factory=now_utc)
    finished_at: Optional[datetime] = None

    class Settings:
        name = "resume_parse_attempts"
        indexes = [
            IndexModel(
                [
                    ("run_id", ASCENDING),
                    ("run_attempt", ASCENDING),
                    ("sequence", ASCENDING),
                ],
                unique=True,
                name="uq_parse_attempt_sequence",
            ),
            IndexModel([("company_id", ASCENDING), ("started_at", DESCENDING)]),
            IndexModel([("status", ASCENDING), ("started_at", DESCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
