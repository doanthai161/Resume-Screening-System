from bson import ObjectId
from pydantic import Field
from beanie import Document
from datetime import datetime, timezone
from pymongo import IndexModel

class UserCompany(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    company_id: ObjectId = Field(..., description="ID of the company")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "user_companies"
        indexes = [
            IndexModel([("user_id", 1)], name="idx_user_companies_user_id"),
            IndexModel([("company_id", 1)], name="idx_user_companies_company_id"),
        ]

    class Config:
        arbitrary_types_allowed = True