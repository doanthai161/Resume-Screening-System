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
