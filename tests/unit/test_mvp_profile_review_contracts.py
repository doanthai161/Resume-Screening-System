"""HR revision and confirmation contracts; no inference or database required."""
import copy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from bson import ObjectId
from pydantic import ValidationError
import pytest

from app.core.resume_extraction import validate_resume_extraction
from app.core.screening_evidence import bind_screening_output, validate_screening_source
from app.models.resume_extraction import ResumeProfile
from app.schemas.resume_profile_review import HRConfirmedFact, HRFactInput, ProfileRevisionCreate
from app.schemas.scoring import CriterionEvidence
from app.schemas.screening_source import ScreeningSourceSnapshot


CONTRACTS = Path(__file__).resolve().parents[2] / "docs/contracts"
SOURCE = (CONTRACTS / "resume-extraction-v1.source.txt").read_bytes().decode("utf-8")


def profile():
    return json.loads((CONTRACTS / "resume-extraction-v1.example.json").read_text(encoding="utf-8"))


def fact(**values):
    return {"section": "skills", "label": "Verified skill", "value": "Synthetic HR-only skill",
            "reason": "Confirmed in interview", **values}


def confirmation():
    return HRConfirmedFact(**fact(), id=ObjectId(), confirmed_by=ObjectId(),
                           confirmed_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc))


def source():
    value = confirmation()
    return {
        "resume_profile_id": ObjectId(), "profile_version": 2, "extraction_run_id": ObjectId(),
        "source_parse_revision": 1, "resume_checksum": "a" * 64, "source_text": SOURCE,
        "source_text_sha256": sha256(SOURCE.encode("utf-8")).hexdigest(), "profile": profile(),
        "extraction_model_name": "synthetic", "extraction_prompt_version": "resume-extract-v1",
        "extractor_version": "http-extractor-1.0", "profile_origin": "hr",
        "profile_edited_by": value.confirmed_by, "profile_edit_reason": "HR correction",
        "profile_created_at": value.confirmed_at, "hr_annotations": [value],
    }


def command(**values):
    return {"base_profile_id": str(ObjectId()), "expected_version": 1, "reason": "HR correction", **values}


def test_profile_revision_accepts_cv_correction_or_hr_facts_and_explicit_empty_facts():
    for changes in ({"profile": profile()}, {"hr_annotations": [fact()]}, {"hr_annotations": []}):
        value = ProfileRevisionCreate.model_validate(command(**changes))
        assert value.reason == "HR correction"
    with pytest.raises(ValidationError):
        ProfileRevisionCreate.model_validate(command())


@pytest.mark.parametrize("field", ["id", "confirmed_by", "confirmed_at", "reviewer_id", "origin"])
def test_client_cannot_spoof_hr_confirmation_identity_or_reviewer(field):
    with pytest.raises(ValidationError):
        ProfileRevisionCreate.model_validate(command(hr_annotations=[fact(**{field: str(ObjectId())})]))


@pytest.mark.parametrize("field", ["edited_by", "version", "parent_profile_id", "origin", "idempotency_key"])
def test_revision_command_rejects_internal_fields(field):
    with pytest.raises(ValidationError):
        ProfileRevisionCreate.model_validate(command(hr_annotations=[], **{field: str(ObjectId())}))


@pytest.mark.parametrize("value", [0, True, "1", 1.0])
def test_expected_profile_version_is_a_positive_strict_integer(value):
    with pytest.raises(ValidationError):
        ProfileRevisionCreate.model_validate(command(hr_annotations=[], expected_version=value))


def test_hr_labels_are_unique_within_section_after_whitespace_and_case_normalization():
    with pytest.raises(ValidationError):
        ProfileRevisionCreate.model_validate(command(hr_annotations=[fact(label=" Python "), fact(label="python")]))
    assert len(ProfileRevisionCreate.model_validate(command(hr_annotations=[
        fact(label="Python"), fact(section="summary", label="Python")
    ])).hr_annotations) == 2


@pytest.mark.parametrize("field", ["label", "value", "reason"])
def test_hr_facts_reject_blank_values(field):
    with pytest.raises(ValidationError):
        HRFactInput.model_validate(fact(**{field: "  "}))


def test_hr_confirmation_normalizes_mongo_naive_datetime_to_utc():
    value = HRConfirmedFact(**fact(), id=ObjectId(), confirmed_by=ObjectId(),
                            confirmed_at=datetime(2026, 10, 14, 1))
    assert value.confirmed_at.tzinfo == timezone.utc


