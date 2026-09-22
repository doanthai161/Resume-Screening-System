from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class ReviewRecommendation(str, Enum):
    STRONG_YES = "strong_yes"
    YES = "yes"
    HOLD = "hold"
    NO = "no"
    STRONG_NO = "strong_no"


class ApplicationReview(Document):
    company_id: PydanticObjectId
    application_id: PydanticObjectId
    reviewer_id: PydanticObjectId
    review_round: int = Field(1, ge=1)
    score: float = Field(..., ge=0, le=100)
    criteria_scores: Dict[str, float] = Field(default_factory=dict)
    recommendation: ReviewRecommendation
    summary: Optional[str] = Field(None, max_length=5000)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "application_reviews"
        indexes = [
            IndexModel(
                [("application_id", ASCENDING), ("reviewer_id", ASCENDING), ("review_round", ASCENDING)],
                unique=True,
                name="uq_application_reviewer_round",
            ),
            IndexModel([("company_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("application_id", ASCENDING), ("created_at", DESCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
