from typing import List, Optional, Union, Dict, Any
from pathlib import Path
from pydantic import Field, field_validator, ConfigDict, SecretStr, computed_field
from pydantic_settings import BaseSettings
import os
import json
import secrets


class Settings(BaseSettings):
    APP_NAME: str = "Resume Screening System"
    APP_VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    WORKERS: int = Field(default=1, description="Number of worker processes")
    RELOAD: bool = Field(default=True, description="Enable auto-reload in development")
    
    PROJECT_ROOT: Path = Field(default=Path(__file__).parent.parent.parent, description="Project root directory")
    
    MONGODB_URI: str = Field(default="mongodb://localhost:27017", description="MongoDB connection URI")
    MONGODB_DB_NAME: str = Field(default="resume_screening", description="MongoDB database name")
    MONGODB_SERVER_SELECTION_TIMEOUT: int = Field(default=5000, description="MongoDB server selection timeout in ms")
    MONGODB_MAX_POOL_SIZE: int = Field(default=10, description="MongoDB maximum connection pool size")
    MONGODB_MIN_POOL_SIZE: int = Field(default=1, description="MongoDB minimum connection pool size")
    
    @computed_field
    @property
    def MONGODB_URL(self) -> str:
        uri = self.MONGODB_URI
        db_name = self.MONGODB_DB_NAME
        if uri.endswith(f"/{db_name}") or f"/{db_name}?" in uri:
            return uri
        
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        
        if parsed.path and parsed.path != '/':
            return uri
        else:
            if '?' in uri:
                base, query = uri.split('?', 1)
                return f"{base}/{db_name}?{query}"
            else:
                return f"{uri}/{db_name}"

    PASSWORD_MIN_LENGTH: int = Field(default=6, description="pw minimum size")
    PASSWORD_MAX_LENGTH: int = Field(default=40, description="pw maximum size")
    OTP_EXPIRY_MINUTES: int= Field(default=30, description="OTP expiry")
    API_KEY_HEADER:str
    
    REDIS_URL: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    REDIS_MAX_CONNECTIONS: int = Field(default=10, description="Maximum Redis connections in pool")
    REDIS_SOCKET_TIMEOUT: int = Field(default=5, description="Redis socket timeout in seconds")
    REDIS_SOCKET_CONNECT_TIMEOUT: int = Field(default=5, description="Redis connection timeout in seconds")
    REDIS_CACHE_TTL: int = Field(default=3600, description="Default Redis cache TTL in seconds (1 hour)")
    
    SECRET_KEY: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(32)),
        min_length=32,
        description="Secret key for JWT token signing"
    )
    ALGORITHM: str = Field(default="HS256", description="JWT algorithm")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, description="Access token expiry in minutes")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, description="Refresh token expiry in days")
    
    BREVO_API_KEY: Optional[SecretStr] = Field(default=None, description="Brevo (Sendinblue) API key")
    BREVO_SENDER_EMAIL: Optional[str] = Field(default=None, description="Default sender email")
    BREVO_SENDER_NAME: str = Field(default="Resume Screening System", description="Default sender name")
    
    UPLOAD_BASE_DIR: Path = Field(default=Path("uploads"), description="Base directory for uploads")
    MAX_UPLOAD_SIZE: int = Field(default=10 * 1024 * 1024, description="Maximum upload size in bytes (10MB)")
    MAX_RESUME_SIZE: int = Field(default=5 * 1024 * 1024, description="Maximum resume size in bytes (5MB)")
    MAX_IMAGE_SIZE: int = Field(default=2 * 1024 * 1024, description="Maximum image size in bytes (2MB)")
    
    ALLOWED_RESUME_EXTENSIONS: str = Field(default="pdf,docx,doc", description="Allowed resume extensions")
    ALLOWED_IMAGE_EXTENSIONS: str = Field(default="jpg,jpeg,png,gif", description="Allowed image extensions")
    ALLOWED_DOCUMENT_EXTENSIONS: str = Field(default="pdf,docx,doc,txt,rtf", description="Allowed document extensions")
    
    UPLOAD_CHUNK_SIZE: int = Field(default=1024 * 1024, description="Upload chunk size in bytes (1MB)")
    MAX_FILES_PER_UPLOAD: int = Field(default=10, description="Maximum files per upload")
    TEMP_FILE_EXPIRY_HOURS: int = Field(default=24, description="Temporary file expiry in hours")
    
    STORAGE_TYPE: str = Field(default="local", description="Storage type: local, s3, azure")
    AWS_ACCESS_KEY_ID: Optional[str] = Field(default=None, description="AWS access key ID for S3")
    AWS_SECRET_ACCESS_KEY: Optional[SecretStr] = Field(default=None, description="AWS secret access key")
    AWS_S3_BUCKET: Optional[str] = Field(default=None, description="S3 bucket name")
    AWS_S3_REGION: Optional[str] = Field(default=None, description="S3 region")
    AZURE_STORAGE_CONNECTION_STRING: Optional[SecretStr] = Field(default=None, description="Azure storage connection string")
    AZURE_CONTAINER_NAME: Optional[str] = Field(default=None, description="Azure container name")
    
    OPENAI_API_KEY: Optional[SecretStr] = Field(default=None, description="OpenAI API key")
    OPENAI_MODEL: str = Field(default="gpt-4-turbo-preview", description="OpenAI model name")
    
    GEMINI_API_KEY: Optional[SecretStr] = Field(default=None, description="Google Gemini API key")
    GEMINI_MODEL: str = Field(default="gemini-pro", description="Gemini model name")
    
    AZURE_OPENAI_ENDPOINT: Optional[str] = Field(default=None, description="Azure OpenAI endpoint")
    AZURE_OPENAI_API_KEY: Optional[SecretStr] = Field(default=None, description="Azure OpenAI API key")
    AZURE_OPENAI_DEPLOYMENT: str = Field(default="gpt-4", description="Azure OpenAI deployment name")
    AZURE_OPENAI_API_VERSION: str = Field(default="2023-12-01-preview", description="Azure OpenAI API version")
    
    HUGGINGFACE_API_KEY: Optional[SecretStr] = Field(default=None, description="HuggingFace API key")
    HUGGINGFACE_MODEL: str = Field(default="microsoft/resume-screening", description="HuggingFace model name")
    
    RATE_LIMIT_ENABLED: bool = Field(default=True, description="Enable rate limiting")
    RATE_LIMIT_DEFAULT: str = Field(default="100/15minutes", description="Default rate limit")
    RATE_LIMIT_UPLOAD: str = Field(default="10/hour", description="Upload rate limit")
    RATE_LIMIT_AUTH: str = Field(default="5/minute", description="Authentication rate limit")
    RATE_LIMIT_SCREENING: str = Field(default="20/hour", description="Resume screening rate limit")
    
    CORS_ORIGINS: str = Field(default="http://localhost:3000,http://localhost:8080", description="CORS allowed origins")
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True, description="Allow CORS credentials")
    CORS_ALLOW_METHODS: str = Field(default="GET,POST,PUT,DELETE,OPTIONS,PATCH", description="Allowed HTTP methods")
    CORS_ALLOW_HEADERS: str = Field(default="*", description="Allowed HTTP headers")
    CORS_EXPOSE_HEADERS: str = Field(default="", description="Exposed HTTP headers")
    CORS_MAX_AGE: int = Field(default=600, description="CORS max age in seconds")
    
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    LOG_FORMAT: str = Field(default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="Log format")
    LOG_FILE: Path = Field(default=Path("logs/app.log"), description="Log file path")
    
    ADMIN_ROLE_NAME: str = Field(default="Administrator", description="Admin role name")
    RECRUITER_ROLE_NAME: str = Field(default="Recruiter", description="Recruiter role name")
    CANDIDATE_ROLE_NAME: str = Field(default="Candidate", description="Candidate role name")
    DEFAULT_ROLE_NAME: str = Field(default="User", description="Default role name")
    
    FIRST_SUPERUSER_EMAIL: str = Field(default="admin@example.com", description="First superuser email")
    FIRST_SUPERUSER_PASSWORD: str = Field(default="changethis", description="First superuser password")
    FIRST_SUPERUSER_FULL_NAME: str = Field(default="Admin User", description="First superuser full name")
    CREATE_FIRST_SUPERUSER: bool = Field(default=True, description="Create first superuser on startup")
    @field_validator("CORS_ORIGINS", "CORS_ALLOW_METHODS", "CORS_ALLOW_HEADERS", "CORS_EXPOSE_HEADERS", mode="before")
    @classmethod
    def parse_comma_separated(cls, v: Any) -> str:
        if v is None:
            return ""
        
        if isinstance(v, list):
            return ",".join([str(item) for item in v])
        
        if isinstance(v, str):
            v = v.strip()
            if v.startswith('[') and v.endswith(']'):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return ",".join([str(item) for item in parsed])
                except json.JSONDecodeError:
                    pass
            return v
        
        return str(v)
    
    @field_validator(
        "ALLOWED_RESUME_EXTENSIONS",
        "ALLOWED_IMAGE_EXTENSIONS", 
        "ALLOWED_DOCUMENT_EXTENSIONS",
        mode="before"
    )
    @classmethod
    def parse_extensions(cls, v: Any) -> str:
        if v is None:
            return ""
        
        if isinstance(v, list):
            return ",".join([str(item) for item in v])
        
        if isinstance(v, str):
            v = v.strip()
            if v.startswith('[') and v.endswith(']'):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return ",".join([str(item) for item in parsed])
                except json.JSONDecodeError:
                    pass
            return v
        
        return str(v)
    
    @field_validator("UPLOAD_BASE_DIR", "LOG_FILE", mode="after")
    @classmethod
    def create_directories(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v
    
    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = ["development", "staging", "production", "test"]
        if v.lower() not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}")
        return v.lower()
    
    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
    
    @property
    def allowed_resume_extensions_list(self) -> List[str]:
        return [ext.strip() for ext in self.ALLOWED_RESUME_EXTENSIONS.split(",") if ext.strip()]
    
    @property
    def allowed_image_extensions_list(self) -> List[str]:
        return [ext.strip() for ext in self.ALLOWED_IMAGE_EXTENSIONS.split(",") if ext.strip()]
    
    @property
    def allowed_document_extensions_list(self) -> List[str]:
        return [ext.strip() for ext in self.ALLOWED_DOCUMENT_EXTENSIONS.split(",") if ext.strip()]
    
    @property
    def upload_path(self) -> Path:
        return self.UPLOAD_BASE_DIR
    
    @property
    def resume_upload_path(self) -> Path:
        return self.UPLOAD_BASE_DIR / "resumes"
    
    @property
    def temp_upload_path(self) -> Path:
        return self.UPLOAD_BASE_DIR / "temp"
    
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"
    
    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"
    
    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "test"
    
    @property
    def huggingface_available(self) -> bool:
        return bool(self.HUGGINGFACE_API_KEY)
    
    @property
    def openai_available(self) -> bool:
        return bool(self.OPENAI_API_KEY)
    
    @property
    def azure_openai_available(self) -> bool:
        return bool(self.AZURE_OPENAI_API_KEY and self.AZURE_OPENAI_ENDPOINT)
    
    @property
    def gemini_available(self) -> bool:
        return bool(self.GEMINI_API_KEY)
    
    @property
    def email_enabled(self) -> bool:
        return bool(self.BREVO_API_KEY and self.BREVO_SENDER_EMAIL)
    
    @property
    def allowed_resume_mime_types(self) -> List[str]:
        mime_map = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "doc": "application/msword",
        }
        return [mime_map[ext] for ext in self.allowed_resume_extensions_list if ext in mime_map]
    
    @property
    def allowed_image_mime_types(self) -> List[str]:
        mime_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
        }
        return [mime_map[ext] for ext in self.allowed_image_extensions_list if ext in mime_map]
    
    
    def get_storage_config(self) -> Dict[str, Any]:
        if self.STORAGE_TYPE == "s3":
            config = {
                "type": "s3",
                "provider": "s3",  # Thêm provider
                "access_key_id": self.AWS_ACCESS_KEY_ID,
                "secret_access_key": self.AWS_SECRET_ACCESS_KEY.get_secret_value() if self.AWS_SECRET_ACCESS_KEY else None,
                "bucket": self.AWS_S3_BUCKET,
                "region": self.AWS_S3_REGION,
            }
        elif self.STORAGE_TYPE == "azure":
            config = {
                "type": "azure",
                "provider": "azure",  # Thêm provider
                "connection_string": self.AZURE_STORAGE_CONNECTION_STRING.get_secret_value() if self.AZURE_STORAGE_CONNECTION_STRING else None,
                "container": self.AZURE_CONTAINER_NAME,
            }
        else:
            config = {
                "type": "local",
                "provider": "local",  # Thêm provider
                "base_path": str(self.upload_path.absolute()),
            }
        
        if "provider" not in config:
            config["provider"] = config.get("type", "local")
        
        return config
    
    def get_ai_provider_config(self) -> Dict[str, Any]:
        if self.openai_available:
            return {
                "provider": "openai",
                "api_key": self.OPENAI_API_KEY.get_secret_value() if self.OPENAI_API_KEY else None,
                "model": self.OPENAI_MODEL,
            }
        elif self.azure_openai_available:
            return {
                "provider": "azure",
                "api_key": self.AZURE_OPENAI_API_KEY.get_secret_value() if self.AZURE_OPENAI_API_KEY else None,
                "endpoint": self.AZURE_OPENAI_ENDPOINT,
                "deployment": self.AZURE_OPENAI_DEPLOYMENT,
                "api_version": self.AZURE_OPENAI_API_VERSION,
            }
        elif self.gemini_available:
            return {
                "provider": "gemini",
                "api_key": self.GEMINI_API_KEY.get_secret_value() if self.GEMINI_API_KEY else None,
                "model": self.GEMINI_MODEL,
            }
        elif self.huggingface_available:
            return {
                "provider": "huggingface",
                "api_key": self.HUGGINGFACE_API_KEY.get_secret_value() if self.HUGGINGFACE_API_KEY else None,
                "model": self.HUGGINGFACE_MODEL,
            }
        else:
            return {"provider": "none"}
    
    def get_upload_config(self) -> Dict[str, Any]:
        return {
            "max_sizes": {
                "resume": self.MAX_RESUME_SIZE,
                "image": self.MAX_IMAGE_SIZE,
                "default": self.MAX_UPLOAD_SIZE,
            },
            "allowed_extensions": {
                "resume": self.allowed_resume_extensions_list,
                "image": self.allowed_image_extensions_list,
                "document": self.allowed_document_extensions_list,
            },
            "paths": {
                "base": str(self.upload_path.absolute()),
                "resumes": str(self.resume_upload_path.absolute()),
                "temp": str(self.temp_upload_path.absolute()),
            }
        }
    
    def get_rate_limit_config(self) -> Dict[str, str]:
        return {
            "default": self.RATE_LIMIT_DEFAULT,
            "upload": self.RATE_LIMIT_UPLOAD,
            "auth": self.RATE_LIMIT_AUTH,
            "screening": self.RATE_LIMIT_SCREENING,
        }
    
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
        validate_default=False, 
    )
    # class Config:
    #     env_file=".env",
    #     env_file_encoding="utf-8",
    #     case_sensitive=False,
    #     extra="allow"

settings = Settings()