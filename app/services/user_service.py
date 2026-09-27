from typing import List, Tuple, Optional
from fastapi import status
from app.repositories.user_repository import UserRepository
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate, UserFilter
from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser


class UserService:
    @staticmethod
    def _authorize_update(
        user_id: str, target: User, data: UserUpdate, caller: CurrentUser
    ) -> None:
        fields = data.model_fields_set
        if not caller.is_admin and user_id != caller.user_id:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Not authorized to update this user",
                status.HTTP_403_FORBIDDEN,
            )
        if target.is_superuser and not caller.is_superuser:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Only superusers can update a superuser",
                status.HTTP_403_FORBIDDEN,
            )
        for field in fields & {"is_active", "is_verified", "is_superuser", "role"}:
            if not caller.is_admin or (
                field == "is_superuser" and not caller.is_superuser
            ):
                raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    f"Cannot update {field} field",
                    status.HTTP_403_FORBIDDEN,
                )
            if field == "role":
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Use actor assignment endpoints to change roles",
                    status.HTTP_400_BAD_REQUEST,
                )
            if getattr(data, field) is None:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    f"{field} cannot be null",
                    status.HTTP_400_BAD_REQUEST,
                )
        # Generic profile/bulk updates must not remove the last active superuser.
        # A dedicated ownership-transfer operation is required for demotion.
        if target.is_superuser and (
            data.is_superuser is False or data.is_active is False
        ):
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Cannot demote or deactivate a superuser through user updates",
                status.HTTP_403_FORBIDDEN,
            )

    @staticmethod
    async def bulk_update_users(
        user_ids: List[str], data: UserUpdate, caller: CurrentUser
    ) -> Tuple[int, int]:
        if not caller.is_admin or not caller.has_permission("users:edit"):
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Only administrators can bulk update users",
                status.HTTP_403_FORBIDDEN,
            )
        # Validate every target before any write, including the caller's own row.
        for user_id in user_ids:
            target = await UserRepository.get_user(user_id)
            if not target:
                raise CustomError(
                    ErrorCodes.NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND
                )
            UserService._authorize_update(user_id, target, data, caller)
        return await UserRepository.bulk_update_users(
            user_ids,
            data.model_dump(exclude_unset=True),
            allow_superuser=caller.is_superuser,
        )

    @staticmethod
    def _authorize_removal(target: User, caller: CurrentUser) -> None:
        if not caller.has_permission("users:delete") or target.is_superuser:
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Cannot remove or deactivate this user", 403
            )
        if str(target.id) == caller.user_id:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Cannot deactivate yourself through administrative endpoints",
                403,
            )

    @staticmethod
    async def bulk_deactivate_users(user_ids: List[str], caller: CurrentUser) -> int:
        for user_id in user_ids:
            target = await UserRepository.get_user(user_id)
            if not target:
                raise CustomError(ErrorCodes.NOT_FOUND, "User not found", 404)
            UserService._authorize_removal(target, caller)
        return await UserRepository.bulk_deactivate_users(user_ids, caller.user_id)

    @staticmethod
    async def create_user(user_data: UserCreate) -> User:
        try:
            return await UserRepository.create_user(user_data)
        except ValueError as e:
            raise CustomError(
                ErrorCodes.VALIDATION, str(e), status_code=status.HTTP_400_BAD_REQUEST
            )

    @staticmethod
    async def get_user(user_id: str, current_user: CurrentUser) -> User:
        if user_id != str(current_user.user_id) and not current_user.is_admin:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Not authorized to view this user",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        user = await UserRepository.get_user(user_id)
        if not user:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "User not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return user

    @staticmethod
    async def list_users(
        page: int,
        size: int,
        filters: Optional[UserFilter],
        sort_by: str,
        sort_desc: bool,
    ) -> Tuple[List[User], int]:
        return await UserRepository.list_users(
            page=page, size=size, filters=filters, sort_by=sort_by, sort_desc=sort_desc
        )

    @staticmethod
    async def update_user(
        user_id: str, update_data: UserUpdate, current_user: CurrentUser
    ) -> User:
        existing_user = await UserRepository.get_user(user_id)
        if not existing_user:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "User not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        UserService._authorize_update(user_id, existing_user, update_data, current_user)

        updated_user = await UserRepository.update_user(
            user_id, update_data, allow_superuser=current_user.is_superuser
        )
        if not updated_user:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to update user",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return updated_user

    @staticmethod
    async def delete_user(user_id: str, current_user: CurrentUser) -> None:
        try:
            target = await UserRepository.get_user(user_id)
            if not target:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User not found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            UserService._authorize_removal(target, current_user)
            success = await UserRepository.delete_user(
                user_id, deleted_by=str(current_user.user_id)
            )
            if not success:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User not found or could not be deleted",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
        except ValueError as e:
            raise CustomError(
                ErrorCodes.BAD_REQUEST, str(e), status_code=status.HTTP_400_BAD_REQUEST
            )

    @staticmethod
    async def hard_delete_user(user_id: str, current_user: CurrentUser) -> None:
        try:
            target = await UserRepository.get_user(user_id)
            if not target:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User not found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            UserService._authorize_removal(target, current_user)
            if user_id == current_user.user_id:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Use account deactivation instead of hard-deleting yourself",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            success = await UserRepository.hard_delete_user(user_id)
            if not success:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "User not found or could not be hard deleted",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
        except ValueError as e:
            raise CustomError(
                ErrorCodes.BAD_REQUEST, str(e), status_code=status.HTTP_400_BAD_REQUEST
            )
