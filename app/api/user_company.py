from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import logging

from app.repositories.user_company_repository import UserCompanyRepository
from app.repositories.user_repository import UserRepository
from app.repositories.company_repository import CompanyRepository
from app.schemas.user_company import (
    AssignUserToCompanyBranch,
    UserCompanyResponse,
    UserCompanyListResponse,
    UserCompanyStats
)
from app.core.security import get_current_user, require_permission
from app.models.user import User
from app.core.monitoring import monitor_endpoint, record_response_time, record_business_metric
from app.middleware.audit_log import audit_log_action

router = APIRouter()
logger = logging.getLogger(__name__)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/assign",
    status_code=status.HTTP_201_CREATED,
    summary="Assign user to company branch",
    description="Assign a user to a specific company branch"
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
    import time
    start_time = time.time()
    
    try:
        user_role = await CompanyRepository.get_user_company_role(
            user_id=str(current_user.id),
            company_id=data.company_id  # Need to get company_id from branch
        )
        
        if not user_role or user_role not in ["owner", "admin", "manager"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners, admins and managers can assign users"
            )
        
        branch = await CompanyRepository.get_company_branch(data.company_branch_id)
        if not branch or not branch.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company branch not found or inactive"
            )
        
        user = await UserRepository.get_user(data.user_id)
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found or inactive"
            )
        
        has_company_access = await CompanyRepository.validate_user_access(
            user_id=data.user_id,
            company_branch_id=data.company_branch_id
        )
        
        if not has_company_access:
            success = await CompanyRepository.add_company_member(
                company_id=str(branch.company_id),
                user_id=data.user_id,
                role="member",
                added_by=str(current_user.id)
            )
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to add user to company members"
                )
        
        assignment = await UserCompanyRepository.assign_user_to_branch(
            user_id=data.user_id,
            company_branch_id=data.company_branch_id,
            assigned_by=str(current_user.id),
            role=data.role,
            permissions=data.permissions
        )
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to assign user to branch"
            )
        
        record_business_metric(
            "user_assigned_to_branch",
            tags={
                "company_branch_id": data.company_branch_id,
                "assigned_by": str(current_user.id),
                "role": data.role or "member"
            }
        )
        
        background_tasks.add_task(
            logger.info,
            f"User {data.user_id} assigned to branch {data.company_branch_id} by {current_user.id}"
        )
        
        return JSONResponse(
            content={
                "success": True,
                "message": "User assigned to company branch successfully",
                "assignment_id": str(assignment.id),
                "user_id": data.user_id,
                "company_branch_id": data.company_branch_id,
                "role": data.role or "member",
                "timestamp": time.time()
            },
            status_code=status.HTTP_201_CREATED
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error assigning user to branch: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assign user to branch"
        )
    finally:
        record_response_time("assign_user_to_company_branch", time.time() - start_time)


@router.post(
    "/unassign",
    status_code=status.HTTP_200_OK,
    summary="Unassign user from company branch",
    description="Unassign a user from a specific company branch (soft delete)"
)
@limiter.limit("3/minute")
@monitor_endpoint("unassign_user_from_company_branch")
@audit_log_action("user_company.unassigned")
async def unassign_user_from_company_branch(
    request: Request,
    data: AssignUserToCompanyBranch,
    current_user: User = Depends(get_current_user)
):
    import time
    start_time = time.time()
    
    try:
        user_role = await CompanyRepository.get_user_company_role(
            user_id=str(current_user.id),
            company_id=data.company_id
        )
        
        if not user_role or user_role not in ["owner", "admin", "manager"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners, admins and managers can unassign users"
            )
        
        success = await UserCompanyRepository.unassign_user_from_branch(
            user_id=data.user_id,
            company_branch_id=data.company_branch_id,
            unassigned_by=str(current_user.id)
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User assignment not found"
            )
        
        record_business_metric(
            "user_unassigned_from_branch",
            tags={
                "company_branch_id": data.company_branch_id,
                "unassigned_by": str(current_user.id)
            }
        )
        
        logger.info(
            f"User {data.user_id} unassigned from branch {data.company_branch_id} by {current_user.id}"
        )
        
        return JSONResponse(
            content={
                "success": True,
                "message": "User unassigned from company branch successfully",
                "user_id": data.user_id,
                "company_branch_id": data.company_branch_id,
                "timestamp": time.time()
            },
            status_code=status.HTTP_200_OK
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unassigning user from branch: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unassign user from branch"
        )
    finally:
        record_response_time("unassign_user_from_company_branch", time.time() - start_time)


