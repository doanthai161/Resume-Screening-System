from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
from fastapi import HTTPException, status
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repositories.job_requirement_repository import JobRequirementRepository
from app.schemas.job_requirement import (
    JobRequirementCreate, 
    JobRequirementUpdate, 
    JobRequirementResponse,
    JobRequirementListResponse
)
from app.models.job_requirement import JobRequirement
from app.core.monitoring import (
    monitor_service_call, 
    record_business_metric,
    start_trace,
    end_trace
)
from app.core.redis import get_redis, is_redis_available

logger = logging.getLogger(__name__)


class JobRequirementService:
    
    @staticmethod
    @monitor_service_call("create_job_requirement")
    async def create_job_requirement(
        user_id: str,
        job_data: JobRequirementCreate
    ) -> JobRequirementResponse:
        trace = start_trace("create_job_requirement")
        
        try:
            # if not await JobRequirementService._validate_user_company_access(
            #     user_id, job_data.company_branch_id
            # ):
            #     raise HTTPException(
            #         status_code=status.HTTP_403_FORBIDDEN,
            #         detail="User does not have access to this company branch"
            #     )
            
            if job_data.expiration_time and job_data.expiration_time < datetime.now():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Expiration time cannot be in the past"
                )
            
            if (job_data.salary_min and job_data.salary_max and 
                job_data.salary_min > job_data.salary_max):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Minimum salary cannot be greater than maximum salary"
                )
            
            if not job_data.programming_languages:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one programming language is required"
                )
            
            if not job_data.skills_required:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one skill is required"
                )
            
            try:
                repo = JobRequirementRepository()
                job = await repo.create_job_requirement(job_data)
            except DuplicateKeyError:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A similar job requirement already exists"
                )
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e)
                )
            
            record_business_metric(
                "job_requirement_created",
                tags={
                    "user_id": user_id,
                    "company_branch_id": job_data.company_branch_id,
                    "experience_level": job_data.experience_level
                }
            )
            
            await JobRequirementService._invalidate_related_caches(job)
            
            return JobRequirementService._to_response(job)
            
        except HTTPException:
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error creating job requirement: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create job requirement"
            )
        finally:
            end_trace(trace, success=True)
    
    @staticmethod
    @monitor_service_call("get_job_requirement")
    async def get_job_requirement(
        job_id: str,
        user_id: Optional[str] = None
    ) -> JobRequirementResponse:
        trace = start_trace("get_job_requirement")
        
        try:
            cache_key = f"job_req:{job_id}"
            if is_redis_available():
                try:
                    redis_client = get_redis()
                    import json
                    cached = await redis_client.get(cache_key)
                    if cached:
                        cached_data = json.loads(cached)
                        record_business_metric("cache_hit", tags={"type": "job_requirement"})
                        
                        if user_id and cached_data.get("user_id") != user_id:
                            if not await JobRequirementService._validate_user_company_access(
                                user_id, cached_data.get("company_branch_id")
                            ):
                                raise HTTPException(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    detail="Access denied"
                                )
                        
                        return JobRequirementResponse(**cached_data)
                except Exception as e:
                    logger.warning(f"Cache error for job {job_id}: {e}")
            
            record_business_metric("cache_miss", tags={"type": "job_requirement"})
            
            repo = JobRequirementRepository()
            job = await repo.get_job_requirement(job_id)
            if not job:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job requirement not found"
                )
            
            if is_redis_available():
                try:
                    redis_client = get_redis()
                    import json
                    await redis_client.setex(
                        cache_key,
                        3600,
                        json.dumps(JobRequirementService._to_response(job).dict())
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache job {job_id}: {e}")
            
            return JobRequirementService._to_response(job)
            
        except HTTPException:
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error getting job requirement {job_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get job requirement"
            )
        finally:
            end_trace(trace, success=True)
    
    # @classmethod
    # @monitor_service_call("search_job_requirements")
    async def search_job_requirements(
        self,
        search_term: Optional[str],
        programming_languages: Optional[List[str]],
        skills: Optional[List[str]],
        experience_level: Optional[str],
        skip: int,
        limit: int,
    ) -> JobRequirementListResponse:
        trace = start_trace("search_job_requirements")
        logger.info(
            "[API] Calling Service.search_job_requirements with kwargs: "
            f"{dict(
                search_term=search_term,
                programming_languages=programming_languages,
                skills=skills,
                experience_level=experience_level,
                skip=skip,
                limit=limit,
            )}"
        )
        
        try:
            repository = JobRequirementRepository()
            jobs, total = await repository.search_job_requirements(
                search_term=search_term,
                programming_languages=programming_languages,
                skills=skills,
                experience_level=experience_level,
                skip=skip,
                limit=limit
            )
            
            response_items = [JobRequirementResponse.model_validate(job) for job in jobs]
            
            return JobRequirementListResponse(
                items=response_items,
                total=total,
                skip=skip,
                limit=limit
            )

        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error searching job requirements: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to search job requirements"
            )
        finally:
            end_trace(trace, success=True)

    @staticmethod
    @monitor_service_call("update_job_requirement")
    async def update_job_requirement(
        job_id: str,
        user_id: str,
        update_data: JobRequirementUpdate
    ) -> JobRequirementResponse:
        """Update job requirement with authorization"""
        trace = start_trace("update_job_requirement")
        
        try:
            # Get existing job
            job = await JobRequirementRepository.get_job_requirement(job_id)
            if not job:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job requirement not found"
                )
            
            # Check ownership
            if str(job.user_id) != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only the creator can update this job requirement"
                )
            
            # Check if job is still active
            if not job.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot update an inactive job requirement"
                )
            
            # Validate updates
            if update_data.expiration_time and update_data.expiration_time < datetime.now():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Expiration time cannot be in the past"
                )
            
            if (update_data.salary_min and update_data.salary_max and 
                update_data.salary_min > update_data.salary_max):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Minimum salary cannot be greater than maximum salary"
                )
            
            # Update the job
            updated_job = await JobRequirementRepository.update_job_requirement(
                job_id, update_data
            )
            
            if not updated_job:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job requirement not found after update"
                )
            
            # Record business metric
            record_business_metric(
                "job_requirement_updated",
                tags={"user_id": user_id, "job_id": job_id}
            )
            
            # Invalidate related caches
            await JobRequirementService._invalidate_related_caches(updated_job)
            
            return JobRequirementService._to_response(updated_job)
            
        except HTTPException:
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error updating job requirement {job_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update job requirement"
            )
        finally:
            end_trace(trace, success=True)
    
    @staticmethod
    @monitor_service_call("delete_job_requirement")
    async def delete_job_requirement(
        job_id: str,
        user_id: str,
        hard_delete: bool = False
    ) -> Dict[str, Any]:
        """Delete job requirement (soft delete by default)"""
        trace = start_trace("delete_job_requirement")
        
        try:
            # Get job to check ownership
            job = await JobRequirementRepository.get_job_requirement(job_id)
            if not job:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job requirement not found"
                )
            
            # Check ownership
            if str(job.user_id) != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only the creator can delete this job requirement"
                )
            
            if hard_delete:
                # Hard delete (admin only - implement admin check in production)
                if not await JobRequirementService._is_admin(user_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Hard delete requires admin privileges"
                    )
                
                # Actually delete from database
                await job.delete()
                action = "hard_deleted"
                
                # Clear all related caches
                await JobRequirementService._clear_all_job_caches(job_id, user_id)
            else:
                # Soft delete
                success = await JobRequirementRepository.delete_job_requirement(job_id)
                if not success:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Job requirement not found"
                    )
                action = "soft_deleted"
                
                # Invalidate related caches
                await JobRequirementService._invalidate_related_caches(job)
            
            record_business_metric(
                "job_requirement_deleted",
                tags={"user_id": user_id, "job_id": job_id, "action": action}
            )
            
            return {
                "success": True,
                "message": f"Job requirement {action} successfully",
                "job_id": job_id,
                "action": action,
                "timestamp": datetime.now().isoformat()
            }
            
        except HTTPException:
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error deleting job requirement {job_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete job requirement"
            )
        finally:
            end_trace(trace, success=True)
    
    @staticmethod
    @monitor_service_call("list_job_requirements")
    async def list_job_requirements(
        user_id: Optional[str] = None,
        company_branch_id: Optional[str] = None,
        is_open: Optional[bool] = None,
        is_active: Optional[bool] = True,
        page: int = 1,
        size: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> JobRequirementListResponse:
        """List job requirements with pagination and caching"""
        trace = start_trace("list_job_requirements")
        
        try:
            # Validate pagination
            if page < 1:
                page = 1
            if size < 1 or size > 100:
                size = 20
            
            skip = (page - 1) * size
            sort_order_int = -1 if sort_order == "desc" else 1
            
            # Build cache key
            cache_key_parts = [
                "job_req_list",
                f"user:{user_id}" if user_id else "user:all",
                f"branch:{company_branch_id}" if company_branch_id else "branch:all",
                f"open:{is_open}" if is_open is not None else "open:all",
                f"active:{is_active}",
                f"page:{page}",
                f"size:{size}",
                f"sort:{sort_by}:{sort_order}"
            ]
            cache_key = ":".join(cache_key_parts)
            
            # Try cache first
            if is_redis_available():
                try:
                    redis_client = get_redis()
                    import json
                    cached = await redis_client.get(cache_key)
                    if cached:
                        cached_data = json.loads(cached)
                        record_business_metric("cache_hit", tags={"type": "job_list"})
                        
                        return JobRequirementListResponse(**cached_data)
                except Exception as e:
                    logger.warning(f"Cache error for job list: {e}")
            
            record_business_metric("cache_miss", tags={"type": "job_list"})
            
            # Get jobs from repository
            jobs, total = await JobRequirementRepository.list_job_requirements(
                user_id=user_id,
                company_branch_id=company_branch_id,
                is_open=is_open,
                is_active=is_active,
                skip=skip,
                limit=size,
                sort_by=sort_by,
                sort_order=sort_order_int
            )
            
            # Convert to response models
            job_responses = [JobRequirementService._to_response(job) for job in jobs]
            
            response = JobRequirementListResponse(
                job_requirements=job_responses,
                total=total,
                page=page,
                size=size
            )
            
            # Cache the result
            if is_redis_available() and jobs:
                try:
                    redis_client = get_redis()
                    import json
                    await redis_client.setex(
                        cache_key,
                        300,  # 5 minutes TTL for lists
                        json.dumps(response.dict())
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache job list: {e}")
            
            record_business_metric(
                "job_requirement_listed",
                value=len(job_responses),
                tags={"user_id": user_id or "anonymous", "page": page}
            )
            
            return response
            
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error listing job requirements: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to list job requirements"
            )
        finally:
            end_trace(trace, success=True)
    
    @staticmethod
    @monitor_service_call("search_job_requirements")
    async def search_job_requirements(
        search_term: Optional[str] = None,
        programming_languages: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        experience_level: Optional[str] = None,
        page: int = 1,
        size: int = 20
    ) -> JobRequirementListResponse:
        """Search job requirements with filters"""
        trace = start_trace("search_job_requirements")
        
        try:
            # Validate pagination
            if page < 1:
                page = 1
            if size < 1 or size > 100:
                size = 20
            
            skip = (page - 1) * size
            
            # Search jobs
            jobs, total = await JobRequirementRepository.search_job_requirements(
                search_term=search_term or "",
                programming_languages=programming_languages,
                skills=skills,
                experience_level=experience_level,
                skip=skip,
                limit=size
            )
            
            # Convert to response models
            job_responses = [JobRequirementService._to_response(job) for job in jobs]
            
            record_business_metric(
                "job_requirement_searched",
                value=len(job_responses),
                tags={
                    "has_search_term": bool(search_term),
                    "has_filters": bool(programming_languages or skills or experience_level)
                }
            )
            
            return JobRequirementListResponse(
                job_requirements=job_responses,
                total=total,
                page=page,
                size=size
            )
            
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error searching job requirements: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to search job requirements"
            )
        finally:
            end_trace(trace, success=True)
    
    @staticmethod
    @monitor_service_call("get_job_stats")
    async def get_job_stats(user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get statistics about job requirements"""
        trace = start_trace("get_job_stats")
        
        try:
            # Build cache key
            cache_key = f"job_stats:user:{user_id or 'all'}"
            
            # Try cache first
            if is_redis_available():
                try:
                    redis_client = get_redis()
                    import json
                    cached = await redis_client.get(cache_key)
                    if cached:
                        record_business_metric("cache_hit", tags={"type": "job_stats"})
                        return json.loads(cached)
                except Exception as e:
                    logger.warning(f"Cache error for job stats: {e}")
            
            record_business_metric("cache_miss", tags={"type": "job_stats"})
            
            # Get counts
            total_active = await JobRequirementRepository.get_active_job_count(user_id)
            
            # Get recent jobs
            recent_jobs, _ = await JobRequirementRepository.list_job_requirements(
                user_id=user_id,
                is_active=True,
                is_open=True,
                skip=0,
                limit=5
            )
            
            # Calculate metrics
            stats = {
                "total_active_jobs": total_active,
                "recent_jobs": len(recent_jobs),
                "popular_skills": await JobRequirementService._get_popular_skills(user_id),
                "popular_languages": await JobRequirementService._get_popular_languages(user_id),
                "timestamp": datetime.now().isoformat()
            }
            
            # Cache the result
            if is_redis_available():
                try:
                    redis_client = get_redis()
                    import json
                    await redis_client.setex(cache_key, 60, json.dumps(stats))  # 1 minute TTL
                except Exception as e:
                    logger.warning(f"Failed to cache job stats: {e}")
            
            record_business_metric(
                "job_stats_retrieved",
                tags={"user_id": user_id or "system"}
            )
            
            return stats
            
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error getting job stats: {e}", exc_info=True)
            return {
                "total_active_jobs": 0,
                "recent_jobs": 0,
                "popular_skills": [],
                "popular_languages": [],
                "timestamp": datetime.now().isoformat(),
                "error": "Failed to retrieve statistics"
            }
        finally:
            end_trace(trace, success=True)
    
    @staticmethod
    @monitor_service_call("bulk_update_job_status")
    async def bulk_update_job_status(
        job_ids: List[str],
        user_id: str,
        is_open: Optional[bool] = None,
        is_active: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Bulk update job statuses"""
        trace = start_trace("bulk_update_job_status")
        
        try:
            if not job_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No job IDs provided"
                )
            
            if is_open is None and is_active is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one status field (is_open or is_active) must be provided"
                )
            
            updated_count = 0
            failed_ids = []
            
            for job_id in job_ids:
                try:
                    # Get job
                    job = await JobRequirementRepository.get_job_requirement(job_id)
                    if not job:
                        failed_ids.append({"id": job_id, "reason": "not_found"})
                        continue
                    
                    # Check ownership
                    if str(job.user_id) != user_id:
                        failed_ids.append({"id": job_id, "reason": "unauthorized"})
                        continue
                    
                    # Update status
                    update_data = {}
                    if is_open is not None:
                        update_data["is_open"] = is_open
                    if is_active is not None:
                        update_data["is_active"] = is_active
                    
                    if update_data:
                        await JobRequirementRepository.update_job_requirement(
                            job_id, 
                            JobRequirementUpdate(**update_data)
                        )
                        updated_count += 1
                        
                        # Invalidate cache
                        await JobRequirementService._invalidate_related_caches(job)
                
                except Exception as e:
                    logger.error(f"Error updating job {job_id}: {e}")
                    failed_ids.append({"id": job_id, "reason": str(e)})
            
            # Record metrics
            record_business_metric(
                "job_bulk_update",
                value=updated_count,
                tags={
                    "user_id": user_id,
                    "total": len(job_ids),
                    "success": updated_count,
                    "failed": len(failed_ids)
                }
            )
            
            return {
                "success": True,
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "failed_ids": failed_ids,
                "timestamp": datetime.now().isoformat()
            }
            
        except HTTPException:
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error in bulk update: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to bulk update job statuses"
            )
        finally:
            end_trace(trace, success=True)
    
    @staticmethod
    @monitor_service_call("export_job_requirements")
    async def export_job_requirements(
        user_id: Optional[str] = None,
        company_branch_id: Optional[str] = None,
        format: str = "json"
    ) -> Dict[str, Any]:
        """Export job requirements to various formats"""
        trace = start_trace("export_job_requirements")
        
        try:
            # Get all jobs (no pagination for export)
            jobs, total = await JobRequirementRepository.list_job_requirements(
                user_id=user_id,
                company_branch_id=company_branch_id,
                is_active=True,
                skip=0,
                limit=1000  # Limit for safety
            )
            
            if not jobs:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No job requirements found to export"
                )
            
            # Convert to response models
            job_responses = [JobRequirementService._to_response(job) for job in jobs]
            
            # Format the data
            export_data = {
                "metadata": {
                    "exported_at": datetime.now().isoformat(),
                    "total_jobs": total,
                    "format": format,
                    "user_id": user_id,
                    "company_branch_id": company_branch_id
                },
                "jobs": [job.dict() for job in job_responses]
            }
            
            # Format specific processing
            if format == "csv":
                import csv
                import io
                
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=[
                    "id", "title", "experience_level", "programming_languages", 
                    "skills_required", "salary_min", "salary_max", "is_open",
                    "created_at", "updated_at"
                ])
                
                writer.writeheader()
                for job in job_responses:
                    writer.writerow({
                        "id": job.id,
                        "title": job.title,
                        "experience_level": job.experience_level,
                        "programming_languages": ", ".join(job.programming_languages),
                        "skills_required": ", ".join(job.skills_required),
                        "salary_min": job.salary_min or "",
                        "salary_max": job.salary_max or "",
                        "is_open": job.is_open,
                        "created_at": job.created_at.isoformat(),
                        "updated_at": job.updated_at.isoformat()
                    })
                
                content = output.getvalue()
                content_type = "text/csv"
                
            elif format == "excel":
                try:
                    import pandas as pd
                    
                    # Create DataFrame
                    data = []
                    for job in job_responses:
                        data.append({
                            "ID": job.id,
                            "Title": job.title,
                            "Experience Level": job.experience_level,
                            "Programming Languages": ", ".join(job.programming_languages),
                            "Skills": ", ".join(job.skills_required),
                            "Salary Min": job.salary_min,
                            "Salary Max": job.salary_max,
                            "Open": job.is_open,
                            "Created At": job.created_at,
                            "Updated At": job.updated_at
                        })
                    
                    df = pd.DataFrame(data)
                    
                    # Create Excel file in memory
                    import io
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        df.to_excel(writer, sheet_name='Job Requirements', index=False)
                    
                    content = output.getvalue()
                    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    
                except ImportError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Excel export requires pandas and openpyxl packages"
                    )
            else:  # json
                import json
                content = json.dumps(export_data, default=str, indent=2)
                content_type = "application/json"
            
            record_business_metric(
                "job_requirements_exported",
                value=len(jobs),
                tags={"format": format, "user_id": user_id or "anonymous"}
            )
            
            return {
                "content": content,
                "content_type": content_type,
                "filename": f"job_requirements_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format}",
                "total_jobs": len(jobs)
            }
            
        except HTTPException:
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error exporting job requirements: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to export job requirements"
            )
        finally:
            end_trace(trace, success=True)
    
    # ==================== HELPER METHODS ====================
    
    @staticmethod
    def _to_response(job: JobRequirement) -> JobRequirementResponse:
        """Convert JobRequirement model to response schema"""
        return JobRequirementResponse(
            id=str(job.id),
            user_id=str(job.user_id),
            company_branch_id=str(job.company_branch_id),
            title=job.title,
            programming_languages=job.programming_languages,
            skills_required=job.skills_required,
            experience_level=job.experience_level,
            description=job.description,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            expiration_time=job.expiration_time,
            is_open=job.is_open,
            created_at=job.created_at,
            updated_at=job.updated_at
        )
    
    @staticmethod
    async def _validate_user_company_access(user_id: str, company_branch_id: str) -> bool:
        """Validate that user has access to the company branch"""
        # Implement based on your User-Company relationship model
        # This is a placeholder - implement according to your business logic
        try:
            from app.repositories.company_repository import CompanyRepository
            return await CompanyRepository.validate_user_access(
                user_id=user_id,
                company_branch_id=company_branch_id
            )
        except ImportError:
            # For now, assume validation passes (for development)
            logger.warning("CompanyRepository not available, skipping company access validation")
            return True
    
    @staticmethod
    async def _is_admin(user_id: str) -> bool:
        """Check if user is admin (placeholder implementation)"""
        try:
            from app.models.user import User
            user = await User.get(ObjectId(user_id))
            return user and user.is_superuser
        except Exception:
            return False
    
    @staticmethod
    async def _get_popular_skills(user_id: Optional[str] = None) -> List[str]:
        """Get most popular skills from job requirements"""
        try:
            # Get jobs
            jobs, _ = await JobRequirementRepository.list_job_requirements(
                user_id=user_id,
                is_active=True,
                is_open=True,
                skip=0,
                limit=100  # Get more for better statistics
            )
            
            from collections import Counter
            all_skills = []
            for job in jobs:
                all_skills.extend(job.skills_required)
            
            skill_counter = Counter(all_skills)
            
            # Return top 10 skills
            popular_skills = [skill for skill, _ in skill_counter.most_common(10)]
            
            # Cache popular skills
            if is_redis_available():
                try:
                    redis_client = get_redis()
                    import json
                    cache_key = f"popular_skills:user:{user_id or 'all'}"
                    await redis_client.setex(
                        cache_key,
                        300,  # 5 minutes
                        json.dumps(popular_skills)
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache popular skills: {e}")
            
            return popular_skills
            
        except Exception as e:
            logger.error(f"Error getting popular skills: {e}")
            return []
    
    @staticmethod
    async def _get_popular_languages(user_id: Optional[str] = None) -> List[str]:
        """Get most popular programming languages from job requirements"""
        try:
            # Get jobs
            jobs, _ = await JobRequirementRepository.list_job_requirements(
                user_id=user_id,
                is_active=True,
                is_open=True,
                skip=0,
                limit=100
            )
            
            from collections import Counter
            all_languages = []
            for job in jobs:
                all_languages.extend(job.programming_languages)
            
            language_counter = Counter(all_languages)
            
            # Return top 10 languages
            popular_languages = [lang for lang, _ in language_counter.most_common(10)]
            
            # Cache popular languages
            if is_redis_available():
                try:
                    redis_client = get_redis()
                    import json
                    cache_key = f"popular_languages:user:{user_id or 'all'}"
                    await redis_client.setex(
                        cache_key,
                        300,  # 5 minutes
                        json.dumps(popular_languages)
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache popular languages: {e}")
            
            return popular_languages
            
        except Exception as e:
            logger.error(f"Error getting popular languages: {e}")
            return []
    
    @staticmethod
    async def _invalidate_related_caches(job: JobRequirement) -> None:
        """Invalidate all caches related to a job"""
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            # Patterns to delete
            patterns = [
                f"job_req:{job.id}",  # Single job cache
                f"job_req_list:user:{job.user_id}:*",  # User's job lists
                f"job_req_list:branch:{job.company_branch_id}:*",  # Branch job lists
                "job_req_list:user:all:*",  # Global job lists
                f"job_stats:user:{job.user_id}",  # User stats
                "job_stats:user:all",  # Global stats
                "popular_skills:*",  # Popular skills cache
                "popular_languages:*",  # Popular languages cache
            ]
            
            # Delete all matching keys
            import asyncio
            delete_tasks = []
            for pattern in patterns:
                # Get all keys matching the pattern
                keys = await redis_client.keys(pattern)
                if keys:
                    delete_tasks.append(redis_client.delete(*keys))
            
            if delete_tasks:
                await asyncio.gather(*delete_tasks, return_exceptions=True)
                logger.debug(f"Invalidated caches for job: {job.id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating caches for job {job.id}: {e}")
    
    @staticmethod
    async def _clear_all_job_caches(job_id: str, user_id: str) -> None:
        """Clear all caches for a specific job"""
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            # Get job to get company_branch_id
            job = await JobRequirementRepository.get_job_requirement(job_id)
            if not job:
                return
            
            await JobRequirementService._invalidate_related_caches(job)
            
        except Exception as e:
            logger.warning(f"Error clearing all caches for job {job_id}: {e}")
    
    @staticmethod
    async def cleanup_expired_jobs() -> Dict[str, Any]:
        """Clean up expired job requirements (cron job)"""
        trace = start_trace("cleanup_expired_jobs")
        
        try:
            from datetime import datetime
            
            # Find expired but still open jobs
            query = {
                "expiration_time": {"$lt": datetime.now()},
                "is_open": True,
                "is_active": True
            }
            
            expired_jobs = await JobRequirement.find(query).to_list()
            
            if not expired_jobs:
                return {
                    "processed": 0,
                    "closed": 0,
                    "timestamp": datetime.now().isoformat()
                }
            
            closed_count = 0
            for job in expired_jobs:
                try:
                    # Auto-close expired jobs
                    job.is_open = False
                    await job.save()
                    closed_count += 1
                    
                    # Invalidate cache
                    await JobRequirementService._invalidate_related_caches(job)
                    
                    logger.info(f"Auto-closed expired job: {job.id} - {job.title}")
                    
                except Exception as e:
                    logger.error(f"Error closing expired job {job.id}: {e}")
            
            # Record metric
            record_business_metric(
                "expired_jobs_cleaned",
                value=closed_count,
                tags={"total_expired": len(expired_jobs)}
            )
            
            return {
                "processed": len(expired_jobs),
                "closed": closed_count,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error in cleanup_expired_jobs: {e}", exc_info=True)
            return {
                "error": str(e),
                "processed": 0,
                "closed": 0,
                "timestamp": datetime.now().isoformat()
            }
        finally:
            end_trace(trace, success=True)