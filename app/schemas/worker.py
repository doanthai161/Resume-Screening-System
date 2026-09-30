from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.resume_file import ParsedResumeData
from app.schemas.scoring import CriterionEvaluation


class ScreeningOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria_evaluations: Dict[str, CriterionEvaluation] = Field(..., max_length=50)
    strengths: List[str] = Field(default_factory=list, max_length=100)
    weaknesses: List[str] = Field(default_factory=list, max_length=100)
    missing_skills: List[str] = Field(default_factory=list, max_length=200)
    matched_skills: List[str] = Field(default_factory=list, max_length=200)
    ai_confidence: Optional[float] = Field(None, ge=0, le=1)

    @field_validator("criteria_evaluations")
    @classmethod
    def validate_codes(cls, values):
        if any(not code or len(code) > 80 for code in values):
            raise ValueError("Invalid criterion code")
        return values

    @field_validator("strengths", "weaknesses", "missing_skills", "matched_skills")
    @classmethod
    def validate_text_items(cls, values: List[str]) -> List[str]:
        if any(len(value) > 500 for value in values):
            raise ValueError("Screening output items must be at most 500 characters")
        return values


class ParseOutput(BaseModel):
    parsed_data: ParsedResumeData
    provider: str = Field("unknown", min_length=1, max_length=50)
    ocr_used: bool = False
    quality_score: float = Field(0.0, ge=0.0, le=1.0)
    fallback_reason: Optional[str] = Field(None, max_length=100)
