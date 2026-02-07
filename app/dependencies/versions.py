from fastapi import APIRouter
from app.api import (
    users,
    actors,
    permissions,
    actor_permission,
    companies,
    company_branches,
    user_actor,
    user_company,
    register
)


api_router = APIRouter()

api_router.include_router(register.router, prefix="/register", tags=["Register"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(actors.router, prefix="/actors", tags=["Actors"])
api_router.include_router(permissions.router, prefix="/permissions", tags=["Permissions"])
api_router.include_router(actor_permission.router, prefix="/actor-permissions", tags=["Actor Permissions"])
api_router.include_router(user_actor.router, prefix="/user-actor", tags=["User Actor"])
api_router.include_router(companies.router, prefix="/companies", tags=["Companies"])
api_router.include_router(company_branches.router, prefix="/company-branches", tags=["Company branches"])
api_router.include_router(user_company.router, prefix="/user-company-branch", tags=["User Company Branch"])