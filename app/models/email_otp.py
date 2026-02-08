from beanie import Document, Indexed
from pydantic import Field, EmailStr
from datetime import datetime
from typing import Optional
from bson import ObjectId
from app.utils.time import now_vn


class EmailOTP(Document):
    email: str = Field(..., description="Email address")
    otp_code: str = Field(..., max_length=6, min_length=6)
    otp_type: str = Field(...)
    expires_at: datetime = Field(...)
    attempts: int = 0
    max_attempts: int = 3
    is_used: bool = False
    created_at: datetime = Field(default_factory=now_vn)
    updated_at: datetime = Field(default_factory=now_vn)
    class Settings:
        name = "email_otps"
        indexes = [
            [("email", 1)],
            [("expires_at", 1)],
            [("otp_type", 1)],
            [("is_used", 1)],
            [("created_at", -1)],
            [("email", 1), ("otp_type", 1)],
        ]

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda dt: dt.isoformat()
        }

    @property
    def is_expired(self) -> bool:
        from datetime import datetime
        return datetime.now() > self.expires_at

    @property
    def can_attempt(self) -> bool:
        return self.attempts < self.max_attempts and not self.is_used and not self.is_expired

    def increment_attempt(self) -> None:
        self.attempts += 1
        self.updated_at = now_vn()

    def mark_as_used(self) -> None:
        self.is_used = True
        self.updated_at = now_vn()