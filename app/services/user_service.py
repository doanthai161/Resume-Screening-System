from typing import List, Tuple, Optional
from fastapi import status
from app.repositories.user_repository import UserRepository
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate, UserFilter
from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser

class UserService:
    @staticmethod
    async def create_user(user_data: UserCreate) -> User:
        try:
            return await UserRepository.create_user(user_data)
        except ValueError as e:
            raise CustomError(ErrorCodes.VALIDATION, str(e), status_code=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    async def get_user(user_id: str, current_user: CurrentUser) -> User:
        if user_id != str(current_user.user_id) and not current_user.is_admin:
            raise CustomError(
                ErrorCodes.FORBIDDEN, 
                "Not authorized to view this user", 
                status_code=status.HTTP_403_FORBIDDEN
            )
        
        user = await UserRepository.get_user(user_id)
        if not user:
            raise CustomError(
                ErrorCodes.NOT_FOUND, 
                "User not found", 
                status_code=status.HTTP_404_NOT_FOUND
            )
        return user

    @staticmethod
    async def list_users(
        page: int, 
        size: int, 
        filters: Optional[UserFilter], 
        sort_by: str, 
        sort_desc: bool
    ) -> Tuple[List[User], int]:
        return await UserRepository.list_users(
            page=page,
            size=size,
            filters=filters,
            sort_by=sort_by,
            sort_desc=sort_desc
        )

    @staticmethod
    async def update_user(user_id: str, update_data: UserUpdate, current_user: CurrentUser) -> User:
        existing_user = await UserRepository.get_user(user_id)
        if not existing_user:
            raise CustomError(ErrorCodes.NOT_FOUND, "User not found", status_code=status.HTTP_404_NOT_FOUND)
        
        # Authorization checks
        is_self = user_id == str(current_user.user_id)
        
        # Regular users can only update their own profile
        if not is_self and not current_user.is_admin:
            raise CustomError(
                ErrorCodes.FORBIDDEN, 
                "Not authorized to update this user", 
                status_code=status.HTTP_403_FORBIDDEN
            )
        
        # Non-admin users cannot update privileged fields
        if not current_user.is_admin:
            privileged_fields = ["is_active", "is_verified", "is_superuser", "role"]
            for field in privileged_fields:
                if getattr(update_data, field, None) is not None:
                    raise CustomError(
                        ErrorCodes.FORBIDDEN, 
                        f"Cannot update {field} field", 
                        status_code=status.HTTP_403_FORBIDDEN
                    )
        if update_data.role is not None:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                "Use actor assignment endpoints to change roles",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        
        updated_user = await UserRepository.update_user(user_id, update_data)
        if not updated_user:
            raise CustomError(
                ErrorCodes.INTERNAL, 
                "Failed to update user", 
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
            if target.is_superuser:
                superuser_count = await User.find({"is_superuser": True}).count()
                if superuser_count <= 1:
                    raise CustomError(
                        ErrorCodes.BAD_REQUEST, 
                        "Cannot delete the last superuser", 
                        status_code=status.HTTP_400_BAD_REQUEST
                    )
            
            success = await UserRepository.delete_user(user_id, deleted_by=str(current_user.user_id))
            if not success:
                raise CustomError(
                    ErrorCodes.NOT_FOUND, 
                    "User not found or could not be deleted", 
                    status_code=status.HTTP_404_NOT_FOUND
                )
        except ValueError as e:
            raise CustomError(ErrorCodes.BAD_REQUEST, str(e), status_code=status.HTTP_400_BAD_REQUEST)

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
            if target.is_superuser:
                superuser_count = await User.find({"is_superuser": True}).count()
                if superuser_count <= 1:
                    raise CustomError(
                        ErrorCodes.BAD_REQUEST,
                        "Cannot delete the last superuser",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
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
                    status_code=status.HTTP_404_NOT_FOUND
                )
        except ValueError as e:
            raise CustomError(ErrorCodes.BAD_REQUEST, str(e), status_code=status.HTTP_400_BAD_REQUEST)
