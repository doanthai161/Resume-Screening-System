"""Evidence-based worker contract; aggregate scores belong to the backend."""
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from beanie import PydanticObjectId

from app.schemas.resume_extraction import MAX_SOURCE_CHARACTERS


class CriterionStatus(str, Enum):
    EVALUATED = "evaluated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class CriterionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", hide_input_in_errors=True)
    page: Optional[int] = Field(None, ge=1)
    # Quotes preserve whitespace and offsets count Unicode characters, not bytes.
    text: str = Field(..., strict=True, min_length=1, max_length=2000)
    start: Optional[int] = Field(None, strict=True, ge=0, lt=MAX_SOURCE_CHARACTERS)
    end: Optional[int] = Field(None, strict=True, gt=0, le=MAX_SOURCE_CHARACTERS)
    origin: Literal["cv", "hr"] = "cv"
    hr_fact_id: Optional[PydanticObjectId] = None

    @model_validator(mode="after")
    def validate_span(self):
        if not self.text.strip():
            raise ValueError("Screening evidence requires a nonblank quote")
        if self.origin == "hr":
            if not self.hr_fact_id or self.page is not None or self.start is not None or self.end is not None:
                raise ValueError("HR evidence requires a fact ID and cannot claim CV offsets or pages")
            return self
        if self.hr_fact_id is not None:
            raise ValueError("CV evidence cannot reference an HR confirmation")
        if (self.start is None) != (self.end is None):
            raise ValueError("Screening evidence requires both span offsets or neither")
        if self.start is not None and self.end - self.start != len(self.text):
            raise ValueError("Screening evidence span length must match its quote")
        return self


class CriterionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True,
                             revalidate_instances="always", hide_input_in_errors=True)
    status: CriterionStatus
    score: Optional[float] = Field(None, ge=0, le=100, allow_inf_nan=False)
    reason: str = Field(..., min_length=1, max_length=2000)
    evidence: list[CriterionEvidence] = Field(default_factory=list, max_length=10)
    verification_question: Optional[str] = Field(None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_evaluation(self):
        if self.status == CriterionStatus.EVALUATED:
            if self.score is None or not self.evidence:
                raise ValueError("Evaluated criteria require a score and evidence")
        elif self.score is not None:
            raise ValueError("Unresolved criteria must have a null score")
        return self
