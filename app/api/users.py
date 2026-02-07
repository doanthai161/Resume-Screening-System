from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from pydantic import EmailStr
from app.schemas.user import (
    UserCreate,
    UserUpdate,
    UserResponse,
    UserFilter,
    UserBulkUpdate,
    UserBulkDeactivate,
    UserChangePassword,
    UserResetPasswordRequest,
    UserResetPasswordConfirm,
    UserStatisticsResponse,
    UserActivityStatsResponse
)
from app.models.user import User
from app.core.security import get_current_user, require_permission, CurrentUser
from app.logs.logging_config import logger
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.repositories.user_repository import UserRepository

router = APIRouter()

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_user(
    request: Request,
    user_data: UserCreate,
    current_user: CurrentUser = Depends(require_permission("user:create"))
):
    try:
        user = await UserRepository.create_user(user_data)
        return UserResponse.model_validate(user.dict(exclude={"hashed_password"}))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating user: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        )

@router.get("/", response_model=List[UserResponse])
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    filters: Optional[UserFilter] = Depends(),
    sort_by: str = Query("created_at", regex="^(email|username|full_name|created_at|last_login)$"),
    sort_desc: bool = Query(True),
    current_user: CurrentUser = Depends(require_permission("user:read"))
):
    try:
        users, total = await UserRepository.list_users(
            page=page,
            size=size,
            filters=filters,
            sort_by=sort_by,
            sort_desc=sort_desc
        )
        
        response_users = [
            UserResponse.model_validate(user.dict(exclude={"hashed_password"})) 
            for user in users
        ]
        
        return response_users
        
    except Exception as e:
        logger.error(f"Error listing users: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list users"
        )

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("user:read"))
):
    try:
        if user_id != str(current_user.user_id) and not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this user"
            )
        
        user = await UserRepository.get_user(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        return UserResponse.model_validate(user.dict(exclude={"hashed_password"}))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user"
        )

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    update_data: UserUpdate,
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        existing_user = await UserRepository.get_user(user_id)
        if not existing_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Authorization checks
        is_self = user_id == str(current_user.user_id)
        
        # Regular users can only update their own profile
        if not is_self and not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this user"
            )
        
        # Non-admin users cannot update privileged fields
        if not current_user.is_superuser:
            privileged_fields = ["is_active", "is_verified", "is_superuser", "role"]
            for field in privileged_fields:
                if getattr(update_data, field, None) is not None:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Cannot update {field} field"
                    )
        
        updated_user = await UserRepository.update_user(user_id, update_data)
        if not updated_user:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user"
            )
        
        return UserResponse.model_validate(updated_user.dict(exclude={"hashed_password"}))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user"
        )

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("user:delete"))
):
    try:
        if user_id == str(current_user.user_id) and current_user.is_superuser:
            superuser_count = await User.find({"is_superuser": True}).count()
            if superuser_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete the last superuser"
                )
        
        success = await UserRepository.delete_user(user_id, deleted_by=str(current_user.user_id))
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found or could not be deleted"
            )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete user"
        )

@router.delete("/hard/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_user(
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("user:hard_delete"))
):
    try:
        success = await UserRepository.hard_delete_user(user_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found or could not be hard deleted"
            )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error hard deleting user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to hard delete user"
        )

