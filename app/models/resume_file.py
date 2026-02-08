from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl
from beanie import Document
from bson import ObjectId
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
    
    parsed_data: Optional[ParsedResumeData] = Field(None)
    
    status: str = Field("pending", pattern="^(pending|processing|parsed|error)$")
    processing_errors: List[str] = Field(default_factory=list)
    
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    uploaded_at: datetime = Field(default_factory=lambda: now_vn())
    processed_at: Optional[datetime] = Field(None)
    last_accessed_at: Optional[datetime] = Field(None)
    
    class Settings:
        name = "resume_files"
        indexes = [
            [("uploader_id", 1)],
            [("user_id", 1)],
            [("company_branch_id", 1)],
            [("checksum", 1)],
            [("status", 1)],
            [("uploaded_at", -1)],
            [("parsed_data.skills", 1)],
            [("company_branch_id", 1), ("status", 1)],
            [("uploader_id", 1), ("uploaded_at", -1)],
        ]
    
    class Config:
        arbitrary_types_allowed = True