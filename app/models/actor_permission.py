from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_utc
from beanie import PydanticObjectId
class ActorPermission(Document):
    actor_id: PydanticObjectId = Field(..., description="ID of the actor")
    permission_id: PydanticObjectId = Field(..., description="ID of the permission")
    created_at: datetime = Field(default_factory=lambda: now_utc())

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
            