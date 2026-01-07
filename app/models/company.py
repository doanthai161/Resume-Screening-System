from pydantic import Field 
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn
from bson import ObjectId
from typing import Optional
from pymongo import IndexModel


class Company(Document):
    user_id: ObjectId = Field(..., description="ID of the user who owns the company")
    name: str = Field(..., description="Name of the company")
    company_short_name: str = Field(..., max_length=50, description="Short name or abbreviation of the company")
    description: Optional[str] = Field(None, max_length=250, description="Description of the company")
    company_code: str = Field(..., max_length=50, description="Unique code for the company")
    tax_code:Optional[str] = Field(None, max_length=50, description="Tax identification code")
    email: str = Field(..., description="Company contact email")
    logo_url: Optional[str] = Field(None, description="URL to the company logo image")
    website: str = Field(..., description="Company website URL")
    is_active: bool = Field(default=True, description="Is the company active?")
    updated_by: Optional[ObjectId] = Field(None, description="ID of the user who last updated the company")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "companies"
        indexes = [
            IndexModel([("user_id", 1)], name="idx_companies_user_id"),
            IndexModel([("company_code", 1)], name="idx_companies_company_code", unique=True),
            IndexModel([("email", 1)], name="idx_companies_email"),
        ]

    class Config:
        arbitrary_types_allowed = True
