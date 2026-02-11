from beanie import Document
from pydantic import Field
from typing import Optional
from datetime import datetime
from app.utils.time import now_utc
from bson import ObjectId

class UserActor(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    actor_id: ObjectId = Field(..., description="ID of the actor")
    created_by: ObjectId = Field(..., description="ID of the user created")
    updated_by: Optional[ObjectId] = Field(None, description="User Update user_actor")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "user_actors"
        indexes = [
            [("user_id", 1)],
            [("actor_id", 1)],
            [("created_at", -1)],
            [("user_id", 1), ("actor_id", 1)],
        ]

    class Config:
        arbitrary_types_allowed = True