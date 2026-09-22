from datetime import datetime
from typing import Any, Dict, List, Optional

import json

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.candidate import CandidateStatus


class CandidateCreate(BaseModel):
    company_id: str
    full_name: str = Field(..., min_length=1, max_length=150)
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = Field(None, max_length=32)
    location: Optional[str] = Field(None, max_length=200)
    source: str = Field("manual", max_length=50)
    tags: List[str] = Field(default_factory=list, max_length=50)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    user_id: Optional[str] = None

    @field_validator("company_id", "user_id")
    @classmethod
    def validate_object_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        from bson import ObjectId

        if not ObjectId.is_valid(value):
            raise ValueError("Invalid object ID")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if value.strip()]
        if any(len(value) > 50 for value in cleaned):
            raise ValueError("Each tag must be at most 50 characters")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Tags must be unique")
        return cleaned

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if len(value) > 50 or len(json.dumps(value, default=str).encode("utf-8")) > 16_384:
            raise ValueError("Candidate metadata is too large")
        return value


class CandidateUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=150)
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = Field(None, max_length=32)
    location: Optional[str] = Field(None, max_length=200)
    source: Optional[str] = Field(None, max_length=50)
    status: Optional[CandidateStatus] = None
    tags: Optional[List[str]] = Field(None, max_length=50)
    metadata: Optional[Dict[str, Any]] = None

    _tags = field_validator("tags")(CandidateCreate.validate_tags.__func__)
    _metadata = field_validator("metadata")(CandidateCreate.validate_metadata.__func__)


class CandidateResponse(BaseModel):
    id: str
    company_id: str
    user_id: Optional[str] = None
    full_name: str
    email: Optional[str] = None
    phone_number: Optional[str] = None
    location: Optional[str] = None
    source: str
    status: CandidateStatus
    tags: List[str]
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CandidateListResponse(BaseModel):
    items: List[CandidateResponse]
    total: int
    page: int
    size: int
