from datetime import datetime

from beanie import Document, PydanticObjectId
from pymongo import IndexModel


class AuthSession(Document):
    sid: str
    user_id: PydanticObjectId
    auth_version: int
    refresh_digest: str
    expires_at: datetime
    revoked_at: datetime | None = None

    class Settings:
        name = "auth_sessions"
        indexes = [
            IndexModel("sid", unique=True),
            IndexModel("expires_at", expireAfterSeconds=0),
            IndexModel("user_id"),
        ]
