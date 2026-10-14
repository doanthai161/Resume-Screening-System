"""Append-only HR decisions; operational pointers live on ScreeningResult."""
from datetime import datetime
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.screening_result import ScreeningDecision
from app.schemas.screening_override import ScreeningOverrideAIBase, Reason, HRScore
from app.utils.time import now_utc


class ScreeningOverride(Document):
    company_id: PydanticObjectId
    application_id: PydanticObjectId
    screening_result_id: PydanticObjectId
    screening_run_id: PydanticObjectId
    resume_file_id: PydanticObjectId
    version: int = Field(..., ge=1)
    base_version: int = Field(..., ge=0)
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    reviewer_id: PydanticObjectId
    reason: Reason
    overall_score: Optional[HRScore]
    decision: ScreeningDecision
    comments: Optional[str] = Field(None, max_length=5000)
    ai_base: ScreeningOverrideAIBase
    created_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "screening_overrides"
        indexes = [
            IndexModel([("company_id", ASCENDING), ("idempotency_key", ASCENDING)], unique=True,
                       name="uq_screening_override_idempotency"),
            IndexModel([("screening_result_id", ASCENDING), ("version", DESCENDING)], unique=True,
                       name="uq_screening_override_version"),
            IndexModel([("company_id", ASCENDING), ("application_id", ASCENDING), ("created_at", DESCENDING)]),
        ]

