"""Evidence-based worker contract; aggregate scores belong to the backend."""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CriterionStatus(str, Enum):
    EVALUATED = "evaluated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class CriterionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    page: Optional[int] = Field(None, ge=1)
    text: str = Field(..., min_length=1, max_length=2000)


class CriterionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
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
