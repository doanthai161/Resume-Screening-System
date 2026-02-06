from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn
from pymongo import IndexModel
from typing import Optional


class Permission(Document):
    name: str = Field(..., description="Name of the permission")
    description: Optional[str] = Field(None, description="Description of the permission")
    is_active: bool = Field(default=True, description="Indicates if the permission is active")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "permissions"
        indexes = [
            {"key": [("name", 1)], "name": "idx_permissions_name", "unique": True},
            {"key": [("is_active", 1)], "name": "idx_permissions_active"},
        ]
    class Config:
        arbitrary_types_allowed = True