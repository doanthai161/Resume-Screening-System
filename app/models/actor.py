from beanie import Document
from pydantic import Field
from pymongo import IndexModel
from typing import Optional
from datetime import datetime
from app.utils.time import now_vn

class Actor(Document):
    name: str = Field(..., max_length=100, description="Actor's full name")
    description: Optional[str] = Field(None, description="Description of the actor")
    is_active: bool = Field(default=True, description="Indicates if the actor is active")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "actors"
        indexes = [
            IndexModel([("name", 1)], name="idx_actors_name"),
            IndexModel([("created_at", -1)], name="idx_actors_created_at_desc"),
        ]