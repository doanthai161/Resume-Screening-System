from datetime import datetime
from enum import Enum
from typing import List, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field, model_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class JobStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED = "paused"
    CLOSED = "closed"
    ARCHIVED = "archived"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"


class WorkMode(str, Enum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"


class JobRequirement(Document):
    user_id: PydanticObjectId = Field(..., description="Creator user ID")
    company_branch_id: PydanticObjectId
    title: str = Field(..., min_length=1, max_length=200)
    programming_languages: List[str] = Field(default_factory=list, max_length=100)
    skills_required: List[str] = Field(default_factory=list, max_length=200)
    experience_level: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=20000)
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    work_mode: WorkMode = WorkMode.ONSITE
    location: Optional[str] = Field(None, max_length=200)
    number_of_openings: int = Field(1, ge=1, le=10000)
    salary_min: Optional[int] = Field(None, ge=0)
    salary_max: Optional[int] = Field(None, ge=0)
    salary_currency: Optional[str] = Field(None, min_length=3, max_length=3)
    salary_period: Optional[str] = Field(None, pattern=r"^(hour|day|month|year)$")
    expiration_time: Optional[datetime] = None
    status: JobStatus = JobStatus.DRAFT
    # Compatibility fields for existing APIs; status is the new source of truth.
    is_open: bool = False
    is_active: bool = True
    screening_model_id: Optional[PydanticObjectId] = None
    active_scorecard_id: Optional[PydanticObjectId] = None
    auto_screening_enabled: bool = True
    screening_threshold: float = Field(70.0, ge=0.0, le=100.0)
    version: int = Field(1, ge=1)
    published_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    updated_by: Optional[PydanticObjectId] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @model_validator(mode="after")
    def validate_business_rules(self) -> "JobRequirement":
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("salary_min must be less than or equal to salary_max")
        if bool(self.salary_min is not None or self.salary_max is not None) and not self.salary_currency:
            raise ValueError("salary_currency is required when a salary range is provided")
        self.salary_currency = self.salary_currency.upper() if self.salary_currency else None
        self.is_open = self.status == JobStatus.PUBLISHED
        self.is_active = self.status != JobStatus.ARCHIVED
        return self

    class Settings:
        name = "job_requirements"
        indexes = [
            IndexModel([("company_branch_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("expiration_time", ASCENDING)]),
            IndexModel([("skills_required", ASCENDING)]),
            IndexModel([("screening_model_id", ASCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
