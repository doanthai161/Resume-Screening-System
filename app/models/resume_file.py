# app/models/resume_file.py
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl
from beanie import Document
from bson import ObjectId
from pymongo import IndexModel
from app.utils.time import now_vn


class ParsedResumeData(BaseModel):
    personal_info: Dict[str, Any] = Field(default_factory=dict)
    summary: Optional[str] = Field(None)
    skills: List[str] = Field(default_factory=list)
    experiences: List[Dict[str, Any]] = Field(default_factory=list)
    education: List[Dict[str, Any]] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    raw_text: Optional[str] = Field(None)
    parser_version: str = Field("1.0.0")
    confidence_score: float = Field(0.0, ge=0.0, le=1.0)
    parsed_at: datetime = Field(default_factory=lambda: now_vn())

class ResumeFile(Document):
    filename: str = Field(..., description="Tên file trong hệ thống")
    original_filename: str = Field(..., description="Tên file gốc")
    file_path: str = Field(..., description="Đường dẫn file")
    file_size: int = Field(..., description="Kích thước (bytes)")
    mime_type: str = Field(..., description="Loại file")
    uploader_id: ObjectId = Field(..., description="ID người upload")
    user_id: Optional[ObjectId] = Field(None, description="ID ứng viên")
    company_branch_id: Optional[ObjectId] = Field(None, description="ID chi nhánh công ty")
    checksum: str = Field(..., description="Checksum để tránh duplicate")
    
    # Parsed data
    parsed_data: Optional[ParsedResumeData] = Field(None)
    
    # Status
    status: str = Field("pending", pattern="^(pending|processing|parsed|error)$")
    processing_errors: List[str] = Field(default_factory=list)
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Timestamps
    uploaded_at: datetime = Field(default_factory=lambda: now_vn())
    processed_at: Optional[datetime] = Field(None)
    last_accessed_at: Optional[datetime] = Field(None)
    
    class Settings:
        name = "resume_files"
        indexes = [
            IndexModel([("uploader_id", 1)], name="idx_resume_files_uploader"),
            IndexModel([("user_id", 1)], name="idx_resume_files_user", sparse=True),
            IndexModel([("company_branch_id", 1)], name="idx_resume_files_company", sparse=True),
            IndexModel([("checksum", 1)], name="idx_resume_files_checksum", unique=True),
            IndexModel([("status", 1)], name="idx_resume_files_status"),
            IndexModel([("uploaded_at", -1)], name="idx_resume_files_uploaded_desc"),
            IndexModel([("parsed_data.skills", 1)], name="idx_resume_files_skills"),
        ]
    
    class Config:
        arbitrary_types_allowed = True