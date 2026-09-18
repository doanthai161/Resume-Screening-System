from fastapi import Request, BackgroundTasks, APIRouter, Depends, status
from app.schemas.user import UserActorResponse
from app.schemas.response import ApiResponse
from app.core.rate_limiter import limiter
from app.logs.logging_config import logger
from app.api.permissions import CurrentUser, require_permission
from app.services.user_actor_service import UserActorService
from app.core.errors import CustomError, ErrorCodes

router = APIRouter()

@router.post("/user-actors", response_model=ApiResponse[UserActorResponse], status_code=201)
@limiter.limit("5/minute")
async def assign_actor_to_user(
    request: Request,
    user_id: str,
    actor_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("users:edit")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"User {current_user.user_id} assigning actor {actor_id} to user {user_id}"
        )

        response_data = await UserActorService.assign_actor(
            user_id=user_id,
            actor_id=actor_id,
            updater_id=current_user.user_id
        )

        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error assigning actor to user: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to assign actor to user",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.get("/user-actors/{user_id}", response_model=ApiResponse[UserActorResponse])
@limiter.limit("10/minute")
async def get_user_actor(
    request: Request,
    user_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("users:view")),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Fetching user-actor mapping for user_id={user_id}"
        )

        response_data = await UserActorService.get_user_actor(user_id)

        background_tasks.add_task(
            logger.info,
            f"Fetched user-actor mapping for user_id={user_id}"
        )

        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting user actor: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get user actor",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.delete("/user-actors/{user_actor_id}", response_model=ApiResponse[dict], status_code=200)
@limiter.limit("5/minute")
async def delete_user_actor(
    request: Request,
    user_actor_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("users:edit")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"User {current_user.user_id} deleting user_actor ID {user_actor_id}"
        )

        await UserActorService.delete_user_actor(user_actor_id)

        background_tasks.add_task(
            logger.info,
            f"UserActor {user_actor_id} deleted permanently"
        )

        return ApiResponse.ok({
            "message": "Actor removed from user successfully",
            "user_actor_id": user_actor_id
        })
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error deleting user actor: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to delete user actor",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
