from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class ScreeningResultStatus(str, Enum):
    EVALUATED = "evaluated"
    REVIEWED = "reviewed"
    SUPERSEDED = "superseded"


class ScreeningDecision(str, Enum):
    PASS = "pass"
    HOLD = "hold"
    FAIL = "fail"


class ScreeningResult(Document):
    company_id: Optional[PydanticObjectId] = None
    application_id: Optional[PydanticObjectId] = None
    screening_run_id: Optional[PydanticObjectId] = None
    resume_file_id: PydanticObjectId
    job_requirement_id: PydanticObjectId
    scorecard_id: Optional[PydanticObjectId] = None
    ai_model_id: Optional[PydanticObjectId] = None
    evaluator_user_id: Optional[PydanticObjectId] = None
    evaluator_id: Optional[PydanticObjectId] = None  # legacy
    overall_score: float = Field(..., ge=0.0, le=100.0)
    match_percentage: float = Field(..., ge=0.0, le=100.0)
    decision: ScreeningDecision = ScreeningDecision.HOLD
    skill_score: float = Field(0.0, ge=0.0, le=100.0)
    experience_score: float = Field(0.0, ge=0.0, le=100.0)
    education_score: float = Field(0.0, ge=0.0, le=100.0)
    language_score: float = Field(0.0, ge=0.0, le=100.0)
    criteria_scores: Dict[str, float] = Field(default_factory=dict)
    scorecard_snapshot: Dict[str, Any] = Field(default_factory=dict)
    strengths: List[str] = Field(default_factory=list, max_length=100)
    weaknesses: List[str] = Field(default_factory=list, max_length=100)
    missing_skills: List[str] = Field(default_factory=list, max_length=200)
    matched_skills: List[str] = Field(default_factory=list, max_length=200)
    ai_model_used: Optional[str] = None  # legacy snapshot
    ai_model_version: Optional[str] = None
    ai_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    status: ScreeningResultStatus = ScreeningResultStatus.EVALUATED
    notes: Optional[str] = Field(None, max_length=5000)
    evaluated_at: datetime = Field(default_factory=now_utc)
    reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "screening_results"
        indexes = [
            IndexModel(
                [("screening_run_id", ASCENDING)],
                unique=True,
                name="uq_screening_result_run",
                partialFilterExpression={"screening_run_id": {"$type": "objectId"}},
            ),
            IndexModel([("application_id", ASCENDING), ("evaluated_at", DESCENDING)]),
            IndexModel([("job_requirement_id", ASCENDING), ("overall_score", DESCENDING)]),
            IndexModel([("company_id", ASCENDING), ("decision", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("resume_file_id", ASCENDING), ("job_requirement_id", ASCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
