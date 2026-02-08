from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn
from bson import ObjectId
class ActorPermission(Document):
    actor_id: ObjectId = Field(..., description="ID of the actor")
    permission_id: ObjectId = Field(..., description="ID of the permission")
    created_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "actor_permissions"
        indexes = [
            [("actor_id", 1)],
            [("permission_id", 1)],
            [("created_at", -1)],
            [("actor_id", 1), ("permission_id", 1)],
        ]

    class Config:
        arbitrary_types_allowed = True
            