"""Deterministic scoring from the immutable run scorecard snapshot."""
from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, Field, model_validator

from app.models.job_scorecard import ScorecardCriterion
from app.schemas.scoring import CriterionEvaluation, CriterionStatus


class ScoringSnapshot(BaseModel):
    criteria: list[ScorecardCriterion] = Field(..., min_length=1, max_length=50)
    pass_threshold: float = Field(..., ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_weights(self):
        if len({item.code for item in self.criteria}) != len(self.criteria):
            raise ValueError("Duplicate scorecard criterion")
        total = sum(Decimal(str(item.weight)) for item in self.criteria)
        if abs(total - 1) > Decimal("0.001"):
            raise ValueError("Scorecard weights must sum to 1")
        return self


def calculate_screening(snapshot: dict, evaluations: dict[str, CriterionEvaluation]) -> dict:
    card = ScoringSnapshot.model_validate(snapshot)
    codes = {item.code for item in card.criteria}
    if evaluations.keys() - codes:
        raise ValueError("Output contains criteria outside the scorecard snapshot")
    resolved = {
        item.code: evaluations.get(item.code) or CriterionEvaluation(
            status=CriterionStatus.INSUFFICIENT_EVIDENCE,
            reason="Chưa đủ dữ liệu: worker chưa cung cấp đánh giá tiêu chí này.",
        )
        for item in card.criteria
    }
    total_weight = sum(Decimal(str(item.weight)) for item in card.criteria)
    covered = Decimal(0)
    points = Decimal(0)
    required_failed = False
    complete = True
    for item in card.criteria:
        evaluation = resolved[item.code]
        weight = Decimal(str(item.weight)) / total_weight
        if evaluation.status != CriterionStatus.EVALUATED:
            complete = False
            continue
        covered += weight
        points += weight * Decimal(str(evaluation.score))
        if item.required and evaluation.score < item.minimum_score:
            required_failed = True
    # Stored totals use two decimals; use the same total for threshold comparison.
    rounded_points = points.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Any unresolved criterion requires human review, even if bounds imply failure.
    decision = "hold"
    if complete:
        decision = "pass" if rounded_points >= Decimal(str(card.pass_threshold)) and not required_failed else "fail"
    lower = max(0.0, min(100.0, float(rounded_points)))
    upper = max(lower, min(100.0, float(
        (points + (1 - covered) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )))
    return {
        "scoring_version": 2,
        "overall_score": lower if complete else None,
        "match_percentage": None,  # A rubric score is not a probability of suitability.
        "decision": decision,
        "evidence_coverage": min(100.0, round(float(covered * 100), 2)),
        "score_lower_bound": lower,
        "score_upper_bound": upper,
        "criteria_evaluations": resolved,
        "criteria_scores": {code: item.score for code, item in resolved.items()},
    }
