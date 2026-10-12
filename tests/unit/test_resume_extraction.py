import copy
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.resume_extraction import validate_resume_extraction
from app.schemas.resume_extraction import (
    Certification,
    Education,
    Experience,
    ExtractedValue,
    ResumeDate,
    ResumeExtractionOutput,
    ShortText,
    SourceEvidence,
)
from app.schemas.worker import ParseOutput


CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts"
SOURCE = "Nguyễn An 👋\nPython\nEnglish B2\n2022 2023 2024\nExample Co\n"


def missing():
    return {"status": "insufficient_evidence", "value": None, "reason": "Chưa đủ dữ liệu"}


def missing_section():
    return {"status": "insufficient_evidence", "items": [], "reason": "Chưa đủ dữ liệu"}


def evidence(quote, source=SOURCE):
    start = source.index(quote)
    return {"quote": quote, "start": start, "end": start + len(quote)}


def extracted(value, quote):
    return {"status": "extracted", "value": value, "evidence": [evidence(quote)]}


def conflict():
    return {
        "status": "conflicting_evidence", "value": None, "reason": "Two names in source",
        "alternatives": [
            {"value": "Nguyễn An", "evidence": [evidence("Nguyễn An")]},
            {"value": "Example Co", "evidence": [evidence("Example Co")]},
        ],
    }


def payload():
    return {
        "schema_version": "1.0",
        "source_text_sha256": sha256(SOURCE.encode("utf-8")).hexdigest(),
        "personal_info": {
            "full_name": extracted("Nguyễn An", "Nguyễn An"),
            "email": missing(), "phone": missing(), "location": missing(),
            "links": missing_section(),
        },
        "summary": missing(),
        "skills": {
            "status": "extracted", "items": [{"name": extracted("Python", "Python"), "level": missing()}],
        },
        "experiences": missing_section(), "education": missing_section(),
        "certifications": missing_section(), "languages": missing_section(),
    }


def dated_record(model, start=None, end=None):
    data = {name: missing() for name in model.model_fields if name != "review_issues"}
    anchor = "organization" if model is Experience else "institution"
    data[anchor] = extracted("Example Co", "Example Co")
    data["start_date"] = extracted(start, str(start["year"])) if start else missing()
    data["end_date"] = extracted(end, str(end["year"])) if end else missing()
    return data


def test_partial_profile_preserves_unknowns_and_unicode_evidence():
    output = validate_resume_extraction(payload(), raw_text=SOURCE)
    assert output.personal_info.phone.value is None
    assert output.personal_info.phone.status == "insufficient_evidence"
    assert output.experiences.items == []
    # Offsets count Unicode code points, including characters after the emoji.
    python = output.skills.items[0].name.evidence[0]
    assert SOURCE[python.start:python.end] == "Python"
    assert "overall_score" not in output.model_dump()
    restored = ResumeExtractionOutput.model_validate_json(output.model_dump_json())
    assert validate_resume_extraction(restored, raw_text=SOURCE) == output


def test_entirely_unknown_profile_does_not_invent_information():
    data = payload()
    data["personal_info"]["full_name"] = missing()
    data["skills"] = missing_section()
    output = validate_resume_extraction(data, raw_text=SOURCE)
    assert output.personal_info.full_name.value is None
    assert output.skills.status == "insufficient_evidence"


def test_conflicts_preserve_both_evidenced_candidates_without_resolving():
    data = payload()
    data["personal_info"]["full_name"] = conflict()
    output = validate_resume_extraction(data, raw_text=SOURCE)
    name = output.personal_info.full_name
    assert name.value is None
    assert [item.value for item in name.alternatives] == ["Nguyễn An", "Example Co"]


@pytest.mark.parametrize("fact", [
    {"status": "extracted", "value": "Python"},
    {"status": "extracted", "value": None, "evidence": [evidence("Python")]},
    {"status": "extracted", "value": " ", "evidence": [evidence("Python")]},
    {"status": "extracted", "value": 123, "evidence": [evidence("Python")]},
    {"status": "insufficient_evidence", "value": "Python", "reason": "Missing"},
    {"status": "insufficient_evidence", "value": None},
    {"status": "conflicting_evidence", "reason": "Conflict", "alternatives": []},
    {"status": "evaluated", "value": "Python", "evidence": [evidence("Python")]},
])
def test_invalid_field_states_are_rejected(fact):
    data = payload()
    data["skills"]["items"][0]["name"] = fact
    with pytest.raises(ValidationError):
        validate_resume_extraction(data, raw_text=SOURCE)


