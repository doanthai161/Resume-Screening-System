from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn
from bson import ObjectId
from pymongo import IndexModel

class ActorPermission(Document):
    actor_id: ObjectId = Field(..., description="ID of the actor")
    permission_id: ObjectId = Field(..., description="ID of the permission")
    created_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "actor_permissions"
        indexes = [
            IndexModel([("actor_id",1), ("permission_id", 1)], name = "idx_actor_permission")
        ]

    class Config:
        arbitrary_types_allowed = True
            