from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Query, Request
from pydantic import EmailStr
from app.schemas.response import ApiResponse
from app.core.errors import CustomError, ErrorCodes
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
from app.core.config import settings
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.services.user_service import UserService
from app.repositories.user_repository import UserRepository

router = APIRouter()

@router.post("/", response_model=ApiResponse[UserResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_user(
    request: Request,
    user_data: UserCreate,
    current_user: CurrentUser = Depends(require_permission("users:create"))
):
    try:
        user = await UserService.create_user(user_data)
        data = UserResponse.model_validate(user.dict(exclude={"hashed_password"}))
        return ApiResponse.ok(data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error creating user: {e}", exc_info=True)
        raise CustomError(ErrorCodes.INTERNAL, "Failed to create user", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

@router.get("/", response_model=ApiResponse[List[UserResponse]])
@limiter.limit(settings.RATE_LIMIT_READ)
async def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    filters: Optional[UserFilter] = Depends(),
    sort_by: str = Query("created_at", regex="^(email|username|full_name|created_at|last_login)$"),
    sort_desc: bool = Query(True),
    current_user: CurrentUser = Depends(require_permission("users:view"))
):
    try:
        users, total = await UserService.list_users(
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
        
        return ApiResponse.ok(response_users)
        
    except Exception as e:
        logger.error(f"Error listing users: {e}", exc_info=True)
        raise CustomError(ErrorCodes.INTERNAL, "Failed to list users", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/search/", response_model=List[UserResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def search_users(
    request: Request,
    q: str = Query(..., min_length=2, max_length=100, description="Search term"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(require_permission("users:view")),
):
    try:
        users, _ = await UserRepository.search_users(q, skip, limit)
        return [
            UserResponse.model_validate(user.dict(exclude={"hashed_password"}))
            for user in users
        ]
    except Exception as e:
        logger.error(f"Error searching users: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search users",
        )


@router.get("/me", response_model=UserResponse)
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_current_user_profile(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        user = await UserRepository.get_user(str(current_user.user_id))
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        return UserResponse.model_validate(user.dict(exclude={"hashed_password"}))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting current user profile: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user profile",
        )

@router.get("/{user_id}", response_model=ApiResponse[UserResponse])
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_user(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("users:view"))
):
    try:
        user = await UserService.get_user(user_id, current_user)
        data = UserResponse.model_validate(user.dict(exclude={"hashed_password"}))
        return ApiResponse.ok(data)
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user"
        )

@router.put("/{user_id}", response_model=ApiResponse[UserResponse])
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def update_user(
    request: Request,
    user_id: str,
    update_data: UserUpdate,
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        updated_user = await UserService.update_user(user_id, update_data, current_user)
        data = UserResponse.model_validate(updated_user.dict(exclude={"hashed_password"}))
        return ApiResponse.ok(data)
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}", exc_info=True)
        raise CustomError(ErrorCodes.INTERNAL, "Failed to update user", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def delete_user(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("users:delete"))
):
    try:
        await UserService.delete_user(user_id, current_user)
        return ApiResponse.ok(message="User deleted successfully")
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
        raise CustomError(ErrorCodes.INTERNAL, "Failed to delete user", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

@router.delete("/hard/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def hard_delete_user(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("users:delete"))
):
    try:
        await UserService.hard_delete_user(user_id, current_user)
        return ApiResponse.ok(message="User hard deleted successfully")
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error hard deleting user {user_id}: {e}", exc_info=True)
        raise CustomError(ErrorCodes.INTERNAL, "Failed to hard delete user", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

@router.post("/bulk/update", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def bulk_update_users(
    request: Request,
    bulk_data: UserBulkUpdate,
    current_user: CurrentUser = Depends(require_permission("users:edit"))
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
@limiter.limit("5/minute")
async def bulk_deactivate_users(
    request: Request,
    bulk_data: UserBulkDeactivate,
    current_user: CurrentUser = Depends(require_permission("users:delete"))
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
@limiter.limit(settings.RATE_LIMIT_WRITE)
async def verify_user(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("users:edit"))
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify user"
        )

@router.post("/password/change", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
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
    reset_request: UserResetPasswordRequest,
    background_tasks: BackgroundTasks,
):
    try:
        token = await UserRepository.generate_password_reset_token(reset_request.email)
        if token:
            from app.core.email_otp import send_otp_email

            background_tasks.add_task(
                send_otp_email,
                str(reset_request.email),
                token,
                "password_reset",
            )
        return {
            "message": "If an account exists with this email, a reset link has been sent"
        }
        
    except Exception as e:
        logger.error(f"Error requesting password reset: {e}", exc_info=True)
        return {
            "message": "If an account exists with this email, a reset link has been sent"
        }

@router.post("/password/reset/confirm", status_code=status.HTTP_200_OK)
@limiter.limit("5/hour")
async def confirm_password_reset(
    request: Request,
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
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_user_statistics(
    request: Request,
    current_user: CurrentUser = Depends(require_permission("users:view"))
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
@limiter.limit(settings.RATE_LIMIT_READ)
async def get_user_activity_stats(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(require_permission("users:view"))
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
@limiter.limit("5/minute")
async def clear_user_cache(
    request: Request,
    current_user: CurrentUser = Depends(require_permission("users:edit"))
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
