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
    # This requires mocking the dependency in tests or relying on the actual DB if it's a test DB.
    # Typically in E2E we override the auth dependency to return our mock user.
    from app.core.security import require_permission
    from app.main import app
    
    # Override dependency for testing
    app.dependency_overrides[require_permission("actors:create")] = lambda: current_admin
    
    # Clean up DB before test if it's an integration test.
    # Since we don't have direct DB access here, we assume it's clean or we mock.
    # In a real scenario, tests would run against a dedicated test database.
    response = await async_client.post("/api/v1/actors/create-actor", json=test_actor)
    
    # We might get 429 if rate limiter is active in tests, or 401 if auth fails.
    # Let's assume the test setup handles this.
    assert response.status_code in [200, 201]
    data = response.json()
    assert "data" in data
    assert data["data"]["name"] == test_actor["name"]
    
    # Clear overrides
    app.dependency_overrides = {}
