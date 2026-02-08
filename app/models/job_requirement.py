from datetime import datetime
from app.utils.time import now_vn
from typing import List, Optional
from beanie import Document
from pydantic import Field
from bson import ObjectId


class JobRequirement(Document):
    user_id: ObjectId = Field(..., description="ID of the user who created the job requirement")
    company_branch_id: ObjectId = Field(..., description="ID of the company branch associated with the job requirement")
    title: str = Field(..., description="Title of the job requirement")
    programming_languages: List[str] = Field(..., description="List of required programming languages")
    skills_required: List[str] = Field(..., description="List of required skills")
    experience_level: str = Field(..., description="Experience level required for the job")
    description: Optional[str] = Field(None, description="Detailed description of the job requirement")
    salary_min: Optional[int] = Field(None, description="Minimum salary for the job")
    salary_max: Optional[int] = Field(None, description="Maximum salary for the job")
    expiration_time: Optional[datetime] = Field(None, description="Expiration date of the job requirement")
    is_open: bool = Field(default=True, description="Is the job requirement open?")
    is_active: bool = Field(default=True, description="Is the job requirement active?")
    screening_model_id: Optional[ObjectId] = Field(None, description="AI model used to screen")
    auto_screening_enabled: bool = Field(True, description="Is auto screening enabled?")
    screening_threshold: float = Field(70.0, ge=0.0, le=100.0, description="Threshold score to pass screening")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "job_requirements"
        indexes = [
            [("user_id", 1)],
            [("company_branch_id", 1)],
            [("title", 1)],
            [("is_open", 1)],
            [("is_active", 1)],
            [("expiration_time", 1)],
            [("created_at", -1)],
            [("company_branch_id", 1), ("is_open", 1)],
            [("company_branch_id", 1), ("is_active", 1)],
        ]

    class Config:
        arbitrary_types_allowed = True
        