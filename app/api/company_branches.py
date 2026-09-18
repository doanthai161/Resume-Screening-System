from fastapi import Request, BackgroundTasks, APIRouter, Depends, status
from typing import List
import time
import logging

from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchResponse
from app.schemas.response import ApiResponse
from app.core.rate_limiter import limiter
from app.logs.logging_config import logger
from app.core.security import get_current_user, require_permission, CurrentUser
from app.models.user import User
from app.core.monitoring import monitor_endpoint, record_response_time
from app.middleware.audit_log import audit_log_action
from app.core.errors import CustomError, ErrorCodes
from app.services.company_branch_service import CompanyBranchService

router = APIRouter()

@router.post(
    "/{company_id}/branches",
    response_model=ApiResponse[CompanyBranchResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create company branch",
    description="Create a new branch for a company"
)
@limiter.limit("5/minute")
@monitor_endpoint("create_company_branch")
@audit_log_action("company_branch.created")
async def create_company_branch(
    request: Request,
    company_id: str,
    branch_data: CompanyBranchCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("companies:create")
    ),
):
    start_time = time.time()
    
    try:
        branch = await CompanyBranchService.create_company_branch(
            company_id=company_id,
            branch_data=branch_data,
            user_id=str(current_user.user_id),
            is_superuser=getattr(current_user, "is_superuser", False)
        )
        
        background_tasks.add_task(
            logger.info,
            f"User {current_user.email} created company branch {branch.id} for company {company_id}"
        )
        
        response_data = CompanyBranchResponse(
            id=str(branch.id),
            company_id=str(branch.company_id),
            bussiness_type=branch.bussiness_type,
            branch_name=branch.branch_name,
            phone_number=branch.phone_number,
            address=branch.address,
            description=branch.description,
            company_type=branch.company_type,
            company_industry=branch.company_industry,
            country=branch.country,
            company_size=branch.company_size,
            working_days=branch.working_days,
            overtime_policy=branch.overtime_policy,
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error creating company branch: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to create company branch",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("create_company_branch", time.time() - start_time)

@router.get(
    "/{company_id}/branches",
    response_model=ApiResponse[List[CompanyBranchResponse]],
    summary="List company branches",
    description="List all branches for a company"
)
@limiter.limit("10/minute")
@monitor_endpoint("list_company_branches")
async def list_company_branches(
    request: Request,
    company_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user)
):
    start_time = time.time()
    
    try:
        branches = await CompanyBranchService.list_company_branches(
            company_id=company_id,
            user_id=str(current_user.id),
            is_superuser=getattr(current_user, "is_superuser", False),
            permissions=getattr(current_user, "permissions", [])
        )
        
        background_tasks.add_task(
            logger.info,
            f"User {current_user.id} listed branches for company {company_id}"
        )
        
        response_data = [
            CompanyBranchResponse(
                id=str(branch.id),
                company_id=str(branch.company_id),
                bussiness_type=branch.bussiness_type,
                branch_name=branch.branch_name,
                phone_number=branch.phone_number,
                address=branch.address,
                description=branch.description,
                company_type=branch.company_type,
                company_industry=branch.company_industry,
                country=branch.country,
                company_size=branch.company_size,
                working_days=branch.working_days,
                overtime_policy=branch.overtime_policy,
            )
            for branch in branches
        ]
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error listing company branches: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to list company branches",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("list_company_branches", time.time() - start_time)

@router.get(
    "/{company_id}/branches/{branch_id}",
    response_model=ApiResponse[CompanyBranchResponse],
    summary="Get company branch",
    description="Get branch details by ID"
)
@limiter.limit("30/minute")
@monitor_endpoint("get_company_branch")
async def get_company_branch(
    request: Request,
    company_id: str,
    branch_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    start_time = time.time()
    
    try:
        branch = await CompanyBranchService.get_company_branch(
            company_id,
            branch_id,
            user_id=str(current_user.id),
            is_superuser=getattr(current_user, "is_superuser", False)
        )
        
        background_tasks.add_task(
            logger.info,
            f"User {current_user.id} retrieved branch {branch_id}"
        )
        
        response_data = CompanyBranchResponse(
            id=str(branch.id),
            company_id=str(branch.company_id),
            bussiness_type=branch.bussiness_type,
            branch_name=branch.branch_name,
            phone_number=branch.phone_number,
            address=branch.address,
            description=branch.description,
            company_type=branch.company_type,
            company_industry=branch.company_industry,
            country=branch.country,
            company_size=branch.company_size,
            working_days=branch.working_days,
            overtime_policy=branch.overtime_policy,
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting branch {branch_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get branch",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("get_company_branch", time.time() - start_time)
