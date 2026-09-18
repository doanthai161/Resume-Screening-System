from fastapi import Request, BackgroundTasks, APIRouter, Depends, Query, status
from app.schemas.permission import PermissionCreate, PermissionResponse, PermissionUpdate, PermissionListResponse
from app.schemas.response import ApiResponse
from app.core.rate_limiter import limiter
from app.logs.logging_config import logger
from app.core.security import CurrentUser, require_permission
from app.services.permission_service import PermissionService
from app.core.errors import CustomError, ErrorCodes

router = APIRouter()

@router.post("/create-permission", response_model=ApiResponse[PermissionResponse])
@limiter.limit("5/minute")
async def create_permission(
    request: Request,
    data: PermissionCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("permissions:create")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Creating permission with name: {data.name}"
        )
        permission = await PermissionService.create_permission(data)
        background_tasks.add_task(
            logger.info,
            f"Permission created with ID: {permission.id}"
        )

        response_data = PermissionResponse(
            id=str(permission.id),
            name=permission.name,
            description=permission.description,
            is_active=permission.is_active,
        )
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error creating permission: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to create permission",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
@router.get("/permissions", response_model=ApiResponse[PermissionListResponse])
@limiter.limit("10/minute")
async def list_permissions(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: CurrentUser = Depends(
        require_permission("permissions:view")
    ),
):
    try:
        permissions, total = await PermissionService.list_permissions(page, size)
        
        response_data = PermissionListResponse(
            total=total,
            page=page,
            size=size,
            permissions=[
                PermissionResponse(
                    id=str(p.id),
                    name=p.name,
                    description=p.description,
                    is_active=p.is_active,
                )
                for p in permissions
            ]
        )
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error listing permissions: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to list permissions",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.put("/update-permission/{permission_id}", response_model=ApiResponse[PermissionResponse])
@limiter.limit("5/minute")
async def update_permission(
    request: Request,
    permission_id: str,
    data: PermissionUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("permissions:edit")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Updating permission with ID: {permission_id}"
        )
        permission = await PermissionService.update_permission(permission_id, data)
        background_tasks.add_task(
            logger.info,
            f"Permission updated with ID: {permission.id}"
        )

        response_data = PermissionResponse(
            id=str(permission.id),
            name=permission.name,
            description=permission.description,
            is_active=permission.is_active,
        )
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error updating permission {permission_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to update permission",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
@router.get("/get-permission/{permission_id}", response_model=ApiResponse[PermissionResponse])
@limiter.limit("10/minute")
async def get_permission(
    request: Request,
    permission_id: str,
    current_user: CurrentUser = Depends(
        require_permission("permissions:view")
    ),
):
    try:
        permission = await PermissionService.get_permission(permission_id)
        
        response_data = PermissionResponse(
            id=str(permission.id),
            name=permission.name,
            description=permission.description,
            is_active=permission.is_active,
        )
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting permission {permission_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get permission",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
@router.delete("/delete-permission/{permission_id}")
@limiter.limit("5/minute")
async def delete_permission(
    request: Request,
    permission_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("permissions:delete")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Deleting permission with ID: {permission_id}"
        )
        await PermissionService.delete_permission(permission_id)
        background_tasks.add_task(
            logger.info,
            f"Permission deleted with ID: {permission_id}"
        )

        return ApiResponse.ok({"detail": "Permission deleted successfully"})
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error deleting permission {permission_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to delete permission",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )