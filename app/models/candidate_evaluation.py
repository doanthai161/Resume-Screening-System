from pydantic import Field
from beanie import Document
from bson import ObjectId
from datetime import datetime
from app.utils.time import now_vn
from typing import Optional
from pymongo import IndexModel

class CandidateEvaluation(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    job_posting_id: ObjectId = Field(..., description="ID of the job posting")
    summary: Optional[str] = Field(None, description="Summary of the candidate evaluation")
    score: Optional[float] = Field(None, description="Score assigned to the candidate")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "candidate_evaluations"
        indexes = [
            IndexModel([("user_id", 1)], name="idx_candidate_evaluations_user_id"),
            IndexModel([("job_posting_id", 1)], name="idx_candidate_evaluations_job_posting_id"),
        ]
    class Config:
        arbitrary_types_allowed = True