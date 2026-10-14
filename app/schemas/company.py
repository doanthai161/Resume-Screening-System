from typing import Optional
from pydantic import BaseModel, EmailStr, ConfigDict, field_validator
from datetime import datetime

class CompanyCreate(BaseModel):
    name: str
    company_short_name: str
    description: Optional[str] = None
    company_code: str
    tax_code: Optional[str] = None
    email: EmailStr
    logo_url: Optional[str] = None
    website: str

class CompanyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    company_short_name: Optional[str] = None
    description: Optional[str] = None
    company_code: Optional[str] = None
    tax_code: Optional[str] = None
    email: Optional[EmailStr] = None
    logo_url: Optional[str] = None
    website: Optional[str] = None

    @field_validator("name", "company_short_name", "company_code", "email", "website")
    @classmethod
    def required_values_cannot_be_cleared(cls, value):
        if value is None or not str(value).strip():
            raise ValueError("Required company fields cannot be null or blank")
        return value

class CompanyResponse(BaseModel):
    id: str
    user_id: str
    name: str
    company_short_name: str
    description: Optional[str]
    industry: Optional[str]
    company_code: str
    tax_code: Optional[str]
    email: EmailStr
    logo_url: Optional[str]
    website: str
    created_at: datetime
    is_active: bool
    updated_at: datetime

class CompanyListResponse(BaseModel):
    companies: list[CompanyResponse]
    total: int
    page: int
    size: int
