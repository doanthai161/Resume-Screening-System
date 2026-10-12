import copy
from hashlib import sha256

from bson import ObjectId
import pytest
from pydantic import ValidationError

from app.core.screening_evidence import bind_screening_output, validate_screening_source
from app.schemas.scoring import CriterionEvidence
from app.schemas.screening_source import ScreeningSourceSnapshot
from app.schemas.worker import ScreeningOutput


SOURCE = "Nguyễn An 👋\n  Python \nPython\nEnglish B2\nExample Co\n2022 2024\n"


def missing():
    return {"status": "insufficient_evidence", "reason": "Chưa đủ dữ liệu", "value": None}


def missing_section():
    return {"status": "insufficient_evidence", "reason": "Chưa đủ dữ liệu", "items": []}


def profile():
    return {
        "schema_version": "1.0", "source_text_sha256": sha256(SOURCE.encode()).hexdigest(),
        "personal_info": {
            "full_name": {
                "status": "extracted", "value": "Nguyễn An",
                "evidence": [{"quote": "Nguyễn An 👋", "start": 0, "end": len("Nguyễn An 👋")}],
            },
            "email": missing(), "phone": missing(), "location": missing(), "links": missing_section(),
        },
        "summary": missing(), "skills": missing_section(), "experiences": missing_section(),
        "education": missing_section(), "certifications": missing_section(), "languages": missing_section(),
    }


def snapshot():
    return {
        "resume_profile_id": ObjectId(), "profile_version": 1, "extraction_run_id": ObjectId(),
        "source_parse_revision": 1, "source_parse_run_id": ObjectId(), "resume_checksum": "a" * 64,
        "source_text": SOURCE, "source_text_sha256": sha256(SOURCE.encode()).hexdigest(), "profile": profile(),
        "extraction_model_name": "test-model", "extraction_prompt_version": "resume-extract-v1",
        "extractor_version": "http-extractor-1.0",
    }


def output(text="English B2", *, status="evaluated", **evidence):
    return {"criteria_evaluations": {"language": {
        "status": status, "score": 80 if status == "evaluated" else None,
        "reason": "Dẫn chứng trong CV", "evidence": [{"text": text, **evidence}],
    }}}


def test_unicode_quote_binding_preserves_whitespace_and_caller_data():
    source, data = snapshot(), output("\n  Python \n")
    before_source, before_output = copy.deepcopy(source), copy.deepcopy(data)
    result = bind_screening_output(data, source)
    quote = result.criteria_evaluations["language"].evidence[0]
    assert quote.text == "\n  Python \n"
    assert quote.start == SOURCE.index("\n  Python \n")
    assert quote.end == quote.start + len(quote.text)
    assert SOURCE[quote.start:quote.end] == quote.text
    assert source == before_source and data == before_output


def test_validated_snapshot_and_output_are_fresh_copies():
    source = ScreeningSourceSnapshot.model_validate(snapshot())
    data = ScreeningOutput.model_validate(output("Nguyễn An 👋"))
    result = bind_screening_output(data, source)
    verified = validate_screening_source(source)
    assert verified is not source and verified.profile is not source.profile
    assert result is not data
    assert data.criteria_evaluations["language"].evidence[0].start is None
    assert result.criteria_evaluations["language"].evidence[0].end == len("Nguyễn An 👋")
    verified.profile.personal_info.full_name.value = "Changed"
    assert source.profile.personal_info.full_name.value == "Nguyễn An"


def test_ambiguous_quote_requires_offsets_but_an_exact_span_is_accepted():
    with pytest.raises(ValueError, match="Invalid screening output evidence"):
        bind_screening_output(output("Python"), snapshot())
    start = SOURCE.rindex("Python")
    result = bind_screening_output(output("Python", start=start, end=start + 6), snapshot())
    assert result.criteria_evaluations["language"].evidence[0].start == start


@pytest.mark.parametrize("status", ["evaluated", "insufficient_evidence", "conflicting_evidence"])
def test_evidence_is_verified_for_every_evaluation_status(status):
    valid = bind_screening_output(output(status=status), snapshot())
    assert valid.criteria_evaluations["language"].evidence[0].start == SOURCE.index("English B2")
    with pytest.raises(ValueError, match="Invalid screening output evidence"):
        bind_screening_output(output("private invented claim", status=status), snapshot())


