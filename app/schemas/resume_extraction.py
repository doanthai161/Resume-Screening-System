"""Versioned extraction contract; independent of parsing and screening results."""

import calendar
from datetime import date
from enum import Enum
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    StrictBool,
    StringConstraints,
    model_validator,
)


MAX_EXTRACTION_BYTES = 2_000_000
MAX_SOURCE_CHARACTERS = 1_000_000
ShortText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=300)
]
LongText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=5000)
]
Reason = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)
]
T = TypeVar("T")


class ExtractionModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", revalidate_instances="always", hide_input_in_errors=True
    )


class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class SourceEvidence(ExtractionModel):
    """Exact raw_text slice, with zero-based Unicode character offsets [start, end)."""

    quote: str = Field(..., strict=True, min_length=1, max_length=2000)
    start: int = Field(..., strict=True, ge=0, lt=MAX_SOURCE_CHARACTERS)
    end: int = Field(..., strict=True, gt=0, le=MAX_SOURCE_CHARACTERS)

    @model_validator(mode="after")
    def validate_span(self) -> "SourceEvidence":
        if not self.quote.strip() or self.end - self.start != len(self.quote):
            raise ValueError("Evidence requires a nonblank quote and matching span length")
        return self


class EvidenceAlternative(ExtractionModel, Generic[T]):
    value: T
    evidence: list[SourceEvidence] = Field(..., min_length=1, max_length=10)


class ExtractedValue(ExtractionModel, Generic[T]):
    status: ExtractionStatus
    value: T | None = None
    evidence: list[SourceEvidence] = Field(default_factory=list, max_length=10)
    reason: Reason | None = None
    alternatives: list[EvidenceAlternative[T]] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_state(self) -> "ExtractedValue[T]":
        if self.status == ExtractionStatus.EXTRACTED:
            if self.value is None or not self.evidence or self.alternatives:
                raise ValueError("Extracted values require a value, evidence and no alternatives")
        elif self.status == ExtractionStatus.INSUFFICIENT_EVIDENCE:
            if self.value is not None or self.alternatives or not self.reason:
                raise ValueError("Unresolved values require null, a reason and no alternatives")
        else:
            if self.value is not None or self.evidence or not self.reason:
                raise ValueError("Conflicts require null, a reason and evidence on alternatives")
            if len(self.alternatives) < 2:
                raise ValueError("Conflicts require at least two distinct alternatives")
            values = [item.value for item in self.alternatives]
            if any(value in values[:index] for index, value in enumerate(values)):
                raise ValueError("Conflict alternatives must have distinct values")
        return self


class ExtractedSection(ExtractionModel, Generic[T]):
    """An absent section is explicit; it does not assert that a candidate has none."""

    status: Literal["extracted", "insufficient_evidence"]
    items: list[T] = Field(..., max_length=200)
    reason: Reason | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ExtractedSection[T]":
        if self.status == "extracted" and not self.items:
            raise ValueError("Extracted sections require at least one item")
        if self.status == "insufficient_evidence" and (self.items or not self.reason):
            raise ValueError("Missing sections require empty items and a reason")
        return self


class ResumeDate(ExtractionModel):
    """Preserve date precision: a year is not silently converted to January 1."""

    year: int = Field(..., strict=True, ge=1, le=9999)
    month: int | None = Field(None, strict=True, ge=1, le=12)
    day: int | None = Field(None, strict=True, ge=1, le=31)

    @model_validator(mode="after")
    def validate_calendar_date(self) -> "ResumeDate":
        if self.day is not None:
            if self.month is None:
                raise ValueError("A day requires a month")
            date(self.year, self.month, self.day)
        return self

    def bounds(self) -> tuple[date, date]:
        first_month, last_month = self.month or 1, self.month or 12
        return (
            date(self.year, first_month, self.day or 1),
            date(self.year, last_month, self.day or calendar.monthrange(self.year, last_month)[1]),
        )


