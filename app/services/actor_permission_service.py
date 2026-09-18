from typing import List, Dict, Any
from fastapi import status
from bson import ObjectId
from app.repositories.actor_permission_repository import ActorPermissionRepository
from app.models.actor_permission import ActorPermission
from app.schemas.actor import AssignPermissionResponse
from app.schemas.permission import PermissionResponse
from app.utils.time import now_utc
from app.core.errors import CustomError, ErrorCodes
from bson.errors import InvalidId

class ActorPermissionService:
    @staticmethod
    async def assign_permissions(actor_id: str, permission_ids: List[str], user_id: str) -> None:
        try:
            try:
                actor_oid = ObjectId(actor_id)
                [ObjectId(pid) for pid in permission_ids]
            except Exception:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Invalid actor_id or permission_ids",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            actor = await ActorPermissionRepository.get_actor(actor_id)
            if not actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            permissions = await ActorPermissionRepository.get_permissions(permission_ids)
            if len(permissions) != len(permission_ids):
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "One or more permissions not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            links = []
            for permission in permissions:
                exists = await ActorPermissionRepository.get_existing_link(actor.id, permission.id)
                if not exists:
                    links.append(
                        ActorPermission(
                            actor_id=actor.id,
                            permission_id=permission.id,
                            created_at=now_utc(),
                        )
                    )

            await ActorPermissionRepository.insert_links(links)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to assign permissions",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def unassign_permissions(actor_id: str, permission_ids: List[str]) -> int:
        if not permission_ids:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                "permission_ids cannot be empty",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            try:
                actor_oid = ObjectId(actor_id)
            except Exception:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Invalid actor_id",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            actor = await ActorPermissionRepository.get_actor(actor_id)
            if not actor:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            await ActorPermissionRepository.delete_links(actor_oid, permission_ids)
            return len(permission_ids)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to unassign permissions",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def get_actor_permissions(actor_id: str) -> AssignPermissionResponse:
        try:
            try:
                ObjectId(actor_id)
            except InvalidId:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Invalid actor_id format",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            actor_doc = await ActorPermissionRepository.get_actor_with_permissions(actor_id)
            if not actor_doc:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Actor not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            return AssignPermissionResponse(
                id=str(actor_doc["_id"]),
                name=actor_doc["name"],
                description=actor_doc.get("description"),
                permissions=[
                    PermissionResponse(**perm)
                    for perm in actor_doc.get("permissions", [])
                ]
            )
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to get actor permissions",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
