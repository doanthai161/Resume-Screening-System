from beanie import Document
from pydantic import Field
from typing import Optional
from datetime import datetime
from app.utils.time import now_utc

class Actor(Document):
    name: str = Field(..., max_length=100, description="Actor's full name")
    description: Optional[str] = Field(None, description="Description of the actor")
    is_active: bool = Field(default=True, description="Indicates if the actor is active")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "actors"
        indexes = [
            [("name", 1)],
            [("is_active", 1)],
            [("created_at", -1)],
        ]