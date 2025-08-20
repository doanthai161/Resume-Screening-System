from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    secret_key: str

    class Config:
        env_file = (".env",)
        extra = "allow"


settings = Settings()
