# app/api/v1/endpoints/companies.py
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import logging

from app.repositories.company_repository import CompanyRepository
from app.schemas.company import (
    CompanyCreate,
    CompanyUpdate,
    CompanyResponse,
    CompanyListResponse,
    CompanyBranchCreate,
    CompanyBranchUpdate,
    CompanyBranchResponse
)
from app.core.security import get_current_user, require_permission
from app.models.user import User
from app.core.monitoring import monitor_endpoint, record_response_time
from app.middleware.audit_log import audit_log_action

router = APIRouter()
logger = logging.getLogger(__name__)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new company",
    description="Create a new company with the current user as owner"
)
@limiter.limit("3/minute")
@monitor_endpoint("create_company")
@audit_log_action("company.created")
async def create_company(
    request: Request,
    company_data: CompanyCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """Create a new company"""
    import time
    start_time = time.time()
    
    try:
        # Use repository to create company
        company = await CompanyRepository.create_company(
            company_data=company_data,
            owner_id=str(current_user.id)
        )
        
        # Background logging
        background_tasks.add_task(
            logger.info,
            f"Company created: {company.id} - {company.name} by user {current_user.id}"
        )
        
        # Convert to response model
        return CompanyResponse(
            id=str(company.id),
            name=company.name,
            description=company.description,
            industry=company.industry,
            size=company.size,
            website=company.website,
            phone=company.phone,
            email=company.email,
            address=company.address,
            city=company.city,
            country=company.country,
            logo_url=company.logo_url,
            owner_id=str(company.owner_id),
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating company: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create company"
        )
    finally:
        record_response_time("create_company", time.time() - start_time)


@router.get(
    "/",
    response_model=CompanyListResponse,
    summary="List companies",
    description="List companies with pagination and filtering"
)
@limiter.limit("10/minute")
@monitor_endpoint("list_companies")
async def list_companies(
    request: Request,
    background_tasks: BackgroundTasks,
    page: int = 1,
    size: int = 10,
    search: Optional[str] = None,
    industry: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """List companies with pagination"""
    import time
    start_time = time.time()
    
    try:
        # Validate pagination
        if page < 1:
            page = 1
        if size < 1 or size > 100:
            size = 10
        
        skip = (page - 1) * size
        
        # Get user's companies from repository
        user_companies = await CompanyRepository.get_user_companies(str(current_user.id))
        
        # If user wants all companies (admin), use search
        is_admin = current_user.is_superuser or "admin" in current_user.permissions
        companies = []
        total = 0
        
        if is_admin and (search or industry):
            # Admin can search all companies
            companies, total = await CompanyRepository.search_companies(
                search_term=search,
                industry=industry,
                skip=skip,
                limit=size
            )
        else:
            # Regular user gets their companies
            companies = user_companies[skip:skip + size]
            total = len(user_companies)
        
        # Convert to response models
        company_responses = []
        for company in companies:
            company_responses.append(CompanyResponse(
                id=str(company.id),
                name=company.name,
                description=company.description,
                industry=company.industry,
                size=company.size,
                website=company.website,
                phone=company.phone,
                email=company.email,
                address=company.address,
                city=company.city,
                country=company.country,
                logo_url=company.logo_url,
                owner_id=str(company.owner_id),
                is_active=company.is_active,
                created_at=company.created_at,
                updated_at=company.updated_at
            ))
        
        background_tasks.add_task(
            logger.info,
            f"User {current_user.id} listed {len(company_responses)} companies"
        )
        
        return CompanyListResponse(
            companies=company_responses,
            total=total,
            page=page,
            size=size
        )
        
    except Exception as e:
        logger.error(f"Error listing companies: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list companies"
        )
    finally:
        record_response_time("list_companies", time.time() - start_time)


@router.get(
    "/{company_id}",
    response_model=CompanyResponse,
    summary="Get company by ID",
    description="Get company details by ID with authorization check"
)
@limiter.limit("30/minute")
@monitor_endpoint("get_company")
async def get_company(
    request: Request,
    company_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """Get company by ID"""
    import time
    start_time = time.time()
    
    try:
        # Get company from repository
        company = await CompanyRepository.get_company(company_id)
        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found"
            )
        
        # Check if user has access to this company
        user_companies = await CompanyRepository.get_user_companies(str(current_user.id))
        has_access = any(str(c.id) == company_id for c in user_companies)
        
        if not has_access and not (current_user.is_superuser or "admin" in current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        background_tasks.add_task(
            logger.info,
            f"Company {company_id} retrieved by user {current_user.id}"
        )
        
        return CompanyResponse(
            id=str(company.id),
            name=company.name,
            description=company.description,
            industry=company.industry,
            size=company.size,
            website=company.website,
            phone=company.phone,
            email=company.email,
            address=company.address,
            city=company.city,
            country=company.country,
            logo_url=company.logo_url,
            owner_id=str(company.owner_id),
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting company {company_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get company"
        )
    finally:
        record_response_time("get_company", time.time() - start_time)


@router.put(
    "/{company_id}",
    response_model=CompanyResponse,
    summary="Update company",
    description="Update company details"
)
@limiter.limit("5/minute")
@monitor_endpoint("update_company")
@audit_log_action("company.updated")
async def update_company(
    request: Request,
    company_id: str,
    update_data: CompanyUpdate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """Update company"""
    import time
    start_time = time.time()
    
    try:
        # Check if user has permission to update
        user_role = await CompanyRepository.get_user_company_role(
            user_id=str(current_user.id),
            company_id=company_id
        )
        
        if not user_role or user_role not in ["owner", "admin"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners and admins can update company"
            )
        
        # Update company using repository
        company = await CompanyRepository.update_company(company_id, update_data)
        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found"
            )
        
        background_tasks.add_task(
            logger.info,
            f"Company {company_id} updated by user {current_user.id}"
        )
        
        return CompanyResponse(
            id=str(company.id),
            name=company.name,
            description=company.description,
            industry=company.industry,
            size=company.size,
            website=company.website,
            phone=company.phone,
            email=company.email,
            address=company.address,
            city=company.city,
            country=company.country,
            logo_url=company.logo_url,
            owner_id=str(company.owner_id),
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating company {company_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update company"
        )
    finally:
        record_response_time("update_company", time.time() - start_time)


@router.delete(
    "/{company_id}",
    summary="Delete company",
    description="Soft delete a company"
)
@limiter.limit("3/minute")
@monitor_endpoint("delete_company")
@audit_log_action("company.deleted")
async def delete_company(
    request: Request,
    company_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """Delete company (soft delete)"""
    import time
    start_time = time.time()
    
    try:
        # Delete company using repository
        success = await CompanyRepository.delete_company(
            company_id=company_id,
            user_id=str(current_user.id)
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found or unauthorized"
            )
        
        background_tasks.add_task(
            logger.info,
            f"Company {company_id} deleted by user {current_user.id}"
        )
        
        return JSONResponse(
            content={
                "success": True,
                "message": "Company deleted successfully",
                "company_id": company_id,
                "timestamp": time.time()
            },
            status_code=status.HTTP_200_OK
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error deleting company {company_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete company"
        )
    finally:
        record_response_time("delete_company", time.time() - start_time)


# ==================== COMPANY BRANCH ENDPOINTS ====================

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
    current_user: User = Depends(get_current_user)
):
    """Create a new company branch"""
    import time
    start_time = time.time()
    
    try:
        # Create branch using repository
        branch = await CompanyRepository.create_company_branch(
            company_id=company_id,
            branch_data=branch_data,
            created_by=str(current_user.id)
        )
        
        background_tasks.add_task(
            logger.info,
            f"Company branch created: {branch.id} - {branch.name} by user {current_user.id}"
        )
        
        return CompanyBranchResponse(
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
    current_user: User = Depends(get_current_user)
):
    """List company branches"""
    import time
    start_time = time.time()
    
    try:
        # Check if user has access to company
        user_companies = await CompanyRepository.get_user_companies(str(current_user.id))
        has_access = any(str(c.id) == company_id for c in user_companies)
        
        if not has_access and not (current_user.is_superuser or "admin" in current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Get branches using repository
        branches = await CompanyRepository.get_company_branches(company_id)
        
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
    """Get company branch by ID"""
    import time
    start_time = time.time()
    
    try:
        # Validate user access to branch
        has_access = await CompanyRepository.validate_user_access(
            user_id=str(current_user.id),
            company_branch_id=branch_id
        )
        
        if not has_access and not (current_user.is_superuser or "admin" in current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Get branch using repository
        branch = await CompanyRepository.get_company_branch(branch_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found"
            )
        
        # Verify branch belongs to company
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
