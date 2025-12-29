from pydantic import Field
from beanie import Document
from datetime import datetime
from app.utils.time import now_vn

class ActorPermission(Document):
    actor_id: str = Field(..., description="ID of the actor")
    permission_id: str = Field(..., description="ID of the permission")
    created_at: datetime = Field(default_factory=lambda: now_vn())

    class Settings:
        name = "actor_permissions"