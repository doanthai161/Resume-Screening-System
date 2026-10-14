import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.schemas.screening_override import ScreeningOverrideCreate


def body():
    return {"company_id": str(ObjectId()), "expected_version": 0, "overall_score": 85,
            "decision": "pass", "reason": " HR đã xác minh dự án với ứng viên. "}


def test_override_requires_reason_and_preserves_explicit_unresolved_assessment():
    data = body()
    result = ScreeningOverrideCreate.model_validate(data)
    assert result.reason == "HR đã xác minh dự án với ứng viên."
    assert result.overall_score == 85
    data.update(overall_score=None, decision="hold")
    assert ScreeningOverrideCreate.model_validate(data).overall_score is None


@pytest.mark.parametrize("field,value", [
    ("reason", "   "), ("reason", None), ("reason", 123),
    ("overall_score", "85"), ("overall_score", True), ("overall_score", -1),
    ("overall_score", 101), ("overall_score", float("nan")), ("overall_score", float("inf")),
    ("expected_version", True), ("expected_version", -1), ("expected_version", 1.0),
    ("decision", "strong_yes"), ("company_id", "invalid"),
])
def test_invalid_override_values_are_rejected(field, value):
    data = body()
    data[field] = value
    with pytest.raises(ValidationError):
        ScreeningOverrideCreate.model_validate(data)


@pytest.mark.parametrize("decision", ["pass", "fail"])
def test_final_override_decision_requires_a_score(decision):
    data = body()
    data.update(overall_score=None, decision=decision)
    with pytest.raises(ValidationError):
        ScreeningOverrideCreate.model_validate(data)


@pytest.mark.parametrize("field", ["reviewer_id", "version", "latest_override_id", "ai_base", "source_snapshot"])
def test_client_cannot_supply_internal_override_fields(field):
    data = body()
    data[field] = str(ObjectId())
    with pytest.raises(ValidationError):
        ScreeningOverrideCreate.model_validate(data)
