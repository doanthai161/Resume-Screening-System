from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_utc
from beanie import PydanticObjectId
from pymongo import ASCENDING, DESCENDING, IndexModel
class ActorPermission(Document):
    actor_id: PydanticObjectId = Field(..., description="ID of the actor")
    permission_id: PydanticObjectId = Field(..., description="ID of the permission")
    created_at: datetime = Field(default_factory=lambda: now_utc())

    class Settings:
        name = "actor_permissions"
        indexes = [
            IndexModel(
                [("actor_id", ASCENDING), ("permission_id", ASCENDING)],
                unique=True,
                name="uq_actor_permission",
            ),
            IndexModel([("permission_id", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]

    class Config:
        arbitrary_types_allowed = True
