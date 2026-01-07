from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends, Query
from app.utils.time import now_vn
from datetime import datetime, timedelta, timezone
from app.models.permission import Permission
from app.schemas.permission import PermissionCreate, PermissionResponse, PermissionUpdate, PermissionListResponse
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.logs.logging_config import logger
from app.core.security import (
    CurrentUser,
    require_permission,
)

router = APIRouter()
app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@router.post("/create-permission", response_model=PermissionResponse)
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
        existing_permission = await Permission.find_one({'name': data.name, 'is_active': True})
        if existing_permission:
            raise HTTPException(status_code=400, detail="Permission already exists")

        permission = Permission(
            name=data.name,
            description=data.description,
            is_active=True,
        )
        await permission.insert()
        background_tasks.add_task(
            logger.info,
            f"Permission created with ID: {permission.id}"
        )

        return PermissionResponse(
            id=str(permission.id),
            name=permission.name,
            description=permission.description,
            is_active=permission.is_active,
        )
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while creating permission"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )
    
@router.get("/permissions", response_model=PermissionListResponse)
@limiter.limit("10/minute")
async def list_permissions(
    request: Request,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: CurrentUser = Depends(
        require_permission("permissions:view")
    ),
):
    skip = (page - 1) * size

    permissions = await Permission.find(
        {"is_active": True}
    ).skip(skip).limit(size).to_list()

    total = await Permission.find(
        {"is_active": True}
    ).count()

    return PermissionListResponse(
        total=total,
        page=page,
        size=size,
        permissions=[
            PermissionResponse.model_validate(p)
            for p in permissions
        ]
    )

    
@router.put("/update-permission/{permission_id}", response_model=PermissionResponse)
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
        permission = await Permission.get(ObjectId(permission_id))
        if not permission or not permission.is_active:
            raise HTTPException(status_code=404, detail="Permission not found")

        if data.name is not None:
            permission.name = data.name
        if data.description is not None:
            permission.description = data.description

        permission.updated_at = now_vn()
        await permission.save()
        background_tasks.add_task(
            logger.info,
            f"Permission updated with ID: {permission.id}"
        )

        return PermissionResponse(
            id=str(permission.id),
            name=permission.name,
            description=permission.description,
            is_active=permission.is_active,
        )
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while updating permission"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )
    
@router.get("/get-permission/{permission_id}", response_model=PermissionResponse)
@limiter.limit("10/minute")
async def get_permission(
    request: Request,
    permission_id: str,
    current_user: CurrentUser = Depends(
        require_permission("permissions:view")
    ),
):
    try:
        permission = await Permission.get(ObjectId(permission_id))
        if not permission or not permission.is_active:
            raise HTTPException(status_code=404, detail="Permission not found")

        return PermissionResponse(
            id=str(permission.id),
            name=permission.name,
            description=permission.description,
            is_active=permission.is_active,
        )
    except RateLimitExceeded:
        logger.error("Rate limit exceeded while retrieving permission")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
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
        permission = await Permission.get(ObjectId(permission_id))
        if not permission or not permission.is_active:
            raise HTTPException(status_code=404, detail="Permission not found")

        permission.is_active = False
        permission.updated_at = now_vn()
        await permission.save()
        background_tasks.add_task(
            logger.info,
            f"Permission deleted with ID: {permission.id}"
        )

        return {"detail": "Permission deleted successfully"}
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while deleting permission"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )