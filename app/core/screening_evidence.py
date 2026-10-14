"""Pure source verification boundary for untrusted screening output."""
from hashlib import sha256

from app.core.resume_extraction import validate_resume_extraction
from app.schemas.screening_source import ScreeningSourceSnapshot
from app.schemas.worker import ScreeningOutput


def validate_screening_source(
    snapshot: dict | ScreeningSourceSnapshot | None,
) -> ScreeningSourceSnapshot:
    """Return a newly validated copy; legacy runs without a snapshot fail closed."""
    try:
        data = snapshot.model_dump(mode="python", warnings=False) if isinstance(snapshot, ScreeningSourceSnapshot) else snapshot
        source = ScreeningSourceSnapshot.model_validate(data)
        if not source.source_text.strip():
            raise ValueError("Empty screening source")
        if source.source_text_sha256 != sha256(source.source_text.encode("utf-8")).hexdigest():
            raise ValueError("Screening source hash mismatch")
        source.profile = validate_resume_extraction(source.profile, raw_text=source.source_text)
        return source
    except ValueError:
        # Neither schema errors nor malformed source data may expose CV values.
        raise ValueError("Invalid or missing screening source snapshot") from None


def bind_screening_output(
    output: dict | ScreeningOutput, snapshot: dict | ScreeningSourceSnapshot | None,
) -> ScreeningOutput:
    """Bind every exact quote to snapshot text without modifying caller input.

    Offsets can disambiguate repeated quotes. Quotes without offsets must identify
    a unique span. Parser output does not currently have a reliable page map, so
    new results cannot claim page numbers; historical results remain readable.
    """
    source = validate_screening_source(snapshot)
    try:
        data = output.model_dump(mode="python", warnings=False) if isinstance(output, ScreeningOutput) else output
        result = ScreeningOutput.model_validate(data)
        data = result.model_dump(mode="python", warnings=False)
        hr_facts = {str(fact.id): fact for fact in source.hr_annotations}
        for evaluation in data["criteria_evaluations"].values():
            for evidence in evaluation["evidence"]:
                if evidence["origin"] == "hr":
                    fact = hr_facts.get(str(evidence["hr_fact_id"]))
                    if fact is None or evidence["text"] != fact.value:
                        raise ValueError("HR evidence does not match a frozen confirmation")
                    continue
                if evidence["page"] is not None:
                    raise ValueError("Screening source has no verified page map")
                quote, start, end = evidence["text"], evidence["start"], evidence["end"]
                if start is None:
                    start = source.source_text.find(quote)
                    if start < 0 or source.source_text.find(quote, start + 1) >= 0:
                        raise ValueError("Screening quote requires a unique source span")
                    end = start + len(quote)
                if end > len(source.source_text) or source.source_text[start:end] != quote:
                    raise ValueError("Screening quote does not match its source span")
                evidence.update(start=start, end=end)
        return ScreeningOutput.model_validate(data)
    except ValueError:
        raise ValueError("Invalid screening output evidence") from None
