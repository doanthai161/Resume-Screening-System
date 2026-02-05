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
)
from app.core.security import get_current_user, require_permission
from app.models.user import User
from app.core.monitoring import monitor_endpoint, record_response_time
from app.middleware.audit_log import audit_log_action

router = APIRouter()
logger = logging.getLogger(__name__)

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
    import time
    start_time = time.time()
    
    try:
        company = await CompanyRepository.create_company(
            company_data=company_data,
            owner_id=str(current_user.id)
        )
        
        background_tasks.add_task(
            logger.info,
            f"Company created: {company.id} - {company.name} by user {current_user.id}"
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
    import time
    start_time = time.time()
    
    try:
        if page < 1:
            page = 1
        if size < 1 or size > 100:
            size = 10
        
        skip = (page - 1) * size
        
        user_companies = await CompanyRepository.get_user_companies(str(current_user.id))
        
        is_admin = current_user.is_superuser or "admin" in current_user.permissions
        companies = []
        total = 0
        
        if is_admin and (search or industry):
            companies, total = await CompanyRepository.search_companies(
                search_term=search,
                industry=industry,
                skip=skip,
                limit=size
            )
        else:
            companies = user_companies[skip:skip + size]
            total = len(user_companies)
        
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
    import time
    start_time = time.time()
    
    try:
        company = await CompanyRepository.get_company(company_id)
        if not company:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found"
            )
        
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

