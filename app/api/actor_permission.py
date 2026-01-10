from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends
from app.utils.time import now_vn
from app.models.actor import Actor
from app.models.permission import Permission
from app.models.actor_permission import ActorPermission
from app.schemas.actor import AssignPermissionRequest, AssignPermissionResponse
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

@router.post("/actor-permission",response_model=dict)
@limiter.limit("5/minute")
async def assign_permission_to_actor(
    request: Request,
    payload: AssignPermissionRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("permissions:edit")),
):
    try:
        actor_oid = ObjectId(payload.actor_id)
        perm_oids = [ObjectId(pid) for pid in payload.permission_ids]
    except Exception:
        raise HTTPException(400, "Invalid actor_id or permission_ids")

    actor = await Actor.find_one({"_id": actor_oid, "is_active": True})
    if not actor:
        raise HTTPException(404, "Actor not found")

    permissions = await Permission.find(
        {"_id": {"$in": perm_oids}, "is_active": True}
    ).to_list()

    if len(permissions) != len(perm_oids):
        raise HTTPException(404, "One or more permissions not found")

    links = []
    for permission in permissions:
        exists = await ActorPermission.find_one({
            "actor_id": actor.id,
            "permission_id": permission.id
        })
        if not exists:
            links.append(
                ActorPermission(
                    actor_id=actor.id,
                    permission_id=permission.id,
                    created_at=now_vn(),
                    created_by=current_user.user_id,
                )
            )

    if links:
        await ActorPermission.insert_many(links)

    background_tasks.add_task(
        logger.info,
        f"Permissions assigned to actor ID: {payload.actor_id}"
    )

    return {"message": "Permissions assigned successfully"}


@router.post("/unassign-permission", response_model=dict)
@limiter.limit("5/minute")
async def unassign_permission_from_actor(
    request: Request,
    actor_id: str,
    permission_ids: list[str],
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("permissions:edit")),
):
    if not permission_ids:
        raise HTTPException(400, "permission_ids cannot be empty")
    background_tasks.add_task(
        logger.info,
        f"Unassigning {len(perm_oids)} permissions from actor {actor_id}"
    )

    try:
        actor_oid = ObjectId(actor_id)
        perm_oids = [ObjectId(pid) for pid in permission_ids]
    except Exception:
        raise HTTPException(400, "Invalid actor_id or permission_ids")

    actor = await Actor.find_one(
        Actor.id == actor_oid,
        Actor.is_active == True
    )
    if not actor:
        raise HTTPException(404, "Actor not found")

    background_tasks.add_task(
        logger.info,
        f"Unassigning permissions from actor ID: {actor_id}"
    )

    await ActorPermission.find({
        "actor_id": actor.id,
        "permission_id": {"$in": perm_oids}
    }).delete()

    background_tasks.add_task(
        logger.info,
        f"Permissions unassigned from actor ID: {actor_id}"
    )

    return {
        "message": "Permissions unassigned successfully",
        "count": len(perm_oids)
    }

    
@router.get(
    "/actor-permissions/{actor_id}",
    response_model=AssignPermissionResponse
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
    # 1️⃣ Validate ObjectId
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

    # 2️⃣ Aggregation pipeline
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

    try:
        collection = Actor.get_motor_collection()
    except AttributeError:
        collection = Actor.get_pymongo_collection()

    cursor = collection.aggregate(pipeline)
    result = await cursor.to_list(length=1)

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

    # 5️⃣ Map sang response model
    return AssignPermissionResponse(
        id=str(actor_doc["_id"]),
        name=actor_doc["name"],
        description=actor_doc.get("description"),
        permissions=[
            PermissionResponse(**perm)
            for perm in actor_doc.get("permissions", [])
        ]
    )
