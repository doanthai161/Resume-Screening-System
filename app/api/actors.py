from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends
from app.utils.time import now_utc
from app.models.actor import Actor
from app.schemas.actor import ActorCreate, ActorResponse, ActorUpdate
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

@router.post("/create-actor", response_model=ActorResponse)
@limiter.limit("3/minute")
async def create_actor(
    request: Request,
    data: ActorCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("actors:create")
    ),
    
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Creating actor with name: {data.name}"
        )
        existing_actor = await Actor.find_one({'name': data.name, 'is_active': True})
        if existing_actor:
            raise HTTPException(status_code=400, detail="Actor already exists")

        actor = Actor(
            name=data.name,
            is_active=True,
            description=data.description,
        )
        await actor.insert()
        background_tasks.add_task(
            logger.info,
            f"Actor created with ID: {actor.id}"
        )

        return ActorResponse(
            id=str(actor.id),
            name=actor.name,
            description=actor.description,
        )
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while creating actor"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )


@router.get("/list-actors", response_model=list[ActorResponse])
@limiter.limit("10/minute")
async def list_actors(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("actors:view")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            "Listing all actors"
        )
        actors = await Actor.find({"is_active": True}).to_list()

        return [
            ActorResponse(
                id=str(actor.id),
                name=actor.name,
                description=actor.description,
            ) for actor in actors
        ]
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while listing actors"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )

@router.put("/update-actor/{actor_id}", response_model=ActorResponse)
@limiter.limit("5/minute")
async def update_actor(
    request: Request,
    actor_id: str,
    data: ActorUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("actors:edit")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Updating actor with ID: {actor_id}"
        )
        actor = await Actor.find_one({"_id": ObjectId(actor_id), "is_active": True})
        if not actor:
            raise HTTPException(status_code=404, detail="Actor not found")

        if data.name is not None:
            actor.name = data.name
        if data.description is not None:
            actor.description = data.description

        await actor.save()
        background_tasks.add_task(
            logger.info,
            f"Actor updated with ID: {actor.id}"
        )

        return ActorResponse(
            name=actor.name,
            description=actor.description,
        )
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while updating actor"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )
    
@router.get("/get-actor/{actor_id}", response_model=ActorResponse)
@limiter.limit("10/minute")
async def get_actor(
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
            f"Fetching actor with ID: {actor_id}"
        )
        actor = await Actor.find_one({"_id": ObjectId(actor_id), "is_active": True})
        if not actor:
            raise HTTPException(status_code=404, detail="Actor not found")

        return ActorResponse(
            id=str(actor.id),
            name=actor.name,
            description=actor.description,
        )
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while fetching actor"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )
    
@router.delete("/delete-actor/{actor_id}")
@limiter.limit("5/minute")
async def delete_actor(
    request: Request,
    actor_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("actors:delete")
    ),
):
    try:
        background_tasks.add_task(
            logger.info,
            f"Deleting actor with ID: {actor_id}"
        )
        actor = await Actor.find_one({"_id": ObjectId(actor_id), "is_active": True})
        if not actor:
            raise HTTPException(status_code=404, detail="Actor not found")

        actor.is_active = False
        await actor.save()
        background_tasks.add_task(
            logger.info,
            f"Actor deleted with ID: {actor_id}"
        )

        return {"message": "Actor deleted successfully"}
    except RateLimitExceeded:
        background_tasks.add_task(
            logger.error,
            "Rate limit exceeded while deleting actor"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )