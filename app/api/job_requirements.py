from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging

from app.services.job_requirement_service import JobRequirementService
from app.schemas.job_requirement import (
    JobRequirementCreate,
    JobRequirementUpdate,
    JobRequirementResponse,
    JobRequirementListResponse
)
from app.core.security import get_current_user, CurrentUser, require_permission
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/",
    response_model=JobRequirementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new job requirement",
    description="Create a new job requirement for screening resumes"
)
@limiter.limit("10/minute")
async def create_job_requirement(
    request: Request,
    job_data: JobRequirementCreate,
    current_user: CurrentUser = Depends(
        require_permission("job_requirements:create")
    ),
):
    try:
        job_data.user_id = str(current_user.user_id)
        job = await JobRequirementService.create_job_requirement(
            user_id=str(current_user.user_id),
            job_data=job_data
        )
        return job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in create_job_requirement API: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get(
    "/{job_id}",
    response_model=JobRequirementResponse,
    summary="Get job requirement by ID",
    description="Retrieve a specific job requirement by its ID"
)
@limiter.limit("30/minute")
async def get_job_requirement(
    request: Request,
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        user_id = str(current_user.user_id) if current_user else None
        job = await JobRequirementService.get_job_requirement(
            job_id=job_id,
            user_id=user_id
        )
        return job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_job_requirement API: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.put(
    "/{job_id}",
    response_model=JobRequirementResponse,
    summary="Update job requirement",
    description="Update an existing job requirement"
)
@limiter.limit("20/minute")
async def update_job_requirement(
    request: Request,
    job_id: str,
    update_data: JobRequirementUpdate,
    current_user: CurrentUser = Depends(
        require_permission("job_requirements:edit")
    ),
):
    try:
        job = await JobRequirementService.update_job_requirement(
            job_id=job_id,
            user_id=str(current_user.user_id),
            update_data=update_data
        )
        return job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in update_job_requirement API: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.delete(
    "/{job_id}",
    summary="Delete job requirement",
    description="Soft delete a job requirement"
)
@limiter.limit("10/minute")
async def delete_job_requirement(
    request: Request,
    job_id: str,
    hard_delete: bool = Query(False, description="Perform hard delete (admin only)"),
        current_user: CurrentUser = Depends(
        require_permission("job_requirements:delete")
    ),
):
    try:
        await JobRequirementService.delete_job_requirement(
            job_id=job_id,
            user_id=str(current_user.user_id),
            hard_delete=hard_delete
        )
        
        return JSONResponse(
            content={"message": "Job requirement deleted successfully"},
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in delete_job_requirement API: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get(
    "/",
    response_model=JobRequirementListResponse,
    summary="List job requirements",
    description="Get a paginated list of job requirements with optional filters"
)
@limiter.limit("60/minute")
async def list_job_requirements(
    request: Request,
    company_branch_id: Optional[str] = Query(None, description="Filter by company branch ID"),
    is_open: Optional[bool] = Query(None, description="Filter by open status"),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of items to return"),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        user_id = str(current_user.user_id) if current_user else None
        jobs, total = await JobRequirementService.list_job_requirements(
            user_id=user_id,
            company_branch_id=company_branch_id,
            is_open=is_open,
            skip=skip,
            limit=limit
        )
        
        return JobRequirementListResponse(
            items=jobs,
            total=total,
            skip=skip,
            limit=limit
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in list_job_requirements API: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get(
    "/search",
    response_model=JobRequirementListResponse,
    summary="Search job requirements",
    description="Search job requirements by text and filters"
)
@limiter.limit("30/minute")
async def search_job_requirements(
    request: Request,
    q: str = Query(..., min_length=2, description="Search query for title or description"),
    programming_languages: Optional[List[str]] = Query(None, description="Filter by programming languages"),
    skills: Optional[List[str]] = Query(None, description="Filter by required skills"),
    experience_level: Optional[str] = Query(None, description="Filter by experience level"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        user_id = str(current_user.user_id) if current_user else None
        jobs, total = await JobRequirementService.search_job_requirements(
            user_id=user_id,
            search_term=q,
            programming_languages=programming_languages,
            skills=skills,
            experience_level=experience_level,
            skip=skip,
            limit=limit
        )
        
        return JobRequirementListResponse(
            items=jobs,
            total=total,
            skip=skip,
            limit=limit
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in search_job_requirements API: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get(
    "/export",
    summary="Export job requirements",
    description="Export job requirements to CSV, Excel, or JSON format"
)
@limiter.limit("5/minute")
async def export_job_requirements(
    request: Request,
    format: str = Query("json", regex="^(csv|excel|json)$"),
    company_branch_id: Optional[str] = Query(None, description="Filter by company branch ID"),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        user_id = str(current_user.user_id)
        export_result = await JobRequirementService.export_job_requirements(
            user_id=user_id,
            company_branch_id=company_branch_id,
            format=format
        )
        
        return StreamingResponse(
            iter([export_result["content"]]),
            media_type=export_result["content_type"],
            headers={"Content-Disposition": f"attachment; filename={export_result['filename']}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in export_job_requirements API: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
