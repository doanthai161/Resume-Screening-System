from typing import Optional
from pydantic import BaseModel, EmailStr
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
    name: Optional[str]
    company_short_name: Optional[str]
    description: Optional[str]
    company_code: Optional[str]
    tax_code: Optional[str]
    email: Optional[EmailStr]
    logo_url: Optional[str]
    website: Optional[str]
    is_active: Optional[bool]

class CompanyResponse(BaseModel):
    user_id: str
    name: str
    company_short_name: str
    description: Optional[str]
    company_code: str
    tax_code: Optional[str]
    email: EmailStr
    logo_url: Optional[str]
    website: str
    created_at: datetime

class CompanyListResponse(BaseModel):
    companies: list[CompanyResponse]
    total: int
    page: int
    size: int