@router.delete(
    "/{assignment_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete user-company assignment",
    description="Permanently delete a user-company branch assignment (hard delete)"
)
@limiter.limit("2/minute")
@monitor_endpoint("delete_user_company_assignment")
@audit_log_action("user_company.deleted")
async def delete_user_company_assignment(
    request: Request,
    assignment_id: str,
    current_user: User = Depends(get_current_user)
):
    import time
    start_time = time.time()
    
    try:
        if not (current_user.is_superuser or "admin" in current_user.permissions):
            assignment = await UserCompanyRepository.get_assignment(assignment_id)
            if assignment:
                user_role = await CompanyRepository.get_user_company_role(
                    user_id=str(current_user.id),
                    company_id=assignment.company_id  # Need company_id in assignment
                )
                
                if not user_role or user_role not in ["owner", "admin"]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only owners and admins can delete assignments"
                    )
        
        success = await UserCompanyRepository.delete_assignment(
            assignment_id=assignment_id,
            deleted_by=str(current_user.id)
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assignment not found"
            )
        
        record_business_metric(
            "user_company_assignment_deleted",
            tags={"deleted_by": str(current_user.id)}
        )
        
        logger.warning(
            f"HARD DELETE user_company assignment: {assignment_id} by {current_user.id}"
        )
        
        return JSONResponse(
            content={
                "success": True,
                "message": "User-company assignment deleted permanently",
                "assignment_id": assignment_id,
                "timestamp": time.time()
            },
            status_code=status.HTTP_200_OK
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting assignment {assignment_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete assignment"
        )
    finally:
        record_response_time("delete_user_company_assignment", time.time() - start_time)


@router.get(
    "/assignments/{assignment_id}",
    response_model=UserCompanyResponse,
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
    import time
    start_time = time.time()
    
    try:
        assignment = await UserCompanyRepository.get_assignment(assignment_id)
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assignment not found"
            )
        
        has_access = await CompanyRepository.validate_user_access(
            user_id=str(current_user.id),
            company_branch_id=assignment.company_branch_id
        )
        
        if not has_access and not (current_user.is_superuser or "admin" in current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        user = await UserRepository.get_user(assignment.user_id)
        branch = await CompanyRepository.get_company_branch(assignment.company_branch_id)
        
        if not user or not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User or branch not found"
            )
        
        return UserCompanyResponse(
            id=str(assignment.id),
            user_id=str(assignment.user_id),
            user_email=user.email,
            user_name=user.full_name,
            company_branch_id=str(assignment.company_branch_id),
            company_branch_name=branch.name,
            company_id=str(branch.company_id),
            role=assignment.role,
            permissions=assignment.permissions or [],
            is_active=assignment.is_active,
            assigned_at=assignment.assigned_at,
            unassigned_at=assignment.unassigned_at,
            assigned_by=str(assignment.assigned_by) if assignment.assigned_by else None,
            unassigned_by=str(assignment.unassigned_by) if assignment.unassigned_by else None,
            created_at=assignment.created_at,
            updated_at=assignment.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting assignment {assignment_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get assignment"
        )
    finally:
        record_response_time("get_user_company_assignment", time.time() - start_time)


@router.get(
    "/branch/{company_branch_id}/users",
    response_model=UserCompanyListResponse,
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
    import time
    start_time = time.time()
    
    try:
        has_access = await CompanyRepository.validate_user_access(
            user_id=str(current_user.id),
            company_branch_id=company_branch_id
        )
        
        if not has_access and not (current_user.is_superuser or "admin" in current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        if page < 1:
            page = 1
        if size < 1 or size > 100:
            size = 20
        
        skip = (page - 1) * size
        
        assignments, total = await UserCompanyRepository.list_branch_assignments(
            company_branch_id=company_branch_id,
            active_only=active_only,
            skip=skip,
            limit=size
        )
        
        assignments_with_details = []
        for assignment in assignments:
            user = await UserRepository.get_user(assignment.user_id)
            branch = await CompanyRepository.get_company_branch(assignment.company_branch_id)
            
            if user and branch:
                assignments_with_details.append(UserCompanyResponse(
                    id=str(assignment.id),
                    user_id=str(assignment.user_id),
                    user_email=user.email,
                    user_name=user.full_name,
                    company_branch_id=str(assignment.company_branch_id),
                    company_branch_name=branch.name,
                    company_id=str(branch.company_id),
                    role=assignment.role,
                    permissions=assignment.permissions or [],
                    is_active=assignment.is_active,
                    assigned_at=assignment.assigned_at,
                    unassigned_at=assignment.unassigned_at,
                    assigned_by=str(assignment.assigned_by) if assignment.assigned_by else None,
                    unassigned_by=str(assignment.unassigned_by) if assignment.unassigned_by else None,
                    created_at=assignment.created_at,
                    updated_at=assignment.updated_at
                ))
        
        record_business_metric(
            "branch_users_listed",
            value=len(assignments_with_details),
            tags={"company_branch_id": company_branch_id, "active_only": active_only}
        )
        
        return UserCompanyListResponse(
            assignments=assignments_with_details,
            total=total,
            page=page,
            size=size,
            company_branch_id=company_branch_id,
            active_only=active_only
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing branch users: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list branch users"
        )
    finally:
        record_response_time("list_branch_users", time.time() - start_time)


@router.get(
    "/user/{user_id}/branches",
    response_model=List[UserCompanyResponse],
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
    import time
    start_time = time.time()
    
    try:
        if user_id != str(current_user.id) and not (current_user.is_superuser or "admin" in current_user.permissions):
            user_assignments = await UserCompanyRepository.list_user_assignments(user_id, active_only)
            can_view = False
            
            for assignment in user_assignments:
                user_role = await CompanyRepository.get_user_company_role(
                    user_id=str(current_user.id),
                    company_branch_id=assignment.company_branch_id
                )
                if user_role in ["owner", "admin", "manager"]:
                    can_view = True
                    break
            
            if not can_view:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied"
                )
        
        assignments = await UserCompanyRepository.list_user_assignments(
            user_id=user_id,
            active_only=active_only
        )
        
        assignments_with_details = []
        for assignment in assignments:
            user = await UserRepository.get_user(assignment.user_id)
            branch = await CompanyRepository.get_company_branch(assignment.company_branch_id)
            
            if user and branch:
                assignments_with_details.append(UserCompanyResponse(
                    id=str(assignment.id),
                    user_id=str(assignment.user_id),
                    user_email=user.email,
                    user_name=user.full_name,
                    company_branch_id=str(assignment.company_branch_id),
                    company_branch_name=branch.name,
                    company_id=str(branch.company_id),
                    role=assignment.role,
                    permissions=assignment.permissions or [],
                    is_active=assignment.is_active,
                    assigned_at=assignment.assigned_at,
                    unassigned_at=assignment.unassigned_at,
                    assigned_by=str(assignment.assigned_by) if assignment.assigned_by else None,
                    unassigned_by=str(assignment.unassigned_by) if assignment.unassigned_by else None,
                    created_at=assignment.created_at,
                    updated_at=assignment.updated_at
                ))
        
        record_business_metric(
            "user_branches_listed",
            value=len(assignments_with_details),
            tags={"user_id": user_id, "active_only": active_only}
        )
        
        return assignments_with_details
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing user branches: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list user branches"
        )
    finally:
        record_response_time("list_user_branches", time.time() - start_time)


@router.get(
    "/statistics/{company_branch_id}",
    response_model=UserCompanyStats,
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
    import time
    start_time = time.time()
    
    try:
        has_access = await CompanyRepository.validate_user_access(
            user_id=str(current_user.id),
            company_branch_id=company_branch_id
        )
        
        if not has_access and not (current_user.is_superuser or "admin" in current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        stats = await UserCompanyRepository.get_branch_assignment_stats(company_branch_id)
        
        record_business_metric(
            "branch_stats_retrieved",
            tags={"company_branch_id": company_branch_id}
        )
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting branch stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get branch statistics"
        )
    finally:
        record_response_time("get_branch_assignment_stats", time.time() - start_time)


@router.put(
    "/assignments/{assignment_id}/role",
    response_model=UserCompanyResponse,
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
    current_user: User = Depends(get_current_user)
):
    import time
    start_time = time.time()
    
    try:
        assignment = await UserCompanyRepository.get_assignment(assignment_id)
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assignment not found"
            )
        
        user_role = await CompanyRepository.get_user_company_role(
            user_id=str(current_user.id),
            company_branch_id=assignment.company_branch_id
        )
        
        if not user_role or user_role not in ["owner", "admin", "manager"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners, admins and managers can update roles"
            )
        
        updated_assignment = await UserCompanyRepository.update_assignment_role(
            assignment_id=assignment_id,
            role=role,
            updated_by=str(current_user.id)
        )
        
        if not updated_assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assignment not found"
            )
        
        user = await UserRepository.get_user(updated_assignment.user_id)
        branch = await CompanyRepository.get_company_branch(updated_assignment.company_branch_id)
        
        if not user or not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User or branch not found"
            )
        
        record_business_metric(
            "assignment_role_updated",
            tags={
                "assignment_id": assignment_id,
                "old_role": assignment.role,
                "new_role": role,
                "updated_by": str(current_user.id)
            }
        )
        
        logger.info(
            f"Assignment {assignment_id} role updated from {assignment.role} to {role} by {current_user.id}"
        )
        
        return UserCompanyResponse(
            id=str(updated_assignment.id),
            user_id=str(updated_assignment.user_id),
            user_email=user.email,
            user_name=user.full_name,
            company_branch_id=str(updated_assignment.company_branch_id),
            company_branch_name=branch.name,
            company_id=str(branch.company_id),
            role=updated_assignment.role,
            permissions=updated_assignment.permissions or [],
            is_active=updated_assignment.is_active,
            assigned_at=updated_assignment.assigned_at,
            unassigned_at=updated_assignment.unassigned_at,
            assigned_by=str(updated_assignment.assigned_by) if updated_assignment.assigned_by else None,
            unassigned_by=str(updated_assignment.unassigned_by) if updated_assignment.unassigned_by else None,
            created_at=updated_assignment.created_at,
            updated_at=updated_assignment.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating assignment role: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update assignment role"
        )
    finally:
        record_response_time("update_assignment_role", time.time() - start_time)