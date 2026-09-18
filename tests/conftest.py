import pytest
from httpx import AsyncClient
from app.main import app
from app.models.user import User
from bson import ObjectId
import datetime
from app.core.security import CurrentUser
from unittest.mock import patch
from mongomock_motor import AsyncMongoMockClient

@pytest.fixture
def mock_user():
    return User(
        id=ObjectId("5f8d04b3d92c7b1234567890"),
        email="test@example.com",
        username="testuser",
        full_name="Test User",
        hashed_password="hashed_password",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        created_at=datetime.datetime.utcnow(),
        updated_at=datetime.datetime.utcnow()
    )

@pytest.fixture
def mock_admin_user():
    return User(
        id=ObjectId("5f8d04b3d92c7b1234567891"),
        email="admin@example.com",
        username="adminuser",
        full_name="Admin User",
        hashed_password="hashed_password",
        is_active=True,
        is_verified=True,
        is_superuser=True,
        created_at=datetime.datetime.utcnow(),
        updated_at=datetime.datetime.utcnow()
    )

@pytest.fixture
def current_user(mock_user):
    return CurrentUser(
        user_id=mock_user.id,
        email=mock_user.email,
        is_superuser=mock_user.is_superuser,
        scopes=[],
        user=mock_user
    )

@pytest.fixture
def current_admin(mock_admin_user):
    return CurrentUser(
        user_id=mock_admin_user.id,
        email=mock_admin_user.email,
        is_superuser=mock_admin_user.is_superuser,
        scopes=[],
        user=mock_admin_user
    )

@pytest.fixture(autouse=True)
async def mock_db():
    mock_client = AsyncMongoMockClient()
    with patch("motor.motor_asyncio.AsyncIOMotorClient", return_value=mock_client):
        # Initialize Beanie with mock DB
        from beanie import init_beanie
        from app.core.database import DOCUMENT_MODELS
        from app.core.config import settings
        
        db = mock_client[settings.MONGODB_DB_NAME]
        await init_beanie(database=db, document_models=DOCUMENT_MODELS)
        yield mock_client

@pytest.fixture
async def async_client(mock_db):
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
