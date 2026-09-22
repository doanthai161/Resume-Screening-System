from datetime import datetime
import json
from typing import Any, Dict, List, Optional

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field, model_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.utils.time import now_utc


class ParsedResumeData(BaseModel):
    personal_info: Dict[str, Any] = Field(default_factory=dict, max_length=50)
    summary: Optional[str] = Field(None, max_length=5000)
    skills: List[str] = Field(default_factory=list, max_length=500)
    experiences: List[Dict[str, Any]] = Field(default_factory=list, max_length=200)
    education: List[Dict[str, Any]] = Field(default_factory=list, max_length=100)
    certifications: List[str] = Field(default_factory=list, max_length=200)
    languages: List[str] = Field(default_factory=list, max_length=100)
    raw_text: Optional[str] = Field(None, max_length=1_000_000)
    parser_version: str = Field("1.0.0", min_length=1, max_length=50)
    confidence_score: float = Field(0.0, ge=0.0, le=1.0)
    parsed_at: datetime = Field(default_factory=now_utc)

    @model_validator(mode="after")
    def limit_document_size(self) -> "ParsedResumeData":
        payload = json.dumps(self.model_dump(mode="json"), default=str).encode("utf-8")
        if len(payload) > 2_000_000:
            raise ValueError("Parsed resume data is too large")
        return self


class ResumeFile(Document):
    # company_id/candidate_id are optional for legacy reads only. New service
    # writes require both tenant ownership and a stable candidate identity.
    company_id: Optional[PydanticObjectId] = None
    candidate_id: Optional[PydanticObjectId] = None
    filename: str = Field(..., min_length=1, max_length=255)
    original_filename: str = Field(..., min_length=1, max_length=255)
    file_path: str = Field(..., min_length=1, max_length=1000)
    file_size: int = Field(..., gt=0)
    mime_type: str = Field(..., min_length=1, max_length=150)
    uploader_id: PydanticObjectId
    user_id: Optional[PydanticObjectId] = None
    company_branch_id: Optional[PydanticObjectId] = None
    checksum: str = Field(..., min_length=32, max_length=128)
    storage_provider: str = Field("local", max_length=30)
    object_key: Optional[str] = Field(None, max_length=1000)
    version: int = Field(1, ge=1)
    parsed_data: Optional[ParsedResumeData] = None
    status: str = Field("pending", pattern=r"^(pending|processing|parsed|error)$")
    processing_errors: List[str] = Field(default_factory=list, max_length=50)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    uploaded_at: datetime = Field(default_factory=now_utc)
    processed_at: Optional[datetime] = None
    last_accessed_at: Optional[datetime] = None
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[PydanticObjectId] = None

    class Settings:
        name = "resume_files"
        indexes = [
            IndexModel(
                [("company_id", ASCENDING), ("candidate_id", ASCENDING), ("checksum", ASCENDING)],
                unique=True,
                name="uq_resume_candidate_checksum",
                partialFilterExpression={
                    "company_id": {"$type": "objectId"},
                    "candidate_id": {"$type": "objectId"},
                    "is_deleted": False,
                },
            ),
            IndexModel(
                [("storage_provider", ASCENDING), ("object_key", ASCENDING)],
                unique=True,
                name="uq_resume_storage_object",
                partialFilterExpression={"object_key": {"$type": "string"}},
            ),
            IndexModel([("candidate_id", ASCENDING), ("uploaded_at", DESCENDING)]),
            IndexModel([("company_id", ASCENDING), ("status", ASCENDING), ("uploaded_at", DESCENDING)]),
            IndexModel([("uploader_id", ASCENDING), ("uploaded_at", DESCENDING)]),
            IndexModel([("parsed_data.skills", ASCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
