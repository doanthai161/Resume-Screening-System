from pydantic import Field
from beanie import Document
from datetime import datetime, timezone


class Permission(Document):
    name: str = Field(..., description="Name of the permission")
    description: str = Field(..., description="Description of the permission")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))