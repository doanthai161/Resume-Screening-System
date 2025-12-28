from datetime import datetime, timedelta, timezone
from beanie import Document
from pydantic import EmailStr, Field

class EmailOTP(Document):
    email: EmailStr
    otp: str
    expires_at: datetime
    is_used: bool = False

    class Settings:
        name = "email_otps"
