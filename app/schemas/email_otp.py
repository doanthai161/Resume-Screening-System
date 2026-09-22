from pydantic import BaseModel, EmailStr, field_validator


def _normalize_email(value: str) -> str:
    return str(value).strip().lower()

class RequestOTPRequest(BaseModel):
    email: EmailStr

    _normalize_email = field_validator("email", mode="before")(_normalize_email)

class VerifyOTPRegisterRequest(BaseModel):
    email: EmailStr
    otp: str
    password: str

    _normalize_email = field_validator("email", mode="before")(_normalize_email)
