from beanie import Document
from pydantic import Field
from typing import Optional
from datetime import datetime
from app.utils.time import now_utc
from pymongo import ASCENDING, DESCENDING, IndexModel

class Actor(Document):
    name: str = Field(..., max_length=100, description="Actor's full name")
    description: Optional[str] = Field(None, description="Description of the actor")
    is_active: bool = Field(default=True, description="Indicates if the actor is active")
    is_default: bool = Field(default=False, description="Assigned automatically by the system")
    is_system: bool = Field(default=False, description="Protected system role")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "actors"
        indexes = [
            IndexModel([("name", ASCENDING)], unique=True, name="uq_actor_name"),
            IndexModel([("is_active", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]
