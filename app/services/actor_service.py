from typing import List
from fastapi import status
from app.repositories.actor_repository import ActorRepository
from app.schemas.actor import ActorCreate, ActorUpdate
from app.models.actor import Actor
from app.core.errors import CustomError, ErrorCodes
from app.core import cache

class ActorService:
    @staticmethod
    async def create_actor(data: ActorCreate) -> Actor:
        try:
            existing_actor = await ActorRepository.get_by_name(data.name)
            if existing_actor:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Actor already exists",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            return await ActorRepository.create(data)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to create actor",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def list_actors() -> List[Actor]:
        try:
            return await ActorRepository.get_all()
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to list actors",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def update_actor(actor_id: str, data: ActorUpdate) -> Actor:
        try:
            actor = await ActorRepository.get_by_id(actor_id)
            if not actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            actor = await ActorRepository.update(actor, data)
            await cache.invalidate_actor_authorization(actor_id)
            return actor
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to update actor",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def get_actor(actor_id: str) -> Actor:
        try:
            actor = await ActorRepository.get_by_id(actor_id)
            if not actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            return actor
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to get actor",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def delete_actor(actor_id: str) -> None:
        try:
            actor = await ActorRepository.get_by_id(actor_id)
            if not actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            await ActorRepository.soft_delete(actor)
            await cache.invalidate_actor_authorization(actor_id)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to delete actor",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
