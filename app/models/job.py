from datetime import datetime, timezone
from typing import List, Optional
from beanie import Document
from pydantic import Field
from pymongo import IndexModel

class Job(Document):
    job_title: str = Field(..., max_length=100, description="Job title")
    description: str = Field(..., description="Detailed job description")
    requirements: Optional[str] = Field(None, description="Additional requirements")
    skills_required: List[str] = Field(..., description="List of required skills")
    location: Optional[str] = Field(None, max_length=100, description="Job location or remote")
    salary_min: Optional[int] = Field(None, description="Minimum salary")
    salary_max: Optional[int] = Field(None, description="Maximum salary")
    company_name: Optional[str] = Field(None, max_length=100, description="Company name")
    employer_id: Optional[str] = Field(None, description="ID of the user who posted the job")
    active: bool = Field(default=True, description="Is the job post still open?")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "jobs"
        indexes = [
            IndexModel([("employer_id", 1)], name="idx_jobs_employer_id"),
            IndexModel([("job_title", 1)], name="idx_jobs_job_title"),
            IndexModel([("active", 1)], name="idx_jobs_active"),
            IndexModel([("skills_required", 1)], name="idx_jobs_skills_required"),
            IndexModel([("created_at", -1)], name="idx_jobs_created_at_desc"),
        ]

    class Config:
        arbitrary_types_allowed = True