"""HR-confirmed facts remain separate from facts evidenced by the CV."""
from datetime import datetime
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.resume_extraction import ResumeExtractionOutput
from app.utils.time import ensure_utc


class HRFactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True,
                             revalidate_instances="always", hide_input_in_errors=True)
    section: Literal["personal_info", "summary", "skills", "experiences", "education", "certifications", "languages"]
    label: str = Field(..., strict=True, min_length=1, max_length=100)
    value: str = Field(..., strict=True, min_length=1, max_length=2000)
    reason: str = Field(..., strict=True, min_length=1, max_length=1000)


class HRConfirmedFact(HRFactInput):
    id: PydanticObjectId
    confirmed_by: PydanticObjectId
    confirmed_at: datetime

    _utc = field_validator("confirmed_at")(ensure_utc)


class ProfileRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    base_profile_id: PydanticObjectId
    expected_version: int = Field(..., strict=True, ge=1)
    reason: str = Field(..., strict=True, min_length=1, max_length=1000)
    # This part still requires exact CV quotes. Information learned from HR
    # belongs in hr_annotations and must never manufacture a CV citation.
    profile: ResumeExtractionOutput | None = None
    hr_annotations: list[HRFactInput] | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def validate_edit(self):
        if self.profile is None and self.hr_annotations is None:
            raise ValueError("Provide profile corrections or HR-confirmed facts")
        if self.hr_annotations is not None:
            keys = [(fact.section, fact.label.casefold()) for fact in self.hr_annotations]
            if len(set(keys)) != len(keys):
                raise ValueError("HR fact labels must be unique within each section")
        return self
