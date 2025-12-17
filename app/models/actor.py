from beanie import Document
from pydantic import Field
from pymongo import IndexModel
from typing import Optional
from datetime import datetime, timezone

class Actor(Document):
    name: str = Field(..., max_length=100, description="Actor's full name")
    description: Optional[str] = Field(None, description="Description of the actor")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "actors"
        indexes = [
            IndexModel([("name", 1)], name="idx_actors_name"),
            IndexModel([("created_at", -1)], name="idx_actors_created_at_desc"),
        ]