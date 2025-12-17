from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import EmailStr, Field
from pymongo import IndexModel

class User(Document):
    email: EmailStr = Field(..., description="User email, unique")
    full_name: Optional[str] = Field(None, max_length=100, description="Full name")
    hashed_password: str = Field(..., description="Hash of user password")
    address: Optional[str] = Field(None, max_length=200, description="Address")
    phone_number: Optional[str] = Field(None, max_length=15, description="Phone number")
    is_active: bool = Field(True, description="Is account active")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    class Settings:
        name = "users"
        indexes = [
            IndexModel([("email", 1)], name="idx_users_email", unique=True),
            IndexModel([("full_name", 1)], name="idx_users_full_name", unique=True),
        ]

    class Config:
        arbitrary_types_allowed = True

