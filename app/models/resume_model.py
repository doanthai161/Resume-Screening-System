from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
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
    raw_text: Optional[str] = Field(None, description="Toàn bộ text extract từ CV")
    parser_version: str = Field("1.0", description="Version của parser")
    confidence_score: float = Field(0.0, ge=0.0, le=1.0, description="Độ tin cậy parse")
    parsed_at: datetime = Field(default_factory=lambda: now_vn())

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
    parsed_data: Optional[ParsedResumeData] = Field(None, description="Dữ liệu đã parse từ CV")
    status: str = Field("pending", description="Trạng thái: pending, processing, processed, error")
    processing_errors: List[str] = Field(default_factory=list, description="Lỗi khi xử lý")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata bổ sung")
    uploaded_at: datetime = Field(default_factory=lambda: now_vn())
    processed_at: Optional[datetime] = Field(None, description="Thời điểm xử lý xong")
    last_accessed_at: Optional[datetime] = Field(None, description="Thời điểm truy cập gần nhất")
    
    class Settings:
        name = "resume_files"
        indexes = [
            [("uploader_id", 1)],
            [("user_id", 1)],
            [("company_branch_id", 1)],
            [("checksum", 1)],
            [("status", 1)],
            [("uploaded_at", -1)],
            [("processed_at", 1)],
            [("parsed_data.skills", 1)],
            [("parsed_data.languages", 1)],
            [("parsed_data.certifications", 1)],
            [("parsed_data.confidence_score", -1)],
            [("company_branch_id", 1), ("status", 1)],
            [("company_branch_id", 1), ("uploaded_at", -1)],
            [("uploader_id", 1), ("uploaded_at", -1)],
            [("status", 1), ("processed_at", 1)],
        ]
    
    class Config:
        arbitrary_types_allowed = True
    
    @property
    def is_processed(self) -> bool:
        return self.status in ["processed", "parsed"] and self.parsed_data is not None
    
    @property
    def has_skills(self) -> bool:
        return self.is_processed and len(self.parsed_data.skills) > 0
    
    @property
    def processing_time(self) -> Optional[float]:
        if self.processed_at and self.uploaded_at:
            return (self.processed_at - self.uploaded_at).total_seconds()
        return None
    
    def mark_as_processed(self, parsed_data: ParsedResumeData) -> None:
        self.parsed_data = parsed_data
        self.status = "processed"
        self.processed_at = now_vn()
    
    def mark_as_error(self, error_messages: List[str]) -> None:
        self.status = "error"
        self.processing_errors = error_messages
        self.processed_at = now_vn()