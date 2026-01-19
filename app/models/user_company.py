from bson import ObjectId
from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn
from pymongo import IndexModel
from typing import Optional

class UserCompany(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    company_branch_id: ObjectId = Field(..., description="ID of the company")
    created_by: Optional[ObjectId] = Field(None, description="ID of the user who last assign user to company")
    updated_by: Optional[ObjectId] = Field(None, description="ID of the user who last unssign user to the company")
    is_active: bool = Field(default=True, description="Is the user active in company?")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "user_companies"
        indexes = [
            IndexModel([("user_id", 1)], name="idx_user_companies_user_id"),
            IndexModel([("company_id", 1)], name="idx_user_companies_company_id"),
        ]

    class Config:
        arbitrary_types_allowed = True