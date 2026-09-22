from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class CandidateStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    MERGED = "merged"


class Candidate(Document):
    """Tenant-scoped identity for an applicant, independent of a login account."""

    company_id: PydanticObjectId
    user_id: Optional[PydanticObjectId] = None
    full_name: str = Field(..., min_length=1, max_length=150)
    email: Optional[str] = Field(None, max_length=320)
    normalized_email: Optional[str] = Field(None, max_length=320)
    phone_number: Optional[str] = Field(None, max_length=32)
    normalized_phone: Optional[str] = Field(None, max_length=32)
    location: Optional[str] = Field(None, max_length=200)
    source: str = Field("manual", max_length=50)
    status: CandidateStatus = CandidateStatus.ACTIVE
    tags: List[str] = Field(default_factory=list, max_length=50)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_by: PydanticObjectId
    updated_by: Optional[PydanticObjectId] = None
    merged_into_id: Optional[PydanticObjectId] = None
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[PydanticObjectId] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @field_validator("email", "normalized_email", mode="before")
    @classmethod
    def normalize_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip().lower()
        return value or None

    @field_validator("phone_number", mode="before")
    @classmethod
    def clean_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def populate_normalized_fields(self) -> "Candidate":
        self.normalized_email = self.email.lower() if self.email else None
        if self.phone_number:
            prefix = "+" if self.phone_number.startswith("+") else ""
            digits = "".join(ch for ch in self.phone_number if ch.isdigit())
            self.normalized_phone = f"{prefix}{digits}" or None
        else:
            self.normalized_phone = None
        return self

    class Settings:
        name = "candidates"
        indexes = [
            IndexModel(
                [("company_id", ASCENDING), ("normalized_email", ASCENDING)],
                unique=True,
                name="uq_candidate_company_email_active",
                partialFilterExpression={
                    "normalized_email": {"$type": "string"},
                    "is_deleted": False,
                },
            ),
            IndexModel(
                [("company_id", ASCENDING), ("normalized_phone", ASCENDING)],
                unique=True,
                name="uq_candidate_company_phone_active",
                partialFilterExpression={
                    "normalized_phone": {"$type": "string"},
                    "is_deleted": False,
                },
            ),
            IndexModel([("company_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("merged_into_id", ASCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
