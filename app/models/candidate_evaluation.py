from pydantic import Field
from beanie import Document
from bson import ObjectId
from datetime import datetime
from app.utils.time import now_utc
from typing import Optional
class CandidateEvaluation(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    job_posting_id: ObjectId = Field(..., description="ID of the job posting")
    summary: Optional[str] = Field(None, description="Summary of the candidate evaluation")
    score: Optional[float] = Field(None, description="Score assigned to the candidate")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "candidate_evaluations"
        indexes = [
            [("user_id", 1)],
            [("job_posting_id", 1)],
            [("created_at", -1)],
            [("user_id", 1), ("job_posting_id", 1)],
        ]
    class Config:
        arbitrary_types_allowed = True