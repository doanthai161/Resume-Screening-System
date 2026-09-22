from beanie import Document
from pydantic import Field
from bson import ObjectId
from datetime import datetime
from app.utils.time import now_utc
from typing import Optional, Dict, Any
from pymongo import ASCENDING, DESCENDING, IndexModel

class AIModel(Document):
    name: str = Field(..., description="Name of the model")
    model_type: str = Field(..., description="resume_parser, skill_matcher, scoring")
    provider: str = Field(..., description="openai, gemini, custom, huggingface")
    model_id: str = Field(..., description="ID on provider (gpt-4, etc)")
    version: str = Field("1.0", description="Version of the model")
    config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = Field(True, description="Is the model active?")
    total_predictions: int = Field(0, description="Total predictions")
    avg_processing_time: float = Field(0.0)
    last_used: Optional[datetime] = Field(None)
    description: Optional[str] = Field(None)
    created_by: Optional[ObjectId] = Field(None, description="ID of creator; null for system-provided models")
    created_at: datetime = Field(default_factory=lambda: now_utc())
    updated_at: datetime = Field(default_factory=lambda: now_utc())
    
    class Settings:
        name = "ai_models"
        indexes = [
            IndexModel(
                [("provider", ASCENDING), ("model_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="uq_ai_model_provider_id_version",
            ),
            IndexModel([("name", ASCENDING)]),
            IndexModel([("model_type", ASCENDING), ("is_active", ASCENDING)]),
            IndexModel([("last_used", DESCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]
    
    class Config:
        arbitrary_types_allowed = True
