from typing import Optional
from pydantic import BaseModel
from bson import ObjectId

class CompanyBranchCreate(BaseModel):
    company_id: ObjectId
    bussiness_type: str
    branch_name: str
    phone_number: Optional[str] = None
    address: str
    description: Optional[str] = None
    company_type: Optional[str] = None
    company_industry: Optional[str] = None
    country: Optional[str] = None
    company_size: int
    working_days: list[str]
    overtime_policy: Optional[str] = None

class CompanyBranchUpdate(BaseModel):
    bussiness_type: Optional[str]
    branch_name: Optional[str]
    phone_number: Optional[str]
    address: Optional[str]
    description: Optional[str]
    company_type: Optional[str]
    company_industry: Optional[str]
    country: Optional[str]
    company_size: Optional[int]
    working_days: Optional[list[str]]
    overtime_policy: Optional[str]
    is_active: Optional[bool]

class CompanyBranchResponse(BaseModel):
    id: str
    company_id: str
    bussiness_type: str
    branch_name: str
    phone_number: Optional[str]
    address: str
    description: Optional[str]
    company_type: Optional[str]
    company_industry: Optional[str]
    country: Optional[str]
    company_size: int
    working_days: list[str]
    overtime_policy: Optional[str]
    is_active: bool

class CompanyBranchListResponse(BaseModel):
    company_branches: list[CompanyBranchResponse]
    total: int
    page: int
    size: int