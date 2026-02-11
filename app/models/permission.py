from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_utc
from typing import Optional


class Permission(Document):
    name: str = Field(..., description="Name of the permission")
    description: Optional[str] = Field(None, description="Description of the permission")
    is_active: bool = Field(default=True, description="Indicates if the permission is active")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "permissions"
        indexes = [
            [("name", 1)],
            [("is_active", 1)],
            [("created_at", -1)],
        ]
    class Config:
        arbitrary_types_allowed = True