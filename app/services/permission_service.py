from typing import List, Tuple
from fastapi import status
from app.repositories.permission_repository import PermissionRepository
from app.schemas.permission import PermissionCreate, PermissionUpdate
from app.models.permission import Permission
from app.core.errors import CustomError, ErrorCodes

class PermissionService:
    @staticmethod
    async def create_permission(data: PermissionCreate) -> Permission:
        try:
            existing_permission = await PermissionRepository.get_by_name(data.name)
            if existing_permission:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Permission already exists",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            return await PermissionRepository.create(data)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to create permission",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def list_permissions(page: int, size: int) -> Tuple[List[Permission], int]:
        try:
            skip = (page - 1) * size
            return await PermissionRepository.list_permissions(skip, size)
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to list permissions",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def update_permission(permission_id: str, data: PermissionUpdate) -> Permission:
        try:
            permission = await PermissionRepository.get_by_id(permission_id)
            if not permission or not permission.is_active:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Permission not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            return await PermissionRepository.update(permission, data)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to update permission",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def get_permission(permission_id: str) -> Permission:
        try:
            permission = await PermissionRepository.get_by_id(permission_id)
            if not permission or not permission.is_active:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Permission not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            return permission
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to get permission",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def delete_permission(permission_id: str) -> None:
        try:
            permission = await PermissionRepository.get_by_id(permission_id)
            if not permission or not permission.is_active:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Permission not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            await PermissionRepository.soft_delete(permission)
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to delete permission",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
