from typing import Optional, Dict, List
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from fastapi import HTTPException
from app.dependencies.error_code import ErrorCode
from app.schemas.actor import ActorResponse
from datetime import datetime
from enum import Enum


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "password123"
            }
        }
class UserCreate(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    password: str
    address: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str):
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password too long")
        return v
    @field_validator('phone_number')
    def validate_phone_number(cls, v):
        if v and not v.isdigit():
            raise ValueError('Phone number must contain only digits')
        return v

class UserUpdate(BaseModel):
    full_name: Optional[str]
    phone_number: Optional[str]
    address: Optional[str]

class UserResponse(BaseModel):
    id: Optional[str]
    email: EmailStr
    full_name: Optional[str]= None
    phone_number: Optional[str] =None
    address: Optional[str]= None
    message:Optional[str]= None

class UserListRespponse(BaseModel):
    users: list[UserResponse]
    total: int
    page: int
    size: int

class RegisterRequest(BaseModel):
    email: EmailStr
    phone_number: str
    address: Optional[str]
    password: str
    full_name: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str):
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

class VerifyOTPRegisterRequest(BaseModel):
    email: EmailStr
    otp: str

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
    email: Optional[str] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    is_verified: Optional[bool] = None
    role: Optional[str] = None
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
    user_ids: List[str]
    update_data: UserUpdate

class UserBulkDeactivate(BaseModel):
    user_ids: List[str]

class UserChangePassword(BaseModel):
    current_password: str
    new_password: str

    @field_validator('new_password')
    def validate_new_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v

class UserResetPasswordRequest(BaseModel):
    email: EmailStr

class UserResetPasswordConfirm(BaseModel):
    token: str
    new_password: str

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