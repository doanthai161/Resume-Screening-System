from itertools import count
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends
from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchResponse
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.logs.logging_config import logger
from app.core.security import (
    get_current_user,
)
from app.models.user import User
from app.core.monitoring import monitor_endpoint, record_response_time
from app.middleware.audit_log import audit_log_action
from app.repositories.company_branch_repository import CompanyBranchRepository
from typing import List
from app.core.security import get_current_user, require_permission, CurrentUser

router = APIRouter()
app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@router.post(
    "/{company_id}/branches",
    response_model=CompanyBranchResponse,
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
    import time
    start_time = time.time()
    
    try:
        branch = await CompanyBranchRepository.create_company_branch(
            company_id=company_id,
            branch_data=branch_data,
            created_by_id=str(current_user.user_id)
        )
        
        background_tasks.add_task(
            logger.info,
            f"User {current_user.email} created company branch {branch.id} for company {company_id}"
        )
        
        return CompanyBranchResponse(
            id = str(branch.id),
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
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating company branch: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create company branch"
        )
    finally:
        record_response_time("create_company_branch", time.time() - start_time)


@router.get(
    "/{company_id}/branches",
    response_model=List[CompanyBranchResponse],
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
    import time
    start_time = time.time()
    
    try:
        user_companies = await CompanyBranchRepository.get_user_company_branches(str(current_user.id))
        has_access = any(str(c.id) == company_id for c in user_companies)
        
        if not has_access and not (current_user.is_superuser or "admin" in current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        branches = await CompanyBranchRepository.get_company_branches(company_id)
        
        background_tasks.add_task(
            logger.info,
            f"User {current_user.id} listed branches for company {company_id}"
        )
        
        return [
            CompanyBranchResponse(
                id=str(branch.id),
                company_id=str(branch.company_id),
                name=branch.name,
                description=branch.description,
                address=branch.address,
                city=branch.city,
                country=branch.country,
                phone=branch.phone,
                email=branch.email,
                is_headquarters=branch.is_headquarters,
                is_active=branch.is_active,
                created_at=branch.created_at,
                updated_at=branch.updated_at
            )
            for branch in branches
        ]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing company branches: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list company branches"
        )
    finally:
        record_response_time("list_company_branches", time.time() - start_time)


@router.get(
    "/{company_id}/branches/{branch_id}",
    response_model=CompanyBranchResponse,
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
    import time
    start_time = time.time()
    
    try:
        
        branch = await CompanyBranchRepository.get_company_branch(branch_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found"
            )
        
        if str(branch.company_id) != company_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Branch does not belong to specified company"
            )
        
        background_tasks.add_task(
            logger.info,
            f"User {current_user.id} retrieved branch {branch_id}"
        )
        
        return CompanyBranchResponse(
            id = str(branch.id),
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
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting branch {branch_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get branch"
        )
    finally:
        record_response_time("get_company_branch", time.time() - start_time)