class EvidencedRecord(ExtractionModel):
    # Recomputed by BE, including when an extractor supplies these fields.
    review_issues: list[Literal["missing_identity", "conflicting_dates", "conflicting_current_role"]] = Field(
        default_factory=list, max_length=3
    )

    def identity_fields(self) -> tuple[str, ...]:
        return ()

    @model_validator(mode="after")
    def require_evidenced_field(self) -> "EvidencedRecord":
        facts = (getattr(self, name) for name in type(self).model_fields)
        if not any(
            isinstance(fact, ExtractedValue)
            and (fact.evidence or fact.alternatives)
            for fact in facts
        ):
            raise ValueError("A section item must contain at least one evidenced fact")
        self.review_issues = []
        identity = self.identity_fields()
        if identity and not any(getattr(self, name).value is not None for name in identity):
            self.review_issues.append("missing_identity")
        return self


class ProfileLink(EvidencedRecord):
    label: ExtractedValue[ShortText]
    url: ExtractedValue[HttpUrl]

    def identity_fields(self) -> tuple[str, ...]:
        return ("url",)


class PersonalInfo(ExtractionModel):
    full_name: ExtractedValue[ShortText]
    email: ExtractedValue[EmailStr]
    phone: ExtractedValue[ShortText]
    location: ExtractedValue[ShortText]
    links: ExtractedSection[ProfileLink]


class Skill(EvidencedRecord):
    name: ExtractedValue[ShortText]
    level: ExtractedValue[ShortText]

    def identity_fields(self) -> tuple[str, ...]:
        return ("name",)


class DatedRecord(EvidencedRecord):
    start_date: ExtractedValue[ResumeDate]
    end_date: ExtractedValue[ResumeDate]

    @model_validator(mode="after")
    def validate_date_order(self) -> "DatedRecord":
        start, end = self.start_date.value, self.end_date.value
        if start is not None and end is not None and start.bounds()[0] > end.bounds()[1]:
            if "conflicting_dates" not in self.review_issues:
                self.review_issues.append("conflicting_dates")
        return self


class Experience(DatedRecord):
    organization: ExtractedValue[ShortText]
    role: ExtractedValue[ShortText]
    location: ExtractedValue[ShortText]
    is_current: ExtractedValue[StrictBool]
    description: ExtractedValue[LongText]

    def identity_fields(self) -> tuple[str, ...]:
        return ("organization", "role")

    @model_validator(mode="after")
    def validate_current_role(self) -> "Experience":
        if self.is_current.value is True and self.end_date.value is not None:
            if "conflicting_current_role" not in self.review_issues:
                self.review_issues.append("conflicting_current_role")
        return self


class Education(DatedRecord):
    institution: ExtractedValue[ShortText]
    degree: ExtractedValue[ShortText]
    field_of_study: ExtractedValue[ShortText]

    def identity_fields(self) -> tuple[str, ...]:
        return ("institution", "degree")


class Certification(EvidencedRecord):
    name: ExtractedValue[ShortText]
    issuer: ExtractedValue[ShortText]
    issued_date: ExtractedValue[ResumeDate]
    expiry_date: ExtractedValue[ResumeDate]

    def identity_fields(self) -> tuple[str, ...]:
        return ("name",)

    @model_validator(mode="after")
    def validate_date_order(self) -> "Certification":
        issued, expiry = self.issued_date.value, self.expiry_date.value
        if issued is not None and expiry is not None and issued.bounds()[0] > expiry.bounds()[1]:
            if "conflicting_dates" not in self.review_issues:
                self.review_issues.append("conflicting_dates")
        return self


class ResumeLanguage(EvidencedRecord):
    name: ExtractedValue[ShortText]
    proficiency: ExtractedValue[ShortText]

    def identity_fields(self) -> tuple[str, ...]:
        return ("name",)


class ResumeExtractionOutput(ExtractionModel):
    schema_version: Literal["1.0"]
    source_text_sha256: str = Field(
        ..., strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    personal_info: PersonalInfo
    summary: ExtractedValue[LongText]
    skills: ExtractedSection[Skill]
    experiences: ExtractedSection[Experience]
    education: ExtractedSection[Education]
    certifications: ExtractedSection[Certification]
    languages: ExtractedSection[ResumeLanguage]

    @model_validator(mode="after")
    def limit_document_size(self) -> "ResumeExtractionOutput":
        if len(self.model_dump_json().encode("utf-8")) > MAX_EXTRACTION_BYTES:
            raise ValueError("Resume extraction output is too large")
        return self
