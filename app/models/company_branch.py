from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn
from bson import ObjectId
from typing import Optional, List
from pymongo import IndexModel

class CompanyBranch(Document):
    company_id: ObjectId = Field(..., description="ID of the parent company")
    bussiness_type: str = Field(..., description="Type of business the branch is involved in")
    branch_name: str = Field(..., description="Name of the company branch")
    phone_number: Optional[str] = Field(None, description="Contact phone number for the branch")
    address: str = Field(..., description="Physical address of the branch")
    city: Optional[str] = Field(None, description="City where the branch is located")
    description: Optional[str] = Field(None, description="Description of the branch")
    company_type: Optional[str] = Field(None, description="Type of the company branch")
    company_industry: Optional[str] = Field(None, description="Industry sector of the company branch")
    country: Optional[str] = Field(None, description="Country where the branch is located")
    company_size: int = Field(..., description="Number of employees in the branch")
    working_days: List[str] = Field(..., description="Working days of the branch")
    overtime_policy: Optional[str] = Field(None, description="Overtime policy of the branch")
    is_active: bool = Field(default=True, description="Is the branch active?")
    created_by: ObjectId = Field(..., description="ID of the user who created the branch")
    updated_by: Optional[ObjectId] = Field(None, description="ID of the user who last updated the branch")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "company_branches"
        indexes = [
            {"key": [("company_id", 1)], "name": "idx_company_branches_company_id"},
            {"key": [("branch_name", 1)], "name": "idx_company_branches_branch_name"},
            {"key": [("is_active", 1)], "name": "idx_company_branches_is_active"},
            {"key": [("city", 1)], "name": "idx_company_branches_city", "sparse": True},
            {"key": [("country", 1)], "name": "idx_company_branches_country", "sparse": True},
            {"key": [("company_id", 1), ("is_active", 1)], "name": "idx_company_branches_company_active"},
            {"key": [("city", 1), ("country", 1)], "name": "idx_company_branches_location", "sparse": True},
        ]
    class Config:
        arbitrary_types_allowed = True