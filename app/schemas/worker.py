from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.resume_file import ParsedResumeData


class ScreeningOutput(BaseModel):
    overall_score: float = Field(..., ge=0, le=100)
    match_percentage: float = Field(..., ge=0, le=100)
    skill_score: float = Field(0, ge=0, le=100)
    experience_score: float = Field(0, ge=0, le=100)
    education_score: float = Field(0, ge=0, le=100)
    language_score: float = Field(0, ge=0, le=100)
    criteria_scores: Dict[str, float] = Field(default_factory=dict)
    strengths: List[str] = Field(default_factory=list, max_length=100)
    weaknesses: List[str] = Field(default_factory=list, max_length=100)
    missing_skills: List[str] = Field(default_factory=list, max_length=200)
    matched_skills: List[str] = Field(default_factory=list, max_length=200)
    ai_confidence: Optional[float] = Field(None, ge=0, le=1)

    @field_validator("criteria_scores")
    @classmethod
    def validate_criteria_scores(cls, value: Dict[str, float]) -> Dict[str, float]:
        if len(value) > 50:
            raise ValueError("At most 50 criterion scores are allowed")
        if any(len(code) > 80 or score < 0 or score > 100 for code, score in value.items()):
            raise ValueError("Invalid criterion score")
        return value

    @field_validator("strengths", "weaknesses", "missing_skills", "matched_skills")
    @classmethod
    def validate_text_items(cls, values: List[str]) -> List[str]:
        if any(len(value) > 500 for value in values):
            raise ValueError("Screening output items must be at most 500 characters")
        return values


class ParseOutput(BaseModel):
    parsed_data: ParsedResumeData
