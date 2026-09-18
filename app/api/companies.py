from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.responses import JSONResponse
import logging
import time

from app.schemas.company import (
    CompanyCreate,
    CompanyUpdate,
    CompanyResponse,
    CompanyListResponse,
)
from app.schemas.response import ApiResponse
from app.core.security import get_current_user, require_permission, CurrentUser
from app.models.user import User
from app.core.monitoring import monitor_endpoint, record_response_time
from app.middleware.audit_log import audit_log_action
from app.core.rate_limiter import limiter
from app.core.errors import CustomError, ErrorCodes
from app.services.company_service import CompanyService

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "/",
    response_model=ApiResponse[CompanyResponse],
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
    current_user: CurrentUser = Depends(
        require_permission("companies:create")
    ),
):
    start_time = time.time()
    
    try:
        company = await CompanyService.create_company(company_data, str(current_user.id))
        
        background_tasks.add_task(
            logger.info,
            f"Company created: {company.id} - {company.name} by user {current_user.id}"
        )
        
        response_data = CompanyResponse(
            id=str(company.id),
            name=company.name,
            description=company.description,
            company_short_name=company.company_short_name,
            tax_code=company.tax_code,
            company_code=company.company_code,
            industry=company.industry,
            website=company.website,
            email=company.email,
            logo_url=company.logo_url,
            user_id=str(company.user_id),
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error creating company: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to create company",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("create_company", time.time() - start_time)

@router.get(
    "/",
    response_model=ApiResponse[CompanyListResponse],
    summary="List all active companies",
    description="Get a paginated list of all companies that are currently active."
)
@limiter.limit("10/minute")
@monitor_endpoint("list_companies")
async def list_companies(
    request: Request,
    background_tasks: BackgroundTasks,
    page: int = 1,
    size: int = 10,
    current_user: CurrentUser = Depends(get_current_user)
):
    start_time = time.time()
    
    try:
        companies, total = await CompanyService.list_companies(page, size)
        
        company_responses = []
        for company in companies:
            company_dict = company.model_dump()
            company_dict['id'] = str(company.id)
            company_dict['user_id'] = str(company.user_id)
            company_responses.append(CompanyResponse(**company_dict))
            
        background_tasks.add_task(
            logger.info,
            f"User {current_user.email} listed {len(company_responses)} active companies."
        )
        
        response_data = CompanyListResponse(
            companies=company_responses,
            total=total,
            page=page,
            size=size
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error listing companies: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to list companies",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("list_companies", time.time() - start_time)

@router.get(
    "/{company_id}",
    response_model=ApiResponse[CompanyResponse],
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
    start_time = time.time()
    
    try:
        company = await CompanyService.get_company(
            company_id=company_id,
            user_id=str(current_user.id),
            is_superuser=getattr(current_user, "is_superuser", False),
            permissions=getattr(current_user, "permissions", [])
        )
        
        background_tasks.add_task(
            logger.info,
            f"Company {company_id} retrieved by user {current_user.id}"
        )
        
        response_data = CompanyResponse(
            id=str(company.id),
            name=company.name,
            description=company.description,
            company_short_name=company.company_short_name,
            tax_code=company.tax_code,
            company_code=company.company_code,
            industry=company.industry,
            website=company.website,
            email=company.email,
            logo_url=company.logo_url,
            user_id=str(company.user_id),
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error getting company {company_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to get company",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("get_company", time.time() - start_time)

@router.put(
    "/{company_id}",
    response_model=ApiResponse[CompanyResponse],
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
    current_user: CurrentUser = Depends(
        require_permission("companies:edit")
    ),
):
    start_time = time.time()
    
    try:
        company = await CompanyService.update_company(company_id, update_data, str(current_user.id))
        
        background_tasks.add_task(
            logger.info,
            f"Company {company_id} updated by user {current_user.id}"
        )
        
        response_data = CompanyResponse(
            id=str(company.id),
            name=company.name,
            description=company.description,
            company_short_name=company.company_short_name,
            tax_code=company.tax_code,
            company_code=company.company_code,
            industry=company.industry,
            website=company.website,
            email=company.email,
            logo_url=company.logo_url,
            user_id=str(company.user_id),
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error updating company {company_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to update company",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("update_company", time.time() - start_time)

@router.delete(
    "/{company_id}",
    response_model=ApiResponse[Dict],
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
    current_user: CurrentUser = Depends(
        require_permission("companies:delete")
    ),
):
    start_time = time.time()
    
    try:
        await CompanyService.delete_company(company_id, str(current_user.id))
        
        background_tasks.add_task(
            logger.info,
            f"Company {company_id} deleted by user {current_user.id}"
        )
        
        return ApiResponse.ok({
            "message": "Company deleted successfully",
            "company_id": company_id,
            "timestamp": time.time()
        })
        
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"Error deleting company {company_id}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Failed to delete company",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    finally:
        record_response_time("delete_company", time.time() - start_time)

