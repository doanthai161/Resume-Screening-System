from pydantic import BaseModel, EmailStr

class RequestOTPRequest(BaseModel):
    email: EmailStr

class VerifyOTPRegisterRequest(BaseModel):
    email: EmailStr
    otp: str
    password: str
