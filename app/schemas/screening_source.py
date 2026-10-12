"""Immutable input provenance for a screening run, independent of live CV data."""
from typing import Annotated, Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.resume_extraction import MAX_SOURCE_CHARACTERS, ResumeExtractionOutput


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
