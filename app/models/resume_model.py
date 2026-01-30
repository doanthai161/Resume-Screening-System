from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl
from beanie import Document
from bson import ObjectId
from app.utils.time import now_vn
from pymongo import IndexModel

class ResumeFile(Document):
    filename: str = Field(..., description="Tên file lưu trong hệ thống")
    original_filename: str = Field(..., description="Tên file gốc từ người dùng")
    file_path: str = Field(..., description="Đường dẫn lưu file")
    file_size: int = Field(..., description="Kích thước file (bytes)")
    mime_type: str = Field(..., description="Loại file (pdf/docx)")
    uploader_id: ObjectId = Field(..., description="ID người upload")
    user_id: Optional[ObjectId] = Field(None, description="ID ứng viên (nếu có)")
    company_branch_id: ObjectId = Field(..., description="ID chi nhánh công ty")
    checksum: str = Field(..., description="Checksum để tránh duplicate")
    status: str = Field("pending", description="Trạng thái: pending, processing, processed, error")
    processing_errors: List[str] = Field(default_factory=list, description="Lỗi khi xử lý")
    uploaded_at: datetime = Field(default_factory=lambda: now_vn())
    processed_at: Optional[datetime] = Field(None, description="Thời điểm xử lý xong")
    
    class Settings:
        name = "resume_files"
        indexes = [
            IndexModel([("uploader_id", 1)], name="idx_resume_files_uploader"),
            IndexModel([("user_id", 1)], name="idx_resume_files_user", sparse=True),
            IndexModel([("company_branch_id", 1)], name="idx_resume_files_company"),
            IndexModel([("checksum", 1)], name="idx_resume_files_checksum", unique=True),
            IndexModel([("status", 1)], name="idx_resume_files_status"),
            IndexModel([("uploaded_at", -1)], name="idx_resume_files_uploaded_desc"),
        ]
    
    class Config:
        arbitrary_types_allowed = True

class ParsedResumeData(BaseModel):
    """Dữ liệu đã parse từ CV (embedded trong ResumeFile)"""
    personal_info: Dict[str, Any] = Field(default_factory=dict)
    summary: Optional[str] = Field(None)
    skills: List[str] = Field(default_factory=list)
    experiences: List[Dict[str, Any]] = Field(default_factory=list)
    education: List[Dict[str, Any]] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    raw_text: Optional[str] = Field(None, description="Toàn bộ text extract từ CV")
    parser_version: str = Field("1.0", description="Version của parser")
    confidence_score: float = Field(0.0, ge=0.0, le=1.0, description="Độ tin cậy parse")

