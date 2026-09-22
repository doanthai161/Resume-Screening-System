from datetime import datetime
from typing import Optional
from beanie import Document, Indexed, PydanticObjectId
from pydantic import EmailStr, Field, ConfigDict, field_validator
from pymongo import ASCENDING, DESCENDING, IndexModel
from app.utils.time import now_utc


class User(Document):
    email: Indexed(str, unique=True) = Field(..., description="User email, unique")
    username: Optional[str] = Field(None, min_length=3, max_length=50, description="Optional public username")
    full_name: Optional[str] = Field(None, max_length=100, description="Full name")
    hashed_password: str = Field(..., description="Hash of user password")
    address: Optional[str] = Field(None, max_length=200, description="Address")
    phone_number: Optional[str] = Field(None, max_length=15, description="Phone number")
    is_active: bool = Field(True, description="Is account active")
    is_verified: bool = Field(False, description="Is email verified")
    is_superuser: bool = Field(False, description="Is superuser")
    auth_version: int = Field(0, ge=0, description="Incremented to revoke all existing tokens")
    verified_at: Optional[datetime] = Field(None, description="Verify at")
    last_login: Optional[datetime] = Field(None, description="Last login timestamp")
    phone_verified_at: Optional[datetime] = Field(None, description="Phone verification timestamp")
    deactivated_at: Optional[datetime] = Field(None, description="Account deactivation timestamp")
    deactivated_by: Optional[PydanticObjectId] = Field(None, description="User who deactivated this account")
    deleted_at: Optional[datetime] = Field(None, description="Soft-delete timestamp")
    deleted_by: Optional[PydanticObjectId] = Field(None, description="ID of the user who soft-deleted this account")
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return str(value).strip().lower()

    @field_validator("phone_number", mode="before")
    @classmethod
    def normalize_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        raw = value.strip()
        prefix = "+" if raw.startswith("+") else ""
        digits = "".join(character for character in raw if character.isdigit())
        return f"{prefix}{digits}" or None
    
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={
            datetime: lambda dt: dt.isoformat()
        }
    )

    class Settings:
        name = "users"
        indexes = [
            IndexModel(
                [("username", ASCENDING)],
                unique=True,
                name="uq_user_username",
                partialFilterExpression={"username": {"$type": "string"}},
            ),
            IndexModel([("full_name", ASCENDING)]),
            IndexModel(
                [("phone_number", ASCENDING)],
                unique=True,
                name="uq_user_phone",
                partialFilterExpression={"phone_number": {"$type": "string"}},
            ),
            IndexModel([("is_active", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]
