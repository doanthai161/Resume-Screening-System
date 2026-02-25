from pydantic import Field 
from beanie import Document
from datetime import datetime
from app.utils.time import now_utc
from bson import ObjectId
from typing import Optional, List

class Company(Document):
    user_id: ObjectId = Field(..., description="ID of the user who owns the company")
    name: str = Field(..., description="Name of the company")
    industry: Optional[str] = Field(None, max_length=100, description="industry")
    company_short_name: str = Field(..., max_length=50, description="Short name or abbreviation of the company")
    description: Optional[str] = Field(None, max_length=250, description="Description of the company")
    company_code: str = Field(..., max_length=50, description="Unique code for the company")
    tax_code:Optional[str] = Field(None, max_length=50, description="Tax identification code")
    email: str = Field(..., description="Company contact email")
    logo_url: Optional[str] = Field(None, description="URL to the company logo image")
    website: str = Field(..., description="Company website URL")
    branch_ids: List[ObjectId] = Field(default_factory=list, description="list branch id")
    is_active: bool = Field(default=True, description="Is the company active?")
    updated_by: Optional[ObjectId] = Field(None, description="ID of the user who last updated the company")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "companies"
        indexes = [
            [("user_id", 1)],
            [("name", 1)],
            [("company_code", 1)],
            [("email", 1)],
            [("is_active", 1)],
            [("created_at", -1)],
        ]

    class Config:
        arbitrary_types_allowed = True
