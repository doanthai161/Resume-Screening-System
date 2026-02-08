from datetime import datetime
from typing import Optional
from beanie import Document, Indexed
from pydantic import EmailStr, Field, ConfigDict
from app.utils.time import now_vn


class User(Document):
    email: Indexed(str, unique=True) = Field(..., description="User email, unique")
    full_name: Optional[str] = Field(None, max_length=100, description="Full name")
    hashed_password: str = Field(..., description="Hash of user password")
    address: Optional[str] = Field(None, max_length=200, description="Address")
    phone_number: Optional[str] = Field(None, max_length=15, description="Phone number")
    is_active: bool = Field(True, description="Is account active")
    is_verified: bool = Field(False, description="Is email verified")
    is_superuser: bool = Field(False, description="Is superuser")
    last_login: Optional[datetime] = Field(None, description="Last login timestamp")
    created_at: datetime = Field(default_factory=now_vn)
    updated_at: datetime = Field(default_factory=now_vn)
    
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={
            datetime: lambda dt: dt.isoformat()
        }
    )

    class Settings:
        name = "users"
        indexes = [
            [("email", 1)],
            [("full_name", 1)],
            [("phone_number", 1)],
            [("is_active", 1)],
            [("created_at", -1)],
        ]