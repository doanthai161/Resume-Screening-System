from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class ApplicationStage(str, Enum):
    APPLIED = "applied"
    SCREENING = "screening"
    SCREENED = "screened"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ApplicationStatus(str, Enum):
    ACTIVE = "active"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class JobApplication(Document):
    # company_id/candidate_id are optional only for loading legacy documents.
    # All new writes through ApplicationService require both values.
    company_id: Optional[PydanticObjectId] = None
    company_branch_id: Optional[PydanticObjectId] = None
    candidate_id: Optional[PydanticObjectId] = None
    resume_file_id: PydanticObjectId
    job_requirement_id: PydanticObjectId
    applicant_id: Optional[PydanticObjectId] = None
    applied_by: PydanticObjectId
    current_stage: ApplicationStage = ApplicationStage.APPLIED
    status: ApplicationStatus = ApplicationStatus.ACTIVE
    # Kept for safe reads of legacy records; new history is append-only in
    # application_stage_events.
    stages: List[Dict[str, Any]] = Field(default_factory=list, exclude=True)
    latest_screening_result_id: Optional[PydanticObjectId] = None
    source: str = Field("manual_upload", max_length=50)
    assigned_recruiter_id: Optional[PydanticObjectId] = None
    notes: Optional[str] = Field(None, max_length=5000)
    rejection_reason: Optional[str] = Field(None, max_length=1000)
    idempotency_key: Optional[str] = Field(None, min_length=8, max_length=128)
    revision: int = Field(0, ge=0)
    applied_at: datetime = Field(default_factory=now_utc)
    withdrawn_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    hired_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "job_applications"
        indexes = [
            IndexModel(
                [("company_id", ASCENDING), ("idempotency_key", ASCENDING)],
                unique=True,
                name="uq_application_idempotency",
                partialFilterExpression={"idempotency_key": {"$type": "string"}},
            ),
            IndexModel(
                [("job_requirement_id", ASCENDING), ("candidate_id", ASCENDING)],
                unique=True,
                name="uq_application_job_candidate",
                partialFilterExpression={"candidate_id": {"$type": "objectId"}},
            ),
            IndexModel([("resume_file_id", ASCENDING)]),
            IndexModel([("candidate_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("company_id", ASCENDING), ("current_stage", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("job_requirement_id", ASCENDING), ("current_stage", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("assigned_recruiter_id", ASCENDING), ("status", ASCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
