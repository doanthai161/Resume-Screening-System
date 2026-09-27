import pytest
from httpx import AsyncClient
from fastapi import status
from app.models.actor import Actor
from bson import ObjectId

@pytest.fixture
def test_actor():
    return {
        "name": "Test Actor API",
        "description": "API Test Actor Description"
    }

@pytest.mark.asyncio
async def test_create_actor(async_client: AsyncClient, test_actor, mock_admin_user, current_admin):
    from app.core.security import require_permission
    from app.main import app

    # Authentication is overridden here; the service still verifies persisted state.
    await mock_admin_user.insert()
    dependency = require_permission("actors:create")
    app.dependency_overrides[dependency] = lambda: current_admin
    try:
        response = await async_client.post("/api/v1/actors/create-actor", json=test_actor)
        assert response.status_code in [200, 201]
        assert response.json()["data"]["name"] == test_actor["name"]
    finally:
        app.dependency_overrides.pop(dependency, None)
