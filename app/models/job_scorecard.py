from datetime import datetime
from typing import List, Optional

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field, model_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class ScorecardCriterion(BaseModel):
    code: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = Field(None, max_length=1000)
    weight: float = Field(..., gt=0, le=1)
    minimum_score: float = Field(0, ge=0, le=100)
    required: bool = False


class JobScorecard(Document):
    company_id: PydanticObjectId
    job_requirement_id: PydanticObjectId
    version: int = Field(1, ge=1)
    criteria: List[ScorecardCriterion] = Field(..., min_length=1, max_length=50)
    pass_threshold: float = Field(70, ge=0, le=100)
    is_active: bool = True
    idempotency_key: Optional[str] = Field(None, min_length=8, max_length=128)
    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=now_utc)
    deactivated_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_criteria(self) -> "JobScorecard":
        codes = [criterion.code for criterion in self.criteria]
        if len(codes) != len(set(codes)):
            raise ValueError("Scorecard criterion codes must be unique")
        total_weight = sum(criterion.weight for criterion in self.criteria)
        if abs(total_weight - 1.0) > 0.001:
            raise ValueError("Scorecard criterion weights must add up to 1.0")
        return self

    class Settings:
        name = "job_scorecards"
        indexes = [
            IndexModel(
                [("job_requirement_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="uq_scorecard_job_version",
            ),
            IndexModel(
                [("job_requirement_id", ASCENDING), ("is_active", ASCENDING)],
                unique=True,
                name="uq_scorecard_active_job",
                partialFilterExpression={"is_active": True},
            ),
            IndexModel(
                [("company_id", ASCENDING), ("idempotency_key", ASCENDING)],
                unique=True,
                name="uq_scorecard_idempotency",
                partialFilterExpression={"idempotency_key": {"$type": "string"}},
            ),
            IndexModel([("company_id", ASCENDING), ("created_at", DESCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
