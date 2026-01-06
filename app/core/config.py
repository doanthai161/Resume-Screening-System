from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    mongo_uri: str
    secret_key: str
    ADMIN_ROLE_NAME: str = "Administrator"
    DEFAULT_ROLE_NAME: str = "User"
    BREVO_API_KEY: str
    BREVO_SENDER_EMAIL: str
    BREVO_SENDER_NAME: str = "Resume Screening System"

    class Config:
        env_file = (".env",)
        extra = "allow"


settings = Settings()
