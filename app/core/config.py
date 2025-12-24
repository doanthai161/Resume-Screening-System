from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    mongo_uri: str
    secret_key: str
    ADMIN_ROLE_NAME: str = "Quản trị hệ thống"
    DEFAULT_ROLE_NAME: str = "Cán bộ, công chức"
    BREVO_API_KEY: str
    BREVO_SENDER_EMAIL: str
    BREVO_SENDER_NAME: str = "Trợ Lý Ảo"

    class Config:
        env_file = (".env",)
        extra = "allow"


settings = Settings()
