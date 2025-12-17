from beanie import Document
from pydantic import Field
from pymongo import IndexModel
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId

class UserActor(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    actor_id: ObjectId = Field(..., description="ID of the actor")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "user_actors"
        indexes = [
            IndexModel([("user_id", 1)], name="idx_user_actors_user_id"),
            IndexModel([("actor_id", 1)], name="idx_user_actors_actor_id"),
        ]

    class Config:
        arbitrary_types_allowed = True