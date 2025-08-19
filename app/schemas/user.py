from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from fastapi import HTTPException
from app.dependencies.error_code import ErrorCode

class UserCreate(BaseModel):
    email: EmailStr
    hashed_password: str
    role: int
    address: Optional[str] = None

    @field_validator("password")
    def validate_password(cls, v):
        if len(v) < 6:
            raise HTTPException(
                status_code=400, detail=ErrorCode.PASSWORD_MUST_BE_AT_LEAST_6_CHARACTERS
            )
        return v

class UserUpdate(BaseModel):
    role: Optional[int]
    address: Optional[str]

class UserResponse(BaseModel):
    id: str
    email: EmailStr
    role:int
    address: Optional[str]

class UserListRespponse(BaseModel):
    users: list[UserResponse]
    total: int
    page: int
    size: int