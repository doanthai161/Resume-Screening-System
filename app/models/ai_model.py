from beanie import Document
from pydantic import Field
from pymongo import IndexModel
from bson import ObjectId
from datetime import datetime
from app.utils.time import now_vn
from typing import Optional, Dict, Any

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
    created_by: ObjectId = Field(..., description="ID of the user who created the AI model")
    created_at: datetime = Field(default_factory=lambda: now_vn())
    updated_at: datetime = Field(default_factory=lambda: now_vn())
    
    class Settings:
        name = "ai_models"
        indexes = [
            {"key": [("name", 1)], "name": "idx_ai_models_name"},
            {"key": [("model_type", 1)], "name": "idx_ai_models_type"},
            {"key": [("is_active", 1)], "name": "idx_ai_models_active"},
            {"key": [("provider", 1), ("model_id", 1)], "name": "idx_ai_models_provider"},
            {"key": [("model_type", 1), ("is_active", 1)], "name": "idx_ai_type_active"},
            {"key": [("last_used", -1)], "name": "idx_ai_last_used_desc", "sparse": True},
        ]
    
    class Config:
        arbitrary_types_allowed = True