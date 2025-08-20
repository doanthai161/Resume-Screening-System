import motor.motor_asyncio
from beanie import init_beanie
from app.repositories.config import settings

from app.models.job import Job
from app.models.user import User

async def init_db():
    client = motor.motor_asyncio.AsyncIOMotorClient(settings.mongo_uri)
    await init_beanie(
        database= client.get_default_database(),
        document_models=[
            Job,
            User,
            
        ],
    )