from fastapi import Request, BackgroundTasks, APIRouter, Depends, status
from app.schemas.actor import AssignPermissionRequest, AssignPermissionResponse
from app.schemas.response import ApiResponse
from app.core.rate_limiter import limiter
from app.logs.logging_config import logger
from app.core.security import CurrentUser, require_permission
from app.services.actor_permission_service import ActorPermissionService
from app.core.errors import CustomError, ErrorCodes

router = APIRouter()

@router.post("/actor-permission", response_model=ApiResponse[dict])
@limiter.limit("5/minute")
async def assign_permission_to_actor(
    request: Request,
    payload: AssignPermissionRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("permissions:edit")),
):
    try:
        await ActorPermissionService.assign_permissions(
            actor_id=payload.actor_id,
            permission_ids=payload.permission_ids,
            user_id=current_user.user_id
        )

        background_tasks.add_task(
            logger.info,
            f"Permissions assigned to actor ID: {payload.actor_id}"
        )

        return ApiResponse.ok({"message": "Permissions assigned successfully"})
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error assigning permissions: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to assign permissions",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.post("/unassign-permission", response_model=ApiResponse[dict])
@limiter.limit("5/minute")
async def unassign_permission_from_actor(
    request: Request,
    actor_id: str,
    permission_ids: list[str],
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("permissions:edit")),
):
    try:
        count = await ActorPermissionService.unassign_permissions(
            actor_id=actor_id,
            permission_ids=permission_ids
        )

        background_tasks.add_task(
            logger.info,
            f"Permissions unassigned from actor ID: {actor_id}"
        )

        return ApiResponse.ok({
            "message": "Permissions unassigned successfully",
            "count": count
        })
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error unassigning permissions: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to unassign permissions",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    
@router.get(
    "/actor-permissions/{actor_id}",
    response_model=ApiResponse[AssignPermissionResponse]
)
@limiter.limit("10/minute")
async def get_actor_permissions(
    request: Request,
    actor_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("actors:view")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Fetching permissions for actor ID: {actor_id}"
        )

        response_data = await ActorPermissionService.get_actor_permissions(actor_id)

        background_tasks.add_task(
            logger.info,
            f"Fetched permissions for actor ID: {actor_id}"
        )

        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting actor permissions: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get actor permissions",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
