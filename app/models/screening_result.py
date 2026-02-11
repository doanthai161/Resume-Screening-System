from beanie import Document
from pydantic import Field
from bson import ObjectId
from datetime import datetime
from app.utils.time import now_utc
from typing import Optional, List


class CategoryScores(Document):
    skills: float = Field(0.0, ge=0.0, le=100.0)
    experience: float = Field(0.0, ge=0.0, le=100.0)
    education: float = Field(0.0, ge=0.0, le=100.0)
    certifications: float = Field(0.0, ge=0.0, le=100.0)
    overall_fit: float = Field(0.0, ge=0.0, le=100.0)

    class Settings:
        name = "category_scores"
        indexes = [
            [("skills", 1)],
            [("experience", 1)],
            [("education", 1)],
            [("certifications", 1)],
            [("overall_fit", 1)],
        ]
    class Config:
        arbitrary_types_allowed = True

class ScreeningResult(Document):
    """Kết quả đánh giá CV với Job Requirement"""
    resume_file_id: ObjectId = Field(..., description="ID file CV")
    job_requirement_id: ObjectId = Field(..., description="ID job requirement")
    evaluator_id: ObjectId = Field(..., description="ID người/ai đánh giá")
    
    overall_score: float = Field(..., ge=0.0, le=100.0, description="Overall score")
    match_percentage: float = Field(..., ge=0.0, le=100.0, description="Match percentage")
    
    # Detailed scores
    skill_score: float = Field(0.0, ge=0.0, le=100.0)
    experience_score: float = Field(0.0, ge=0.0, le=100.0)
    education_score: float = Field(0.0, ge=0.0, le=100.0)
    language_score: float = Field(0.0, ge=0.0, le=100.0)
    
    # Analysis
    strengths: List[str] = Field(default_factory=list, description="Strengths")
    weaknesses: List[str] = Field(default_factory=list, description="Weaknesses")
    missing_skills: List[str] = Field(default_factory=list, description="Missing skills")
    matched_skills: List[str] = Field(default_factory=list, description="Matched skills")
    
    # AI Metadata
    ai_model_used: Optional[str] = Field(None, description="AI model used")
    ai_model_version: Optional[str] = Field(None)
    ai_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    
    # Status
    status: str = Field("evaluated", description="evaluated, reviewed, hired, rejected")
    notes: Optional[str] = Field(None, description="Notes from recruiter")
    
    # Timestamps
    evaluated_at: datetime = Field(default_factory=lambda: now_utc())
    reviewed_at: Optional[datetime] = Field(None)
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())
    
    class Settings:
        name = "screening_results"
        indexes = [
            [("resume_file_id", 1)],
            [("job_requirement_id", 1)],
            [("evaluator_id", 1)],
            [("overall_score", -1)],
            [("status", 1)],
            [("evaluated_at", -1)],
            [("created_at", -1)],
            [("resume_file_id", 1), ("job_requirement_id", 1)],
            [("job_requirement_id", 1), ("overall_score", -1)],
            [("job_requirement_id", 1), ("status", 1)],
        ]
    
    class Config:
        arbitrary_types_allowed = True