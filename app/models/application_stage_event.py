from datetime import datetime
from typing import Any, Dict, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.job_application import ApplicationStage
from app.utils.time import now_utc


class ApplicationStageEvent(Document):
    """Append-only audit trail for application stage transitions."""

    company_id: PydanticObjectId
    application_id: PydanticObjectId
    from_stage: Optional[ApplicationStage] = None
    to_stage: ApplicationStage
    changed_by: PydanticObjectId
    reason: Optional[str] = Field(None, max_length=1000)
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "application_stage_events"
        indexes = [
            IndexModel(
                [("company_id", ASCENDING), ("idempotency_key", ASCENDING)],
                unique=True,
                name="uq_stage_event_idempotency",
            ),
            IndexModel([("application_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("company_id", ASCENDING), ("to_stage", ASCENDING), ("created_at", DESCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
