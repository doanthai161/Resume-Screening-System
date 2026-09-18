from typing import List
from fastapi import APIRouter, Depends, Request, BackgroundTasks, status
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging
import time

from app.schemas.user_company import (
    AssignUserToCompanyBranch,
    UserCompanyResponse,
    UserCompanyListResponse,
    UserCompanyStats
)
from app.schemas.response import ApiResponse
from app.core.security import get_current_user
from app.models.user import User
from app.core.monitoring import monitor_endpoint, record_response_time
from app.middleware.audit_log import audit_log_action
from app.services.user_company_service import UserCompanyService
from app.core.errors import CustomError, ErrorCodes

router = APIRouter()
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/assign",
    status_code=status.HTTP_201_CREATED,
    summary="Assign user to company branch",
    description="Assign a user to a specific company branch",
    response_model=ApiResponse[dict]
)
@limiter.limit("3/minute")
@monitor_endpoint("assign_user_to_company_branch")
@audit_log_action("user_company.assigned")
async def assign_user_to_company_branch(
    request: Request,
    data: AssignUserToCompanyBranch,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.assign_user(data, str(current_user.id))
        
        background_tasks.add_task(
            logger.info,
            f"User {data.user_id} assigned to branch {data.company_branch_id} by {current_user.id}"
        )
        
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error assigning user to branch: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to assign user to branch",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("assign_user_to_company_branch", time.time() - start_time)


@router.post(
    "/unassign",
    status_code=status.HTTP_200_OK,
    summary="Unassign user from company branch",
    description="Unassign a user from a specific company branch (soft delete)",
    response_model=ApiResponse[dict]
)
@limiter.limit("3/minute")
@monitor_endpoint("unassign_user_from_company_branch")
@audit_log_action("user_company.unassigned")
async def unassign_user_from_company_branch(
    request: Request,
    data: AssignUserToCompanyBranch,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.unassign_user(data, str(current_user.id))
        
        background_tasks.add_task(
            logger.info,
            f"User {data.user_id} unassigned from branch {data.company_branch_id} by {current_user.id}"
        )
        
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error unassigning user from branch: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to unassign user from branch",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("unassign_user_from_company_branch", time.time() - start_time)


@router.delete(
    "/{assignment_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete user-company assignment",
    description="Permanently delete a user-company branch assignment (hard delete)",
    response_model=ApiResponse[dict]
)
@limiter.limit("2/minute")
@monitor_endpoint("delete_user_company_assignment")
@audit_log_action("user_company.deleted")
async def delete_user_company_assignment(
    request: Request,
    assignment_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.delete_assignment(assignment_id, current_user)
        
        background_tasks.add_task(
            logger.warning,
            f"HARD DELETE user_company assignment: {assignment_id} by {current_user.id}"
        )
        
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error deleting assignment {assignment_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to delete assignment",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("delete_user_company_assignment", time.time() - start_time)


@router.get(
    "/assignments/{assignment_id}",
    response_model=ApiResponse[UserCompanyResponse],
    summary="Get assignment details",
    description="Get details of a specific user-company branch assignment"
)
@limiter.limit("30/minute")
@monitor_endpoint("get_user_company_assignment")
async def get_user_company_assignment(
    request: Request,
    assignment_id: str,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.get_assignment(assignment_id, current_user)
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting assignment {assignment_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get assignment",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("get_user_company_assignment", time.time() - start_time)


@router.get(
    "/branch/{company_branch_id}/users",
    response_model=ApiResponse[UserCompanyListResponse],
    summary="List users in company branch",
    description="List all users assigned to a specific company branch"
)
@limiter.limit("20/minute")
@monitor_endpoint("list_branch_users")
async def list_branch_users(
    request: Request,
    company_branch_id: str,
    active_only: bool = True,
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.list_branch_users(
            company_branch_id=company_branch_id,
            active_only=active_only,
            page=page,
            size=size,
            current_user=current_user
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error listing branch users: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to list branch users",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("list_branch_users", time.time() - start_time)


@router.get(
    "/user/{user_id}/branches",
    response_model=ApiResponse[List[UserCompanyResponse]],
    summary="List user's company branches",
    description="List all company branches assigned to a specific user"
)
@limiter.limit("20/minute")
@monitor_endpoint("list_user_branches")
async def list_user_branches(
    request: Request,
    user_id: str,
    active_only: bool = True,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.list_user_branches(
            user_id=user_id,
            active_only=active_only,
            current_user=current_user
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error listing user branches: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to list user branches",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("list_user_branches", time.time() - start_time)


@router.get(
    "/statistics/{company_branch_id}",
    response_model=ApiResponse[UserCompanyStats],
    summary="Get branch assignment statistics",
    description="Get statistics about user assignments for a company branch"
)
@limiter.limit("10/minute")
@monitor_endpoint("get_branch_assignment_stats")
async def get_branch_assignment_stats(
    request: Request,
    company_branch_id: str,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.get_branch_assignment_stats(
            company_branch_id=company_branch_id,
            current_user=current_user
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting branch stats: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get branch statistics",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("get_branch_assignment_stats", time.time() - start_time)


@router.put(
    "/assignments/{assignment_id}/role",
    response_model=ApiResponse[UserCompanyResponse],
    summary="Update assignment role",
    description="Update the role of a user in a company branch"
)
@limiter.limit("5/minute")
@monitor_endpoint("update_assignment_role")
@audit_log_action("user_company.role_updated")
async def update_assignment_role(
    request: Request,
    assignment_id: str,
    role: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    try:
        response_data = await UserCompanyService.update_assignment_role(
            assignment_id=assignment_id,
            role=role,
            current_user=current_user
        )
        
        background_tasks.add_task(
            logger.info,
            f"Assignment {assignment_id} role updated to {role} by {current_user.id}"
        )
        
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error updating assignment role: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to update assignment role",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("update_assignment_role", time.time() - start_time)