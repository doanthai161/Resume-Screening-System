from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends
from app.utils.time import now_vn
from app.models.actor import Actor
from app.models.permission import Permission
from app.models.actor_permission import ActorPermission
from app.schemas.actor import ActorDetailResponse
from app.schemas.permission import PermissionResponse
from app.core.rate_limiter import limiter
from bson.errors import InvalidId
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.logs.logging_config import logger
from app.api.permissions import (
    CurrentUser,
    require_permission,
)

router = APIRouter()
app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@router.post("/actor-permission", response_model=dict)
@limiter.limit("5/minute")
async def assign_permission_to_actor(
    request: Request,
    actor_id: str,
    permission_ids: list[str],
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("permissions:edit")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Assigning permissions to actor ID: {actor_id}"
        )
        actor = await Actor.find_one(Actor.id == ObjectId(actor_id), Actor.is_active == True)
        if not actor:
            raise HTTPException(status_code=404, detail="Actor not found")

        for perm_id in permission_ids:
            permission = await Permission.find_one(Permission.id == ObjectId(perm_id), Permission.is_active == True)
            if not permission:
                raise HTTPException(status_code=404, detail=f"Permission ID {perm_id} not found")

            existing_link = await ActorPermission.find_one({
                "actor_id": actor.id,
                "permission_id": permission.id
            })
            if not existing_link:
                link = ActorPermission(
                    actor_id=actor.id,
                    permission_id=permission.id
                )
                await link.insert()

        background_tasks.add_task(
            logger.info,
            f"Permissions assigned to actor ID: {actor_id}"
        )

        return {"message": "Permissions assigned successfully"}
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while assigning permissions to actor"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )
    
@router.post("/unasign_permission", response_model=dict)
@limiter.limit("5/minute")
async def unassign_permission_from_actor(
    request: Request,
    actor_id: str,
    permission_ids: list[str],
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("permissions:edit")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Unassigning permissions from actor ID: {actor_id}"
        )
        actor = await Actor.find_one(Actor.id == ObjectId(actor_id), Actor.is_active == True)
        if not actor:
            raise HTTPException(status_code=404, detail="Actor not found")

        for perm_id in permission_ids:
            permission = await Permission.find_one(Permission.id == ObjectId(perm_id), Permission.is_active == True)
            if not permission:
                raise HTTPException(status_code=404, detail=f"Permission ID {perm_id} not found")

            existing_link = await ActorPermission.find_one({
                "actor_id": actor.id,
                "permission_id": permission.id
            })
            if existing_link:
                await existing_link.delete()

        background_tasks.add_task(
            logger.info,
            f"Permissions unassigned from actor ID: {actor_id}"
        )

        return {"message": "Permissions unassigned successfully"}
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while unassigning permissions from actor"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )
    
@router.get("/actor-permissions/{actor_id}",response_model=ActorDetailResponse)
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
        try:
            actor_object_id = ObjectId(actor_id)
        except InvalidId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid actor_id format"
            )

        background_tasks.add_task(
            logger.info,
            f"Fetching permissions for actor ID: {actor_id}"
        )

        pipeline = [
            {
                "$match": {
                    "_id": actor_object_id,
                    "is_active": True
                }
            },
            {
                "$lookup": {
                    "from": "actor_permissions",
                    "localField": "_id",
                    "foreignField": "actor_id",
                    "as": "actor_permissions"
                }
            },
            {
                "$lookup": {
                    "from": "permissions",
                    "let": {
                        "permission_ids": "$actor_permissions.permission_id"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$in": ["$_id", "$$permission_ids"]},
                                        {"$eq": ["$is_active", True]}
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "permissions"
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "name": 1,
                    "description": 1,
                    "permissions": {
                        "$map": {
                            "input": "$permissions",
                            "as": "perm",
                            "in": {
                                "id": {"$toString": "$$perm._id"},
                                "name": "$$perm.name",
                                "description": "$$perm.description",
                                "is_active": "$$perm.is_active"
                            }
                        }
                    }
                }
            }
        ]

        result = (
            await Actor.aggregate(pipeline).to_list(length=1)
        )

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Actor not found"
            )

        actor_doc = result[0]

        background_tasks.add_task(
            logger.info,
            f"Fetched permissions for actor ID: {actor_id}"
        )

        return ActorDetailResponse(
            id=str(actor_doc["_id"]),
            name=actor_doc["name"],
            description=actor_doc.get("description"),
            permissions=[
                PermissionResponse(**perm)
                for perm in actor_doc.get("permissions", [])
            ]
        )

    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while fetching actor permissions"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )
