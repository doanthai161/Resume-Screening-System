from pydantic import Field
from beanie import Document
from datetime import datetime, timezone
from pymongo import IndexModel


class Permission(Document):
    name: str = Field(..., description="Name of the permission")
    description: str = Field(..., description="Description of the permission")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "permissions"
        indexes = [
            IndexModel([("name", 1)], name="idx_permissions_name", unique=True),
        ]
    class Config:
        arbitrary_types_allowed = True