from beanie import Document
from pydantic import Field
from pymongo import IndexModel
from bson import ObjectId
from datetime import datetime
from app.utils.time import now_vn
from typing import Optional, List, Dict, Any


class JobApplication(Document):
    resume_file_id: ObjectId = Field(..., description="ID of the resume file")
    job_requirement_id: ObjectId = Field(..., description="ID of the job requirement")
    applicant_id: Optional[ObjectId] = Field(None, description="ID of the applicant (if registered)")
    applied_by: ObjectId = Field(..., description="ID of the user who applied/submitted the resume")
    current_stage: str = Field("screened", description="screened, interviewed, offered, hired, rejected")
    stages: List[Dict[str, Any]] = Field(default_factory=list, description="History of stages")
    screening_result_id: Optional[ObjectId] = Field(None, description="Reference to screening result")
    source: str = Field("manual_upload", description="manual_upload, website, linkedin, etc")
    notes: Optional[str] = Field(None, description="Notes from recruiter")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())
    
    class Settings:
        name = "job_applications"
        indexes = [
            {"key": [("resume_file_id", 1), ("job_requirement_id", 1)], "name": "idx_applications_resume_job"},
            {"key": [("job_requirement_id", 1)], "name": "idx_applications_job"},
            {"key": [("applicant_id", 1)], "name": "idx_applications_applicant", "sparse": True},
            {"key": [("current_stage", 1)], "name": "idx_applications_stage"},
            {"key": [("created_at", -1)], "name": "idx_applications_created_desc"},
            {"key": [("job_requirement_id", 1), ("current_stage", 1)], "name": "idx_applications_job_stage"},
            {"key": [("applicant_id", 1), ("created_at", -1)], "name": "idx_applications_applicant_recent"},
        ]
    
    class Config:
        arbitrary_types_allowed = True