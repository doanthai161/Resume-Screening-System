from typing import Optional, Dict, List
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from bson import ObjectId
from fastapi import HTTPException
from app.dependencies.error_code import ErrorCode
from app.schemas.actor import ActorResponse
from datetime import datetime
from enum import Enum

from app.core.config import settings


def _validate_new_password(value: str) -> str:
    if len(value) < settings.PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters"
        )
    if len(value.encode("utf-8")) > settings.PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"Password must be at most {settings.PASSWORD_MAX_LENGTH} bytes"
        )
    if not any(character.isupper() for character in value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(character.islower() for character in value):
        raise ValueError("Password must contain at least one lowercase letter")
    return value


def _normalize_email(value: str) -> str:
    return str(value).strip().lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)

    _normalize_email = field_validator("email", mode="before")(_normalize_email)
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "password123"
            }
        }
class UserCreate(BaseModel):
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=32)
    password: str
    address: Optional[str] = Field(None, max_length=200)

    _normalize_email = field_validator("email", mode="before")(_normalize_email)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str):
        return _validate_new_password(v)
    @field_validator('phone_number')
    def validate_phone_number(cls, v):
        if v and not v.isdigit():
            raise ValueError('Phone number must contain only digits')
        return v

class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=32)
    address: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_superuser: Optional[bool] = None
    role: Optional[str] = None

    @field_validator("phone_number")
    @classmethod
    def validate_update_phone_number(cls, value: Optional[str]):
        if value and not value.isdigit():
            raise ValueError("Phone number must contain only digits")
        return value

class UserResponse(BaseModel):
    id: Optional[str]
    email: EmailStr
    full_name: Optional[str]= None
    phone_number: Optional[str] =None
    address: Optional[str]= None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    created_at: Optional[datetime] = None
    message:Optional[str]= None

    @field_validator("id", mode="before")
    @classmethod
    def serialize_id(cls, value):
        return str(value) if value is not None else None

class UserListRespponse(BaseModel):
    users: list[UserResponse]
    total: int
    page: int
    size: int

class RegisterRequest(BaseModel):
    email: EmailStr
    phone_number: str = Field(..., min_length=7, max_length=32)
    address: Optional[str] = Field(None, max_length=200)
    password: str
    full_name: str | None = Field(None, max_length=100)

    _normalize_email = field_validator("email", mode="before")(_normalize_email)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str):
        return _validate_new_password(v)

    @field_validator("phone_number")
    @classmethod
    def validate_registration_phone(cls, value: str) -> str:
        raw = value.strip()
        if raw.startswith("+"):
            raw = raw[1:]
        digits = "".join(character for character in raw if character.isdigit())
        if not digits or any(not (character.isdigit() or character in " +-()") for character in value):
            raise ValueError("Invalid phone number")
        return ("+" if value.strip().startswith("+") else "") + digits

class VerifyOTPRegisterRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., pattern=r"^\d{6}$")

    _normalize_email = field_validator("email", mode="before")(_normalize_email)

class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: Optional[int] = 0
    refresh_token: Optional[str] = None
    refresh_token_expires_in: Optional[int] = 0
    user: Optional[UserResponse] = None

class VerifyOTPResponse(BaseModel):
    success:bool
    token: AccessToken
    user: UserResponse

class UserActorResponse(BaseModel):
    user_id: str
    full_name: Optional[str]
    actor: ActorResponse


class UserFilter(BaseModel):
    email: Optional[str] = Field(None, max_length=100)
    full_name: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=32)
    is_verified: Optional[bool] = None
    role: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    created_at_from: Optional[datetime] = None
    created_at_to: Optional[datetime] = None
    updated_at_from: Optional[datetime] = None
    updated_at_to: Optional[datetime] = None

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    MANAGER = "manager"

class UserBulkUpdate(BaseModel):
    user_ids: List[str] = Field(..., min_length=1, max_length=100)
    update_data: UserUpdate

    @field_validator("user_ids")
    @classmethod
    def validate_user_ids(cls, values: List[str]) -> List[str]:
        if len(values) != len(set(values)) or any(not ObjectId.is_valid(value) for value in values):
            raise ValueError("user_ids must contain unique valid object IDs")
        return values

class UserBulkDeactivate(BaseModel):
    user_ids: List[str] = Field(..., min_length=1, max_length=100)

    _validate_user_ids = field_validator("user_ids")(UserBulkUpdate.validate_user_ids.__func__)

class UserChangePassword(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str

    @field_validator('new_password')
    def validate_new_password(cls, v):
        return _validate_new_password(v)

class UserResetPasswordRequest(BaseModel):
    email: EmailStr

    _normalize_email = field_validator("email", mode="before")(_normalize_email)

class UserResetPasswordConfirm(BaseModel):
    token: str = Field(..., min_length=32, max_length=200)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_reset_password(cls, value: str) -> str:
        return _validate_new_password(value)

class UserStatisticsResponse(BaseModel):
    total_users: int
    active_users: int
    verified_users: int
    superusers: int
    recent_signups_7d: int
    inactive_users: int
    unverified_users: int
    user_roles: Dict[str, int]
    calculated_at: str

class UserActivityStatsResponse(BaseModel):
    user_id: str
    email: str
    username: Optional[str]
    full_name: Optional[str]
    is_active: bool
    is_verified: bool
    is_superuser: bool
    account_created: Optional[str]
    account_age_days: int
    last_login: Optional[str]
    last_activity: str
    email_verified_at: Optional[str]
    phone_verified: bool
    calculated_at: str
