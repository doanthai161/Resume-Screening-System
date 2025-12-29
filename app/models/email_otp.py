from datetime import datetime, timedelta, timezone
from beanie import Document
from pydantic import EmailStr, Field
from app.utils.time import now_vn

class EmailOTP(Document):
    email: EmailStr
    otp: str
    expires_at: datetime = Field(default_factory=lambda: now_vn())
    is_used: bool = False

    class Settings:
        name = "email_otps"
