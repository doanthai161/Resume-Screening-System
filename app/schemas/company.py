from typing import Optional
from pydantic import BaseModel, EmailStr
from bson import ObjectId

class CompanyCreate(BaseModel):
    user_id: ObjectId
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
    id: str
    user_id: str
    name: str
    company_short_name: str
    description: Optional[str]
    company_code: str
    tax_code: Optional[str]
    email: EmailStr
    logo_url: Optional[str]
    website: str
    is_active: bool

class CompanyListResponse(BaseModel):
    companies: list[CompanyResponse]
    total: int
    page: int
    size: int