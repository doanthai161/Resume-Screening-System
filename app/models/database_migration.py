from datetime import datetime

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.utils.time import now_utc


class DatabaseMigration(Document):
    version: int = Field(..., ge=1)
    name: str = Field(..., min_length=1, max_length=200)
    checksum: str = Field(..., min_length=16, max_length=128)
    applied_at: datetime = Field(default_factory=now_utc)

    class Settings:
        name = "database_migrations"
        indexes = [IndexModel([("version", ASCENDING)], unique=True, name="uq_migration_version")]