@pytest.mark.parametrize("mutation", ["one_alternative", "same_values", "missing_quote", "resolved_value", "direct_evidence", "no_reason"])
def test_invalid_conflicts_are_rejected(mutation):
    value = conflict()
    if mutation == "one_alternative":
        value["alternatives"].pop()
    elif mutation == "same_values":
        value["alternatives"][1]["value"] = value["alternatives"][0]["value"]
    elif mutation == "missing_quote":
        value["alternatives"][1]["evidence"] = []
    elif mutation == "resolved_value":
        value["value"] = "Nguyễn An"
    elif mutation == "direct_evidence":
        value["evidence"] = [evidence("Nguyễn An")]
    else:
        value.pop("reason")
    with pytest.raises(ValidationError):
        ExtractedValue[ShortText].model_validate(value)


@pytest.mark.parametrize("mutation", ["quote", "offset", "past_end", "alternative_quote", "source_hash", "normalized_source"])
def test_forged_or_stale_evidence_is_rejected(mutation):
    data = payload()
    source = SOURCE
    span = data["skills"]["items"][0]["name"]["evidence"][0]
    if mutation == "quote":
        span["quote"] = "Rust!!"  # Same length; passes structural span validation.
    elif mutation == "offset":
        span["start"] += 1
        span["end"] += 1
    elif mutation == "past_end":
        span.update(start=len(SOURCE), end=len(SOURCE) + len("Python"))
    elif mutation == "alternative_quote":
        data["personal_info"]["full_name"] = conflict()
        data["personal_info"]["full_name"]["alternatives"][1]["evidence"][0]["quote"] = "InventedCo"
    elif mutation == "source_hash":
        data["source_text_sha256"] = "0" * 64
    else:
        source = SOURCE.replace("\n", "\r\n")
    with pytest.raises(ValueError):
        validate_resume_extraction(data, raw_text=source)


def test_exact_quote_whitespace_is_preserved():
    data = payload()
    data["summary"] = extracted("Nguyễn An", "Nguyễn An 👋\n")
    output = validate_resume_extraction(data, raw_text=SOURCE)
    assert output.summary.evidence[0].quote.endswith("\n")


@pytest.mark.parametrize(
    "source", [None, b"Python", "", " \n", "a" * 1_000_001],
    ids=["null", "bytes", "empty", "blank", "over_limit"],
)
def test_invalid_sources_are_rejected(source):
    with pytest.raises(ValueError):
        validate_resume_extraction(payload(), raw_text=source)


@pytest.mark.parametrize("span", [
    {"quote": "Python", "start": True, "end": 7},
    {"quote": "Python", "start": 0, "end": 5},
    {"quote": " ", "start": 0, "end": 1},
    {"quote": "Python", "start": -1, "end": 5},
    {"quote": "Python", "start": 0, "end": 6, "page": 1},
])
def test_invalid_spans_and_unverified_page_claims_are_rejected(span):
    with pytest.raises(ValidationError):
        SourceEvidence.model_validate(span)


@pytest.mark.parametrize("mutation", ["empty_extracted", "nonempty_missing", "no_reason", "placeholder", "omit_section", "extra_tenant", "extra_field", "extra_score", "version", "bad_email", "unsafe_url"])
def test_malformed_sections_and_unexpected_fields_are_rejected(mutation):
    data = payload()
    if mutation == "empty_extracted":
        data["skills"]["items"] = []
    elif mutation == "nonempty_missing":
        data["skills"]["status"] = "insufficient_evidence"
    elif mutation == "no_reason":
        data["education"].pop("reason")
    elif mutation == "placeholder":
        data["skills"]["items"] = [{"name": missing(), "level": missing()}]
    elif mutation == "omit_section":
        data.pop("languages")
    elif mutation == "extra_tenant":
        data["company_id"] = "untrusted"
    elif mutation == "extra_field":
        data["personal_info"]["phone"]["verified_by_hr"] = True
    elif mutation == "extra_score":
        data["overall_score"] = 100
    elif mutation == "version":
        data["schema_version"] = "2.0"
    elif mutation == "bad_email":
        data["personal_info"]["email"] = extracted("not-an-email", "Nguyễn An")
    else:
        data["personal_info"]["links"] = {"status": "extracted", "items": [
            {"label": missing(), "url": extracted("javascript:alert(1)", "Python")},
        ]}
    with pytest.raises(ValidationError):
        validate_resume_extraction(data, raw_text=SOURCE)


@pytest.mark.parametrize("value", [
    {"year": True}, {"year": "2024"}, {"year": 2024, "day": 2},
    {"year": 2023, "month": 2, "day": 29}, {"year": 2024, "month": 13},
    {"year": 2024, "month": 2, "day": 30},
])
def test_invalid_or_invented_date_precision_is_rejected(value):
    with pytest.raises(ValidationError):
        ResumeDate.model_validate(value)