@router.get("/search/", response_model=List[UserResponse])
async def search_users(
    q: str = Query(..., min_length=2, description="Search term"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(require_permission("user:read"))
):
    """
    Search users by email, username, full_name, or phone
    """
    try:
        users, total = await UserRepository.search_users(q, skip, limit)
        
        response_users = [
            UserResponse.model_validate(user.dict(exclude={"hashed_password"})) 
            for user in users
        ]
        
        return response_users
        
    except Exception as e:
        logger.error(f"Error searching users: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search users"
        )

@router.post("/bulk/update", status_code=status.HTTP_200_OK)
async def bulk_update_users(
    bulk_data: UserBulkUpdate,
    current_user: CurrentUser = Depends(require_permission("user:bulk_update"))
):
    """
    Bulk update users (Admin only)
    """
    try:
        updated_count, total_count = await UserRepository.bulk_update_users(
            bulk_data.user_ids,
            bulk_data.update_data.model_dump(exclude_unset=True)
        )
        
        return {
            "message": f"Successfully updated {updated_count} out of {total_count} users",
            "updated_count": updated_count,
            "total_count": total_count
        }
        
    except Exception as e:
        logger.error(f"Error bulk updating users: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to bulk update users"
        )

@router.post("/bulk/deactivate", status_code=status.HTTP_200_OK)
async def bulk_deactivate_users(
    bulk_data: UserBulkDeactivate,
    current_user: CurrentUser = Depends(require_permission("user:delete"))
):
    try:
        deactivated_count = await UserRepository.bulk_deactivate_users(
            bulk_data.user_ids,
            deactivated_by=str(current_user.user_id)
        )
        
        return {
            "message": f"Successfully deactivated {deactivated_count} users",
            "deactivated_count": deactivated_count
        }
        
    except Exception as e:
        logger.error(f"Error bulk deactivating users: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to bulk deactivate users"
        )

@router.post("/verify/{user_id}", status_code=status.HTTP_200_OK)
async def verify_user(
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("user:verify"))
):
    """
    Verify a user (Admin only)
    """
    try:
        success = await UserRepository.verify_user(user_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        return {"message": "User verified successfully"}
        
    except Exception as e:
        logger.error(f"Error verifying user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify user"
        )

@router.post("/password/change", status_code=status.HTTP_200_OK)
async def change_password(
    password_data: UserChangePassword,
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Change current user's password
    """
    try:
        success = await UserRepository.change_password(
            user_id=str(current_user.user_id),
            current_password=password_data.current_password,
            new_password=password_data.new_password
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )
        
        return {"message": "Password changed successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing password: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password"
        )

@router.post("/password/reset/request", status_code=status.HTTP_200_OK)
@limiter.limit("5/hour")
async def request_password_reset(
    request: Request,
    reset_request: UserResetPasswordRequest
):
    try:
        token = await UserRepository.generate_password_reset_token(reset_request.email)
        
        return {
            "message": "If an account exists with this email, a reset link has been sent",
            "token": token
        }
        
    except Exception as e:
        logger.error(f"Error requesting password reset: {e}", exc_info=True)
        return {
            "message": "If an account exists with this email, a reset link has been sent"
        }

@router.post("/password/reset/confirm", status_code=status.HTTP_200_OK)
async def confirm_password_reset(
    reset_confirm: UserResetPasswordConfirm
):
    try:
        success = await UserRepository.reset_password(
            reset_confirm.token,
            reset_confirm.new_password
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token"
            )
        
        return {"message": "Password reset successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming password reset: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset password"
        )

@router.get("/stats/overall", response_model=UserStatisticsResponse)
async def get_user_statistics(
    current_user: CurrentUser = Depends(require_permission("user:stats"))
):
    try:
        stats = await UserRepository.get_user_statistics()
        return UserStatisticsResponse(**stats)
        
    except Exception as e:
        logger.error(f"Error getting user statistics: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user statistics"
        )

@router.get("/stats/activity/{user_id}", response_model=UserActivityStatsResponse)
async def get_user_activity_stats(
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("user:stats"))
):
    try:
        stats = await UserRepository.get_user_activity_statistics(user_id)
        if "error" in stats:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=stats["error"]
            )
        
        return UserActivityStatsResponse(**stats)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user activity stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user activity statistics"
        )

@router.post("/cache/clear", status_code=status.HTTP_200_OK)
async def clear_user_cache(
    current_user: CurrentUser = Depends(require_permission("user:cache_clear"))
):
    try:
        await UserRepository.clear_all_user_cache()
        return {"message": "User cache cleared successfully"}
        
    except Exception as e:
        logger.error(f"Error clearing user cache: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear user cache"
        )

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        user = await UserRepository.get_user(str(current_user.user_id))
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        return UserResponse.model_validate(user.dict(exclude={"hashed_password"}))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting current user profile: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user profile"
        )