from beanie import Document, Indexed
from pydantic import Field, field_validator
from datetime import datetime
from typing import Optional
from bson import ObjectId
from app.utils.time import now_utc, is_expired_check
from pymongo import ASCENDING, DESCENDING, IndexModel


class EmailOTP(Document):
    email: str = Field(..., description="Email address")
    otp_code: Optional[str] = Field(None, max_length=6, min_length=6, description="Legacy plaintext OTP")
    otp_hash: Optional[str] = Field(None, min_length=64, max_length=64)
    otp_type: str = Field(...)
    expires_at: datetime = Field(...)
    attempts: int = 0
    max_attempts: int = 3
    is_used: bool = False
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return str(value).strip().lower()

    class Settings:
        name = "email_otps"
        indexes = [
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_email_otp"),
            IndexModel([("email", ASCENDING), ("otp_type", ASCENDING), ("is_used", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda dt: dt.isoformat()
        }

    @property
    def is_expired(self) -> bool:
        return is_expired_check(self.expires_at)

    @property
    def can_attempt(self) -> bool:
        return self.attempts < self.max_attempts and not self.is_used and not self.is_expired

    def increment_attempt(self) -> None:
        self.attempts += 1
        self.updated_at = now_utc()

    def mark_as_used(self) -> None:
        self.is_used = True
        self.updated_at = now_utc()
