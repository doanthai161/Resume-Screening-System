from typing import Optional
from app.schemas.user import UserResponse
from app.schemas.company_branch import CompanyBranchResponse
from datetime import datetime
from pydantic import BaseModel

class AssignUserToCompanyBranch(BaseModel):
    user_id: str
    company_branch_id: str

class ListUserCompanyBranchResponse:
    company_branch_id:str
    users: list[UserResponse]


class UserCompanyStats(BaseModel):
    total_users: int
    active_users: int
    inactive_users: int
    total_branches: int
    active_branches: int
    inactive_branches: int

class UserCompanyResponse(BaseModel):
    id: str
    user: UserResponse
    company_branch: CompanyBranchResponse
    role: str
    permissions: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

class UserCompanyListResponse(BaseModel):
    users: list[UserCompanyResponse]
    total: int
    page: int
    size: int
    total_pages: int
    has_next: bool
    has_previous: bool
    next_page: int
    previous_page: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool