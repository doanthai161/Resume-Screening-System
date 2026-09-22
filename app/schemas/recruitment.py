from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.application_review import ReviewRecommendation
from app.models.job_application import ApplicationStage, ApplicationStatus
from app.models.job_scorecard import ScorecardCriterion
from app.models.screening_run import ProcessingStatus


def _validate_object_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    from bson import ObjectId

    if not ObjectId.is_valid(value):
        raise ValueError("Invalid object ID")
    return value


class ApplicationCreate(BaseModel):
    company_id: str
    candidate_id: str
    resume_file_id: str
    job_requirement_id: str
    source: str = Field("manual_upload", max_length=50)
    assigned_recruiter_id: Optional[str] = None

    _ids = field_validator(
        "company_id",
        "candidate_id",
        "resume_file_id",
        "job_requirement_id",
        "assigned_recruiter_id",
        mode="before",
    )(_validate_object_id)


class StageTransitionRequest(BaseModel):
    to_stage: ApplicationStage
    reason: Optional[str] = Field(None, max_length=1000)


class ApplicationResponse(BaseModel):
    id: str
    company_id: str
    company_branch_id: str
    candidate_id: str
    resume_file_id: str
    job_requirement_id: str
    applicant_id: Optional[str] = None
    applied_by: str
    current_stage: ApplicationStage
    status: ApplicationStatus
    source: str
    assigned_recruiter_id: Optional[str] = None
    latest_screening_result_id: Optional[str] = None
    applied_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationListResponse(BaseModel):
    items: List[ApplicationResponse]
    total: int
    page: int
    size: int


class ScorecardCreate(BaseModel):
    company_id: str
    job_requirement_id: str
    criteria: List[ScorecardCriterion] = Field(..., min_length=1, max_length=50)
    pass_threshold: float = Field(70, ge=0, le=100)

    _ids = field_validator("company_id", "job_requirement_id", mode="before")(_validate_object_id)


class ScorecardResponse(BaseModel):
    id: str
    company_id: str
    job_requirement_id: str
    version: int
    criteria: List[ScorecardCriterion]
    pass_threshold: float
    is_active: bool
    created_by: str
    created_at: datetime


class ScreeningStartRequest(BaseModel):
    company_id: str
    application_id: str
    ai_model_id: str

    _ids = field_validator("company_id", "application_id", "ai_model_id", mode="before")(_validate_object_id)


class ScreeningRunResponse(BaseModel):
    id: str
    company_id: str
    application_id: str
    resume_file_id: str
    job_requirement_id: str
    scorecard_id: str
    ai_model_id: str
    status: ProcessingStatus
    attempt: int
    max_attempts: int
    queued_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ApplicationReviewCreate(BaseModel):
    company_id: str
    application_id: str
    review_round: int = Field(1, ge=1)
    score: float = Field(..., ge=0, le=100)
    criteria_scores: Dict[str, float] = Field(default_factory=dict)
    recommendation: ReviewRecommendation
    summary: Optional[str] = Field(None, max_length=5000)

    _ids = field_validator("company_id", "application_id", mode="before")(_validate_object_id)

    @field_validator("criteria_scores")
    @classmethod
    def validate_criteria_scores(cls, value: Dict[str, float]) -> Dict[str, float]:
        if len(value) > 50:
            raise ValueError("At most 50 criterion scores are allowed")
        if any(len(code) > 80 or score < 0 or score > 100 for code, score in value.items()):
            raise ValueError("Criterion codes must be <= 80 characters and scores between 0 and 100")
        return value


class ApplicationReviewResponse(BaseModel):
    id: str
    company_id: str
    application_id: str
    reviewer_id: str
    review_round: int
    score: float
    criteria_scores: Dict[str, float]
    recommendation: ReviewRecommendation
    summary: Optional[str]
    created_at: datetime
    updated_at: datetime