def test_partial_dates_remain_partial_and_calendar_bounds_are_correct():
    year = ResumeDate(year=2024)
    assert year.model_dump() == {"year": 2024, "month": None, "day": None}
    assert ResumeDate(year=2024, month=2).bounds()[1].isoformat() == "2024-02-29"
    assert ResumeDate(year=2024, month=2, day=29).bounds()[0].isoformat() == "2024-02-29"


@pytest.mark.parametrize("model", [Experience, Education])
def test_reversed_dates_are_preserved_for_review_and_partial_dates_are_allowed(model):
    record = model.model_validate(dated_record(model, {"year": 2024}, {"year": 2022}))
    assert record.review_issues == ["conflicting_dates"]
    assert record.start_date.value.year == 2024 and record.end_date.value.year == 2022
    restored = model.model_validate_json(record.model_dump_json())
    assert restored.review_issues == ["conflicting_dates"]
    item = model.model_validate(dated_record(model, {"year": 2024, "month": 12}, {"year": 2024}))
    assert item.end_date.value.month is None


def test_ongoing_and_unknown_end_dates_are_distinct_and_boolean_is_strict():
    data = dated_record(Experience, {"year": 2022})
    ongoing = copy.deepcopy(data)
    ongoing["is_current"] = extracted(True, "2024")
    assert Experience.model_validate(ongoing).is_current.value is True
    assert Experience.model_validate(data).is_current.value is None
    ongoing["end_date"] = extracted({"year": 2024}, "2024")
    assert Experience.model_validate(ongoing).review_issues == ["conflicting_current_role"]
    ongoing["end_date"] = missing()
    ongoing["is_current"]["value"] = "true"
    with pytest.raises(ValidationError):
        Experience.model_validate(ongoing)


def test_conflicting_certification_dates_are_preserved_for_hr_review():
    data = {name: missing() for name in Certification.model_fields if name != "review_issues"}
    data.update(name=extracted("Python", "Python"),
                issued_date=extracted({"year": 2024}, "2024"),
                expiry_date=extracted({"year": 2023}, "2023"))
    assert Certification.model_validate(data).review_issues == ["conflicting_dates"]


def test_unresolved_partial_evidence_is_preserved_and_verified():
    data = payload()
    data["personal_info"]["email"] = {**missing(), "evidence": [evidence("Nguyễn An")]}
    output = validate_resume_extraction(data, raw_text=SOURCE)
    assert output.personal_info.email.value is None
    assert len(output.personal_info.email.evidence) == 1
    data["personal_info"]["email"]["evidence"][0]["quote"] = "Invented!"
    with pytest.raises(ValueError):
        validate_resume_extraction(data, raw_text=SOURCE)


def test_identity_less_record_is_explicitly_marked_and_worker_cannot_clear_flag():
    data = payload()
    data["skills"]["items"] = [{"name": missing(), "level": extracted("B2", "B2"), "review_issues": []}]
    record = validate_resume_extraction(data, raw_text=SOURCE).skills.items[0]
    assert record.review_issues == ["missing_identity"]
    record.review_issues.clear()
    from app.schemas.resume_extraction import Skill
    assert Skill.model_validate(record).review_issues == ["missing_identity"]


def test_collection_and_aggregate_byte_limits():
    data = payload()
    item = data["skills"]["items"][0]
    data["skills"]["items"] = [copy.deepcopy(item) for _ in range(201)]
    with pytest.raises(ValidationError):
        ResumeExtractionOutput.model_validate(data)
    large_fact = extracted("Python", "Python")
    large_fact["evidence"] = [{"quote": "ắ" * 2000, "start": 0, "end": 2000}] * 10
    data["skills"]["items"] = [{"name": large_fact, "level": missing()} for _ in range(40)]
    with pytest.raises(ValidationError, match="too large"):
        ResumeExtractionOutput.model_validate(data)


def test_existing_parse_output_remains_compatible():
    output = ParseOutput(parsed_data={"raw_text": SOURCE, "parser_version": "mineru-test"})
    assert output.parsed_data.raw_text == SOURCE
    assert output.parsed_data.skills == []
    assert "schema_version" not in output.parsed_data.model_dump()


def test_published_schema_and_example_match_backend_contract():
    schema = json.loads((CONTRACT_DIR / "resume-extraction-v1.schema.json").read_text(encoding="utf-8"))
    assert schema == ResumeExtractionOutput.model_json_schema()
    source = (CONTRACT_DIR / "resume-extraction-v1.source.txt").read_bytes().decode("utf-8")
    example = json.loads((CONTRACT_DIR / "resume-extraction-v1.example.json").read_text(encoding="utf-8"))
    output = validate_resume_extraction(example, raw_text=source)
    assert output.experiences.items[0].is_current.value is True
    assert output.languages.items[0].name.value == "English"
    assert output.personal_info.full_name.value == "Nguyễn An"
