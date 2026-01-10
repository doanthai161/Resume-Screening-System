from beanie import Document
from pydantic import Field
from pymongo import IndexModel
from typing import Optional
from datetime import datetime
from app.utils.time import now_vn
from bson import ObjectId

class UserActor(Document):
    user_id: ObjectId = Field(..., description="ID of the user")
    actor_id: ObjectId = Field(..., description="ID of the actor")
    created_by: ObjectId = Field(..., description="ID of the user created")
    updated_by: Optional[ObjectId] = Field(None, description="User Update user_actor")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "user_actors"
        indexes = [
            IndexModel([("user_id", 1)], name="idx_user_actors_user_id"),
            IndexModel([("actor_id", 1)], name="idx_user_actors_actor_id"),
        ]

    class Config:
        arbitrary_types_allowed = True