@pytest.mark.parametrize("mutation", ["quote", "offset", "past_end", "unpaired", "page", "unknown_field"])
def test_forged_or_unverifiable_evidence_is_rejected(mutation):
    start = SOURCE.index("English B2")
    data = output(start=start, end=start + len("English B2"))
    evidence = data["criteria_evaluations"]["language"]["evidence"][0]
    if mutation == "quote":
        evidence["text"] = "Invented!!"
    elif mutation == "offset":
        evidence["start"] += 1
        evidence["end"] += 1
    elif mutation == "past_end":
        evidence.update(start=len(SOURCE), end=len(SOURCE) + len(evidence["text"]))
    elif mutation == "unpaired":
        evidence.pop("end")
    elif mutation == "page":
        evidence["page"] = 1
    else:
        evidence["source_text_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="Invalid screening output evidence"):
        bind_screening_output(data, snapshot())


@pytest.mark.parametrize("payload", [
    {"text": "   "}, {"text": "English B2", "start": True, "end": 11},
    {"text": "English B2", "start": 0.0, "end": 10},
    {"text": "English B2", "start": "0", "end": 10},
    {"text": "English B2", "start": 0}, {"text": "English B2", "end": 10},
    {"text": "English B2", "start": 1, "end": 10},
])
def test_offsets_are_paired_strict_and_quote_length_matches(payload):
    with pytest.raises(ValidationError):
        CriterionEvidence.model_validate(payload)


def test_historical_page_evidence_is_readable_but_not_accepted_for_a_new_completion():
    legacy = ScreeningOutput.model_validate(output(page=1))
    assert legacy.criteria_evaluations["language"].evidence[0].page == 1
    with pytest.raises(ValueError, match="Invalid screening output evidence"):
        bind_screening_output(legacy, snapshot())


@pytest.mark.parametrize("mutation", ["missing", "empty", "hash", "profile_hash", "profile_quote", "text", "blank", "extra"])
def test_missing_or_corrupt_snapshot_fails_closed(mutation):
    data = snapshot()
    if mutation == "missing":
        data = None
    elif mutation == "empty":
        data = {}
    elif mutation == "hash":
        data["source_text_sha256"] = "b" * 64
    elif mutation == "profile_hash":
        data["profile"]["source_text_sha256"] = "b" * 64
    elif mutation == "profile_quote":
        data["profile"]["personal_info"]["full_name"]["evidence"][0]["quote"] = "Invented!!!"
    elif mutation == "text":
        data["source_text"] += "Changed source"
    elif mutation == "blank":
        data["source_text"] = "   "
    else:
        data["company_id"] = ObjectId()
    with pytest.raises(ValueError, match="Invalid or missing screening source snapshot"):
        bind_screening_output(output(), data)


@pytest.mark.parametrize("mutation", ["profile_quote", "hash", "evidence", "state"])
def test_mutated_pydantic_instances_are_revalidated_without_exposing_values(mutation):
    source = ScreeningSourceSnapshot.model_validate(snapshot())
    data = ScreeningOutput.model_validate(output())
    private_value = "synthetic private CV data"
    if mutation == "profile_quote":
        source.profile.personal_info.full_name.evidence[0].quote = private_value
    elif mutation == "hash":
        source.source_text_sha256 = private_value
    elif mutation == "evidence":
        data.criteria_evaluations["language"].evidence[0].text = private_value
    else:
        data.criteria_evaluations["language"].score = private_value
    with pytest.raises(ValueError) as caught:
        bind_screening_output(data, source)
    assert private_value not in str(caught.value)
    assert SOURCE not in str(caught.value)
    assert "input_value" not in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("profile_version", 0), ("profile_version", True), ("source_parse_revision", -1),
    ("source_parse_revision", "1"), ("extraction_model_name", " "),
    ("extraction_prompt_version", "x" * 151), ("extractor_version", 123),
])
def test_snapshot_metadata_has_strict_bounds(field, value):
    data = snapshot()
    data[field] = value
    with pytest.raises(ValueError, match="Invalid or missing screening source snapshot"):
        validate_screening_source(data)


def test_unresolved_criteria_without_quotes_and_missing_criteria_remain_valid():
    data = {"criteria_evaluations": {"language": {
        "status": "insufficient_evidence", "reason": "Chưa đủ dữ liệu", "score": None,
    }}}
    result = bind_screening_output(data, snapshot())
    assert result.criteria_evaluations["language"].evidence == []
    assert bind_screening_output({"criteria_evaluations": {}}, snapshot()).criteria_evaluations == {}
