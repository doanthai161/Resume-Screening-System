import pytest
from unittest.mock import patch, AsyncMock
from fastapi import status
from bson import ObjectId
from app.services.actor_service import ActorService
from app.schemas.actor import ActorCreate, ActorUpdate
from app.core.errors import CustomError, ErrorCodes
from app.models.actor import Actor

@pytest.fixture
def mock_actor():
    return Actor(
        id=ObjectId("5f8d04b3d92c7b1234567890"),
        name="Test Actor",
        description="A test actor",
        is_active=True
    )

@pytest.mark.asyncio
async def test_get_actor_success(mock_actor):
    with patch("app.services.actor_service.ActorRepository.get_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_actor
        
        actor = await ActorService.get_actor(str(mock_actor.id))
        assert actor.id == mock_actor.id
        mock_get.assert_called_once_with(str(mock_actor.id))

@pytest.mark.asyncio
async def test_get_actor_not_found():
    with patch("app.services.actor_service.ActorRepository.get_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        
        with pytest.raises(CustomError) as exc_info:
            await ActorService.get_actor("nonexistent_id")
            
        assert exc_info.value.code == ErrorCodes.NOT_FOUND

@pytest.mark.asyncio
async def test_create_actor_success(mock_actor):
    create_data = ActorCreate(name="New Actor", description="Description")
    
    with patch("app.services.actor_service.ActorRepository.get_by_name", new_callable=AsyncMock) as mock_get_name:
        with patch("app.services.actor_service.ActorRepository.create", new_callable=AsyncMock) as mock_create:
            mock_get_name.return_value = None
            
            created_actor = Actor(id=ObjectId(), name="New Actor", description="Description", is_active=True)
            mock_create.return_value = created_actor
            
            actor = await ActorService.create_actor(create_data)
            
            assert actor.name == "New Actor"
            mock_create.assert_called_once_with(create_data)

@pytest.mark.asyncio
async def test_create_actor_already_exists(mock_actor):
    create_data = ActorCreate(name="Test Actor", description="Description")
    
    with patch("app.services.actor_service.ActorRepository.get_by_name", new_callable=AsyncMock) as mock_get_name:
        mock_get_name.return_value = mock_actor
        
        with pytest.raises(CustomError) as exc_info:
            await ActorService.create_actor(create_data)
            
        assert exc_info.value.code == ErrorCodes.BAD_REQUEST
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