def test_hr_profile_snapshot_serializes_mongo_timestamp_as_utc():
    value = source()
    value["profile_created_at"] = datetime(2026, 10, 14, 1)
    snapshot = ScreeningSourceSnapshot.model_validate(value)
    assert snapshot.profile_created_at.tzinfo == timezone.utc
    assert snapshot.model_dump(mode="json")["profile_created_at"].endswith("Z")


def test_hr_revision_indexes_preserve_one_ai_profile_per_extraction_and_unique_command():
    indexes = {index.document.get("name"): index.document for index in ResumeProfile.Settings.indexes}
    assert indexes["uq_ai_profile_extraction_run"]["partialFilterExpression"] == {"origin": "ai"}
    assert indexes["uq_hr_profile_revision_key"]["partialFilterExpression"] == {"idempotency_key": {"$type": "string"}}


def test_hr_facts_are_bound_to_frozen_identity_and_exact_value_without_modifying_input():
    snapshot = source()
    fact_value = snapshot["hr_annotations"][0]
    output = {"criteria_evaluations": {"python": {"status": "evaluated", "score": 90,
        "reason": "HR confirmation", "evidence": [{"origin": "hr", "hr_fact_id": fact_value.id,
                                                     "text": fact_value.value}]}}}
    before = copy.deepcopy(output)
    bound = bind_screening_output(output, snapshot)
    evidence = bound.criteria_evaluations["python"].evidence[0]
    assert evidence.origin == "hr" and evidence.hr_fact_id == fact_value.id
    assert evidence.text == fact_value.value and evidence.start is evidence.end is evidence.page is None
    assert output == before and fact_value.value not in SOURCE


@pytest.mark.parametrize("mutation", ["wrong_id", "changed_value", "cv_origin", "offset", "page"])
def test_hr_evidence_cannot_claim_another_fact_or_cv_provenance(mutation):
    snapshot = source()
    value = snapshot["hr_annotations"][0]
    evidence = {"origin": "hr", "hr_fact_id": value.id, "text": value.value}
    if mutation == "wrong_id":
        evidence["hr_fact_id"] = ObjectId()
    elif mutation == "changed_value":
        evidence["text"] += " invented"
    elif mutation == "cv_origin":
        evidence["origin"] = "cv"
    elif mutation == "offset":
        evidence.update(start=0, end=len(value.value))
    else:
        evidence["page"] = 1
    output = {"criteria_evaluations": {"python": {"status": "evaluated", "score": 90,
                                                  "reason": "HR confirmation", "evidence": [evidence]}}}
    with pytest.raises(ValueError, match="Invalid screening output evidence"):
        bind_screening_output(output, snapshot)


@pytest.mark.parametrize("field", ["profile_edited_by", "profile_edit_reason", "profile_created_at"])
def test_hr_source_requires_complete_reviewer_provenance(field):
    value = source()
    value[field] = None
    with pytest.raises(ValidationError):
        ScreeningSourceSnapshot.model_validate(value)


def test_ai_source_cannot_claim_hr_facts_and_duplicate_fact_ids_are_rejected():
    value = source()
    value.update(profile_origin="ai", profile_edited_by=None, profile_edit_reason=None)
    with pytest.raises(ValidationError):
        ScreeningSourceSnapshot.model_validate(value)
    value = source()
    value["hr_annotations"] *= 2
    with pytest.raises(ValidationError):
        ScreeningSourceSnapshot.model_validate(value)


def test_cv_fabrication_still_fails_while_hr_only_facts_leave_cv_profile_unchanged():
    value = profile()
    evidence = value["personal_info"]["full_name"]["evidence"][0]
    evidence.update(quote="Invented identity", start=0, end=len("Invented identity"))
    with pytest.raises(ValueError):
        validate_resume_extraction(value, raw_text=SOURCE)
    verified = validate_screening_source(source())
    assert verified.profile.source_text_sha256 == sha256(SOURCE.encode("utf-8")).hexdigest()
    assert verified.hr_annotations[0].value not in SOURCE


def test_hr_evidence_requires_a_confirmation_id():
    with pytest.raises(ValidationError):
        CriterionEvidence(origin="hr", text="Unverified claim")
