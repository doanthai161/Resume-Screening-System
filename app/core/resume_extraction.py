"""Source verification boundary for untrusted extractor output (no DB or I/O)."""

from hashlib import sha256
import copy
import json
from typing import Any, Iterator

from pydantic import BaseModel

from app.schemas.resume_extraction import (
    MAX_SOURCE_CHARACTERS,
    ResumeExtractionOutput,
    SourceEvidence,
)


def _evidence_in(value: Any) -> Iterator[SourceEvidence]:
    if isinstance(value, SourceEvidence):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _evidence_in(getattr(value, name))
    elif isinstance(value, list):
        for item in value:
            yield from _evidence_in(item)


def validate_resume_extraction(
    payload: dict[str, Any] | ResumeExtractionOutput, *, raw_text: str
) -> ResumeExtractionOutput:
    """Validate shape and every quote against the BE's original, unmodified text.

    Call this at the future extractor ingress before persisting a result. Plain
    model_validate checks structure only; it cannot verify a source it never saw.
    Errors deliberately omit CV text and payload values.
    """
    if not isinstance(raw_text, str) or not raw_text.strip() or len(raw_text) > MAX_SOURCE_CHARACTERS:
        raise ValueError("Extraction source must be nonblank text within the character limit")
    output = ResumeExtractionOutput.model_validate(payload)
    if output.source_text_sha256 != sha256(raw_text.encode("utf-8")).hexdigest():
        raise ValueError("Extraction source hash does not match the current parsed text")
    for evidence in _evidence_in(output):
        if evidence.end > len(raw_text) or raw_text[evidence.start:evidence.end] != evidence.quote:
            raise ValueError("Extraction evidence does not match its source span")
    return output


def _limit_json_complexity(value) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        if depth > 32 or nodes + len(pending) + len(children) > 100_000:
            raise ValueError("Extraction JSON nesting or complexity exceeds limit")
        pending.extend((child, depth + 1) for child in children)


def load_extraction_json(data: str | bytes) -> dict:
    """Reject ambiguous duplicate keys without exposing model/CV content."""
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=unique_keys)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Invalid extraction JSON") from None
    if not isinstance(value, dict):
        raise ValueError("Extraction JSON must be an object")
    _limit_json_complexity(value)
    return value


def bind_extraction_source(payload: dict, *, raw_text: str) -> ResumeExtractionOutput:
    """BE supplies the source binding; resolve unique verbatim quotes in code.

    Ambiguous repeated quotes require a wider excerpt or an explicit valid span.
    Never use fuzzy matching or normalize the immutable source.
    """
    _limit_json_complexity(payload)
    payload = copy.deepcopy(payload)
    payload["schema_version"] = "1.0"
    payload["source_text_sha256"] = sha256(raw_text.encode("utf-8")).hexdigest()

    def visit(value):
        if isinstance(value, dict):
            if "quote" in value and "start" not in value and "end" not in value:
                quote = value["quote"]
                if not isinstance(quote, str) or not quote:
                    raise ValueError("Invalid extraction quote")
                start = raw_text.find(quote)
                if start < 0 or raw_text.find(quote, start + 1) >= 0:
                    raise ValueError("Quote must identify a unique source span")
                value.update(start=start, end=start + len(quote))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    return validate_resume_extraction(payload, raw_text=raw_text)
