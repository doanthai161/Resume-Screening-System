# app/core/simple_config.py
import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Settings:
    # Core
    APP_NAME = os.getenv("APP_NAME", "Resume Screening System")
    APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
    API_V1_STR = os.getenv("API_V1_STR", "/api/v1")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    
    # Security
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    
    # Database
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "resume_screening")
    
    # Redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # File Upload
    UPLOAD_BASE_DIR = Path(os.getenv("UPLOAD_BASE_DIR", "uploads"))
    MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", str(10 * 1024 * 1024)))  # 10MB
    
    # CORS
    @property
    def CORS_ORIGINS(self) -> List[str]:
        cors_str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
        return [origin.strip() for origin in cors_str.split(",") if origin.strip()]
    
    # Resume Extensions
    @property
    def ALLOWED_RESUME_EXTENSIONS(self) -> List[str]:
        ext_str = os.getenv("ALLOWED_RESUME_EXTENSIONS", "pdf,docx,doc")
        return [ext.strip() for ext in ext_str.split(",") if ext.strip()]
    
    # Path properties
    @property
    def upload_path(self) -> Path:
        return self.UPLOAD_BASE_DIR
    
    @property
    def resume_upload_path(self) -> Path:
        return self.UPLOAD_BASE_DIR / "resumes"
    
    @property
    def database_url(self) -> str:
        return f"{self.MONGO_URI}/{self.MONGODB_DB_NAME}"
    
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"
    
    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"
    
    def __init__(self):
        # Create directories
        self.UPLOAD_BASE_DIR.mkdir(parents=True, exist_ok=True)
        self.resume_upload_path.mkdir(exist_ok=True)


# Create singleton
settings = Settings()