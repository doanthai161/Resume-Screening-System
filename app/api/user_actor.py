from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends
from app.utils.time import now_utc
from app.models.user import User
from app.models.actor import Actor
from app.models.user_actor import UserActor
from app.schemas.user import UserActorResponse
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

@router.post("/user-actors", response_model=UserActorResponse, status_code=201)
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
        user_oid = ObjectId(user_id)
        actor_oid = ObjectId(actor_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id or actor_id")

    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} assigning actor {actor_id} to user {user_id}"
    )

    user = await User.find_one(
        {"_id": user_oid, "is_active": True}
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    actor = await Actor.find_one(
        {"_id": actor_oid, "is_active": True}
    )
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")

    user_actor = await UserActor.find_one(
        {"user_id": user_oid}
    )
    if not user_actor:
        raise HTTPException(status_code=404, detail="User not found")
 
    user_actor.updated_by = current_user.user_id
    user_actor.actor_id = actor_oid

    try:
        await user_actor.save()
    except Exception as exc:
        if "E11000" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Actor already assigned to user"
            )
        raise

    return UserActorResponse(
        user_id=str(user.id),
        full_name=user.full_name,
        actor_id=str(actor.id),
        actor_name=actor.name
    )

@router.get("/user-actors/{user_id}",response_model=UserActorResponse)
@limiter.limit("10/minute")
async def get_user_actor(
    request: Request,
    user_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("users:view")),
):
    background_tasks.add_task(
        logger.info,
        f"Fetching user-actor mapping for user_id={user_id}",
        f"user_id: {user_id} used api get user_actor"
    )

    try:
        user_oid = ObjectId(user_id)
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id format"
        )
    
    user = await User.find_one({"_id": user_oid, "is_active": True})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    user_actor = await UserActor.find_one(
        UserActor.user_id == user_oid,
    )

    if not user_actor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User-actor mapping not found"
        )

    background_tasks.add_task(
        logger.info,
        f"Fetched user-actor mapping for user_id={user_id}"
    )

    return UserActorResponse(
        id=str(user_actor.id),
        user_id=str(user_actor.user_id),
        full_name= user.full_name,
        actor_id=str(user_actor.actor_id),
        created_at=user_actor.created_at,
    )



@router.delete("/user-actors/{user_actor_id}", status_code=200)
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
        user_actor_oid = ObjectId(user_actor_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_actor_id")

    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} deleting user_actor ID {user_actor_id}"
    )

    user_actor = await UserActor.find_one({"_id": user_actor_oid})
    if not user_actor:
        raise HTTPException(
            status_code=404,
            detail="User-Actor relationship not found"
        )

    await user_actor.delete()

    background_tasks.add_task(
        logger.info,
        f"UserActor {user_actor_id} deleted permanently"
    )

    return {
        "message": "Actor removed from user successfully",
        "user_actor_id": user_actor_id
    }
