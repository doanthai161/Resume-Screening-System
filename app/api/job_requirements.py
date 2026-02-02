from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
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
from app.core.security import get_current_user
from app.models.user import User
from app.core.monitoring import (
    monitor_endpoint,
    record_response_time
)
from app.middleware.audit_log import audit_log_action

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
@monitor_endpoint("create_job_requirement")
@audit_log_action("job_requirement.created")
async def create_job_requirement(
    job_data: JobRequirementCreate,
    current_user: User = Depends(get_current_user)
):
    """
    Create a new job requirement.
    
    - **user_id**: ID of the user creating the job (automatically set from current user)
    - **company_branch_id**: ID of the company branch
    - **title**: Job title
    - **programming_languages**: Required programming languages
    - **skills_required**: Required skills
    - **experience_level**: Required experience level
    - **description**: Job description (optional)
    - **salary_min**: Minimum salary (optional)
    - **salary_max**: Maximum salary (optional)
    - **expiration_time**: Job expiration date (optional)
    """
    import time
    start_time = time.time()
    
    try:
        # Override user_id with current user
        job_data.user_id = str(current_user.id)
        
        job = await JobRequirementService.create_job_requirement(
            user_id=str(current_user.id),
            job_data=job_data
        )
        
        return job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in create_job_requirement: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
    finally:
        record_response_time("create_job_requirement", time.time() - start_time)


@router.get(
    "/{job_id}",
    response_model=JobRequirementResponse,
    summary="Get job requirement by ID",
    description="Retrieve a specific job requirement by its ID"
)
@limiter.limit("30/minute")
@monitor_endpoint("get_job_requirement")
async def get_job_requirement(
    job_id: str,
    current_user: Optional[User] = Depends(get_current_user)
):
    """
    Get job requirement by ID.
    
    - **job_id**: The ID of the job requirement to retrieve
    """
    import time
    start_time = time.time()
    
    try:
        user_id = str(current_user.id) if current_user else None
        job = await JobRequirementService.get_job_requirement(
            job_id=job_id,
            user_id=user_id
        )
        
        return job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_job_requirement: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
    finally:
        record_response_time("get_job_requirement", time.time() - start_time)


@router.put(
    "/{job_id}",
    response_model=JobRequirementResponse,
    summary="Update job requirement",
    description="Update an existing job requirement"
)
@limiter.limit("20/minute")
@monitor_endpoint("update_job_requirement")
@audit_log_action("job_requirement.updated")
async def update_job_requirement(
    job_id: str,
    update_data: JobRequirementUpdate,
    current_user: User = Depends(get_current_user)
):
    """
    Update job requirement.
    
    - **job_id**: The ID of the job requirement to update
    - **update_data**: Fields to update
    """
    import time
    start_time = time.time()
    
    try:
        job = await JobRequirementService.update_job_requirement(
            job_id=job_id,
            user_id=str(current_user.id),
            update_data=update_data
        )
        
        return job
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in update_job_requirement: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
    finally:
        record_response_time("update_job_requirement", time.time() - start_time)


@router.delete(
    "/{job_id}",
    summary="Delete job requirement",
    description="Soft delete a job requirement"
)
@limiter.limit("10/minute")
@monitor_endpoint("delete_job_requirement")
@audit_log_action("job_requirement.deleted")
async def delete_job_requirement(
    job_id: str,
    hard_delete: bool = Query(False, description="Perform hard delete (admin only)"),
    current_user: User = Depends(get_current_user)
):
    """
    Delete job requirement.
    
    - **job_id**: The ID of the job requirement to delete
    - **hard_delete**: If true, perform hard delete (admin only)
    """
    import time
    start_time = time.time()
    
    try:
        result = await JobRequirementService.delete_job_requirement(
            job_id=job_id,
            user_id=str(current_user.id),
            hard_delete=hard_delete
        )
        
        return JSONResponse(
            content={"message": "Job requirement deleted successfully"},
            status_code=status.HTTP_200_OK
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in delete_job_requirement: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
    finally:
        record_response_time("delete_job_requirement", time.time() - start_time)
