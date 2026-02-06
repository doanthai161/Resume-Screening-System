from bson import ObjectId
from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn
from pymongo import IndexModel
from typing import Optional, List

class UserCompany(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    company_branch_id: ObjectId = Field(..., description="ID of the company branch")
    role: str = Field(default="member", description="Role of the user in the branch")
    permissions: List[str] = Field(default_factory=list, description="Specific permissions for this assignment")
    assigned_by: ObjectId = Field(..., description="ID of the user who made this assignment")
    assigned_at: datetime = Field(default_factory=lambda: now_vn(), description="When the user was assigned")
    unassigned_by: Optional[ObjectId] = Field(None, description="ID of the user who unassigned")
    unassigned_at: Optional[datetime] = Field(None, description="When the user was unassigned")
    unassign_reason: Optional[str] = Field(None, description="Reason for unassignment")
    start_date: Optional[datetime] = Field(None, description="When the assignment becomes active")
    end_date: Optional[datetime] = Field(None, description="When the assignment expires")
    is_active: bool = Field(default=True, description="Is the assignment currently active?")
    updated_by: Optional[ObjectId] = Field(None, description="ID of the user who last updated")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "user_companies"
        indexes = [
            {"key": [("user_id", 1)], "name": "idx_user_companies_user_id"},
            {"key": [("company_branch_id", 1)], "name": "idx_user_companies_company_branch_id"},
            {"key": [("assigned_by", 1)], "name": "idx_user_companies_assigned_by"},
            {"key": [("unassigned_by", 1)], "name": "idx_user_companies_unassigned_by", "sparse": True},
            {"key": [("start_date", 1)], "name": "idx_user_companies_start_date", "sparse": True},
            {"key": [("end_date", 1)], "name": "idx_user_companies_end_date", "sparse": True},
            {"key": [("is_active", 1)], "name": "idx_user_companies_is_active"},
            {"key": [("user_id", 1), ("is_active", 1)], "name": "idx_user_companies_user_active"},
            {"key": [("company_branch_id", 1), ("is_active", 1)], "name": "idx_user_companies_branch_active"},
        ]

    class Config:
        arbitrary_types_allowed = True