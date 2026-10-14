"""Immutable input provenance for a screening run, independent of live CV data."""
from datetime import datetime
from typing import Annotated, Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.resume_extraction import MAX_SOURCE_CHARACTERS, ResumeExtractionOutput
from app.schemas.resume_profile_review import HRConfirmedFact
from app.utils.time import ensure_utc


VersionName = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=150)
]


class ScreeningSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", hide_input_in_errors=True)

    snapshot_version: Literal["1.0"] = "1.0"
    resume_profile_id: PydanticObjectId
    profile_version: int = Field(..., strict=True, ge=1)
    extraction_run_id: PydanticObjectId
    source_parse_revision: int = Field(..., strict=True, ge=0)
    source_parse_run_id: PydanticObjectId | None = None
    resume_checksum: str = Field(..., strict=True, min_length=32, max_length=128)
    source_text: str = Field(..., strict=True, min_length=1, max_length=MAX_SOURCE_CHARACTERS)
    source_text_sha256: str = Field(..., strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    profile: ResumeExtractionOutput
    extraction_model_name: VersionName
    extraction_prompt_version: VersionName
    extractor_version: VersionName
    profile_origin: Literal["ai", "hr"] = "ai"
    profile_edited_by: PydanticObjectId | None = None
    profile_edit_reason: str | None = Field(None, min_length=1, max_length=1000)
    profile_created_at: datetime | None = None
    hr_annotations: list[HRConfirmedFact] = Field(default_factory=list, max_length=100)

    @field_validator("profile_created_at")
    @classmethod
    def profile_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_hr_provenance(self):
        if self.profile_origin == "hr":
            if not self.profile_edited_by or not self.profile_created_at or not self.profile_edit_reason or not self.profile_edit_reason.strip():
                raise ValueError("HR profile requires reviewer, timestamp and reason")
        elif self.hr_annotations or self.profile_edited_by or self.profile_edit_reason:
            raise ValueError("AI profile cannot claim HR confirmations")
        if len({fact.id for fact in self.hr_annotations}) != len(self.hr_annotations):
            raise ValueError("Duplicate HR confirmation identity")
        return self
