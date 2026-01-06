from fastapi import APIRouter
from app.api import (
    users,
    actors,
    permissions,
    actor_permission,
)


api_router = APIRouter()

api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(actors.router, prefix="/actors", tags=["Actors"])
api_router.include_router(permissions.router, prefix="/permissions", tags=["Permissions"])
api_router.include_router(actor_permission.router, prefix="/actor-permissions", tags=["Actor Permissions"])