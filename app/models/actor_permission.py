from pydantic import Field
from beanie import Document
from datetime import datetime, timezone

class ActorPermission(Document):
    actor_id: str = Field(..., description="ID of the actor")
    permission_id: str = Field(..., description="ID of the permission")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "actor_permissions"