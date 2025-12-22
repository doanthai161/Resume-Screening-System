from typing import Optional
from pydantic import BaseModel
from bson import ObjectId
from datetime import datetime

class CandidateEvaluationCreate(BaseModel):
    user_id: ObjectId
    job_posting_id: ObjectId
    summary: Optional[str] = None
    score: Optional[float] = None

class CandidateEvaluationUpdate(BaseModel):
    summary: Optional[str]
    score: Optional[float]

class CandidateEvaluationResponse(BaseModel):
    id: str
    user_id: str
    job_posting_id: str
    summary: Optional[str]
    score: Optional[float]
    created_at: datetime
    updated_at: datetime