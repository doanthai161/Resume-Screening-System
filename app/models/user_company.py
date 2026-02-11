from bson import ObjectId
from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_utc
from typing import Optional, List

class UserCompany(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    company_branch_id: ObjectId = Field(..., description="ID of the company branch")
    role: str = Field(default="member", description="Role of the user in the branch")
    permissions: List[str] = Field(default_factory=list, description="Specific permissions for this assignment")
    assigned_by: ObjectId = Field(..., description="ID of the user who made this assignment")
    assigned_at: datetime = Field(default_factory=lambda: now_utc(), description="When the user was assigned")
    unassigned_by: Optional[ObjectId] = Field(None, description="ID of the user who unassigned")
    unassigned_at: Optional[datetime] = Field(None, description="When the user was unassigned")
    unassign_reason: Optional[str] = Field(None, description="Reason for unassignment")
    start_date: Optional[datetime] = Field(None, description="When the assignment becomes active")
    end_date: Optional[datetime] = Field(None, description="When the assignment expires")
    is_active: bool = Field(default=True, description="Is the assignment currently active?")
    updated_by: Optional[ObjectId] = Field(None, description="ID of the user who last updated")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "user_companies"
        indexes = [
            [("user_id", 1)],
            [("company_branch_id", 1)],
            [("assigned_by", 1)],
            [("unassigned_by", 1)],
            [("start_date", 1)],
            [("end_date", 1)],
            [("is_active", 1)],
            [("created_at", -1)],
            [("user_id", 1), ("is_active", 1)],
            [("company_branch_id", 1), ("is_active", 1)],
        ]

    class Config:
        arbitrary_types_allowed = True