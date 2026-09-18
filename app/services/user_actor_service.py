from typing import Optional
from fastapi import status
from bson import ObjectId
from app.repositories.user_actor_repository import UserActorRepository
from app.models.user_actor import UserActor
from app.schemas.user import UserActorResponse
from app.schemas.actor import ActorResponse
from app.core.errors import CustomError, ErrorCodes

class UserActorService:
    @staticmethod
    async def assign_actor(user_id: str, actor_id: str, updater_id: str) -> UserActorResponse:
        try:
            try:
                ObjectId(user_id)
                ObjectId(actor_id)
            except Exception:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Invalid user_id or actor_id",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            user = await UserActorRepository.get_user(user_id)
            if not user:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            actor = await UserActorRepository.get_actor(actor_id)
            if not actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            user_actor = await UserActorRepository.get_user_actor_by_user(user_id)

            try:
                if user_actor:
                    user_actor.updated_by = ObjectId(updater_id)
                    user_actor.actor_id = ObjectId(actor_id)
                    await UserActorRepository.save_user_actor(user_actor)
                else:
                    user_actor = await UserActorRepository.create_user_actor(
                        user_id=user_id,
                        actor_id=actor_id,
                        created_by=updater_id
                    )
            except Exception as exc:
                if "E11000" in str(exc):
                    raise CustomError(
                        ErrorCodes.CONFLICT,
                        "Actor already assigned to user",
                        status_code=status.HTTP_409_CONFLICT
                    )
                raise

            return UserActorResponse(
                user_id=str(user.id),
                full_name=user.full_name,
                actor=ActorResponse(name=actor.name, description=actor.description)
            )
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to assign actor to user",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def get_user_actor(user_id: str) -> UserActorResponse:
        try:
            try:
                ObjectId(user_id)
            except Exception:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Invalid user_id format",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            user = await UserActorRepository.get_user(user_id)
            if not user:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            user_actor = await UserActorRepository.get_user_actor_by_user(user_id)
            if not user_actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User-actor mapping not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            actor = await UserActorRepository.get_actor(str(user_actor.actor_id))
            if not actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            return UserActorResponse(
                user_id=str(user_actor.user_id),
                full_name=user.full_name,
                actor=ActorResponse(name=actor.name, description=actor.description)
            )
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to get user actor",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def delete_user_actor(user_actor_id: str) -> None:
        try:
            try:
                ObjectId(user_actor_id)
            except Exception:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Invalid user_actor_id",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            user_actor = await UserActorRepository.get_user_actor_by_id(user_actor_id)
            if not user_actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User-Actor relationship not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            await UserActorRepository.delete_user_actor(user_actor)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to delete user actor",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
