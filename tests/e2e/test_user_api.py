import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from fastapi import status
from app.models.user import User

@pytest.mark.asyncio
async def test_get_user_api(async_client: AsyncClient, mock_user, current_user):
    # Mock authentication dependency
    from app.api.users import get_current_user, require_permission
    from app.main import app
    
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[require_permission("users:view")] = lambda: current_user

    with patch("app.api.users.UserService.get_user", new_callable=AsyncMock) as mock_get_user:
        mock_get_user.return_value = mock_user
        
        response = await async_client.get(f"/api/v1/users/{mock_user.id}")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        # Check ApiResponse format
        assert data["success"] is True
        assert data["data"]["id"] == str(mock_user.id)
        assert data["data"]["email"] == "test@example.com"
        
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_update_user_api(async_client: AsyncClient, mock_user, current_user):
    from app.api.users import get_current_user
    from app.main import app
    
    app.dependency_overrides[get_current_user] = lambda: current_user

    with patch("app.api.users.UserService.update_user", new_callable=AsyncMock) as mock_update_user:
        updated_mock = mock_user.copy()
        updated_mock.full_name = "New Updated Name"
        mock_update_user.return_value = updated_mock
        
        payload = {"full_name": "New Updated Name"}
        response = await async_client.put(f"/api/v1/users/{mock_user.id}", json=payload)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        assert data["success"] is True
        assert data["data"]["full_name"] == "New Updated Name"
        
    app.dependency_overrides.clear()
