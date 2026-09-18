from fastapi import Request, BackgroundTasks, APIRouter, Depends, status
from app.schemas.actor import ActorCreate, ActorResponse, ActorUpdate
from app.schemas.response import ApiResponse
from app.core.rate_limiter import limiter
from app.logs.logging_config import logger
from app.core.security import CurrentUser, require_permission
from app.services.actor_service import ActorService
from app.core.errors import CustomError, ErrorCodes

router = APIRouter()

@router.post("/create-actor", response_model=ApiResponse[ActorResponse])
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
        actor = await ActorService.create_actor(data)
        background_tasks.add_task(
            logger.info,
            f"Actor created with ID: {actor.id}"
        )

        response_data = ActorResponse(
            id=str(actor.id),
            name=actor.name,
            description=actor.description,
        )
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error creating actor: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to create actor",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.get("/list-actors", response_model=ApiResponse[list[ActorResponse]])
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
        actors = await ActorService.list_actors()

        response_data = [
            ActorResponse(
                id=str(actor.id),
                name=actor.name,
                description=actor.description,
            ) for actor in actors
        ]
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error listing actors: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to list actors",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.put("/update-actor/{actor_id}", response_model=ApiResponse[ActorResponse])
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
        actor = await ActorService.update_actor(actor_id, data)
        background_tasks.add_task(
            logger.info,
            f"Actor updated with ID: {actor.id}"
        )

        response_data = ActorResponse(
            id=str(actor.id),
            name=actor.name,
            description=actor.description,
        )
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error updating actor {actor_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to update actor",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
@router.get("/get-actor/{actor_id}", response_model=ApiResponse[ActorResponse])
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
        actor = await ActorService.get_actor(actor_id)

        response_data = ActorResponse(
            id=str(actor.id),
            name=actor.name,
            description=actor.description,
        )
        return ApiResponse.ok(response_data)
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting actor {actor_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get actor",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
        await ActorService.delete_actor(actor_id)
        background_tasks.add_task(
            logger.info,
            f"Actor deleted with ID: {actor_id}"
        )

        return ApiResponse.ok({"message": "Actor deleted successfully"})
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error deleting actor {actor_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to delete actor",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )