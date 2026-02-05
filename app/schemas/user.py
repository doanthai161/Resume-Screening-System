from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from fastapi import HTTPException
from app.dependencies.error_code import ErrorCode
from app.schemas.actor import ActorResponse
from datetime import datetime


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

class UserUpdate(BaseModel):
    full_name: Optional[str]
    phone_number: Optional[str]
    address: Optional[str]

class UserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str]
    phone_number: Optional[str]
    address: Optional[str]

class UserListRespponse(BaseModel):
    users: list[UserResponse]
    total: int
    page: int
    size: int

class RegisterRequest(BaseModel):
    email: EmailStr
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

class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"

class VerifyOTPResponse(BaseModel):
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