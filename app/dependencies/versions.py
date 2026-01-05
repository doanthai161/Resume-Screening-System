from fastapi import APIRouter
from app.api import (
    users,
    actors,
)


api_router = APIRouter()

api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(actors.router, prefix="/actors", tags=["Actors"])