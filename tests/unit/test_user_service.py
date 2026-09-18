import pytest
from unittest.mock import patch, AsyncMock
from fastapi import status
from app.services.user_service import UserService
from app.schemas.user import UserCreate, UserUpdate
from app.core.errors import CustomError, ErrorCodes

@pytest.mark.asyncio
async def test_get_user_success(mock_user, current_user):
    with patch("app.services.user_service.UserRepository.get_user", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_user
        
        user = await UserService.get_user(str(mock_user.id), current_user)
        assert user.id == mock_user.id
        mock_get.assert_called_once_with(str(mock_user.id))

@pytest.mark.asyncio
async def test_get_user_forbidden(mock_user, current_user):
    # Try to access a different user's profile as a regular user
    with pytest.raises(CustomError) as exc_info:
        await UserService.get_user("some_other_id", current_user)
    
    assert exc_info.value.code == ErrorCodes.FORBIDDEN
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

@pytest.mark.asyncio
async def test_get_user_not_found(mock_admin_user, current_admin):
    # Access as admin, but user doesn't exist
    with patch("app.services.user_service.UserRepository.get_user", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        
        with pytest.raises(CustomError) as exc_info:
            await UserService.get_user("nonexistent_id", current_admin)
            
        assert exc_info.value.code == ErrorCodes.NOT_FOUND

@pytest.mark.asyncio
async def test_update_user_success(mock_user, current_user):
    update_data = UserUpdate(full_name="New Name")
    
    with patch("app.services.user_service.UserRepository.get_user", new_callable=AsyncMock) as mock_get:
        with patch("app.services.user_service.UserRepository.update_user", new_callable=AsyncMock) as mock_update:
            mock_get.return_value = mock_user
            updated_user_mock = mock_user.copy()
            updated_user_mock.full_name = "New Name"
            mock_update.return_value = updated_user_mock
            
            user = await UserService.update_user(str(mock_user.id), update_data, current_user)
            
            assert user.full_name == "New Name"
            mock_update.assert_called_once_with(str(mock_user.id), update_data)

@pytest.mark.asyncio
async def test_update_user_forbidden_field(mock_user, current_user):
    # Try to update a privileged field as a regular user
    update_data = UserUpdate(is_superuser=True)
    
    with patch("app.services.user_service.UserRepository.get_user", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_user
        
        with pytest.raises(CustomError) as exc_info:
            await UserService.update_user(str(mock_user.id), update_data, current_user)
            
        assert exc_info.value.code == ErrorCodes.FORBIDDEN
        assert "Cannot update is_superuser field" in exc_info.value.message
