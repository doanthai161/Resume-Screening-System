from typing import List, Optional, Dict, Any
from datetime import datetime
import hashlib
import json
import logging
from fastapi import HTTPException, status
from app.core.errors import CustomError, ErrorCodes
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repositories.job_requirement_repository import JobRequirementRepository
from app.schemas.job_requirement import (
    JobRequirementCreate, 
    JobRequirementUpdate, 
    JobRequirementResponse,
    JobRequirementListResponse
)
from app.models.job_requirement import JobRequirement, JobStatus
from app.core.monitoring import (
    monitor_service_call, 
    record_business_metric,
    start_trace,
    end_trace
)
from app.core import cache as cache_backend
from app.utils.time import ensure_utc, now_utc

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
            if not await JobRequirementService._validate_user_company_access(
                user_id, job_data.company_branch_id
            ):
                raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    "User does not have access to this company branch",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            if job_data.expiration_time and ensure_utc(job_data.expiration_time) < now_utc():
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Expiration time cannot be in the past",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            if (job_data.salary_min and job_data.salary_max and 
                job_data.salary_min > job_data.salary_max):
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Minimum salary cannot be greater than maximum salary",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            if not job_data.programming_languages:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "At least one programming language is required",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            if not job_data.skills_required:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "At least one skill is required",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                job = await JobRequirementRepository.create_job_requirement(job_data)
            except DuplicateKeyError:
                raise CustomError(
                    ErrorCodes.CONFLICT,
                    "A similar job requirement already exists",
                    status_code=status.HTTP_409_CONFLICT
                )
            except ValueError as e:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    str(e),
                    status_code=status.HTTP_400_BAD_REQUEST
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
            
        except (HTTPException, CustomError):
            end_trace(trace, success=False)
            raise
        except ValueError as e:
            end_trace(trace, success=False)
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from e
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error creating job requirement: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to create job requirement",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
            cache_key = cache_backend.cache_key("job", job_id)
            cached_data = await cache_backend.get_json(cache_key)
            if cached_data:
                response = JobRequirementResponse(**cached_data)
                if user_id and response.user_id != user_id and not await JobRequirementService._validate_user_company_access(
                    user_id, response.company_branch_id
                ):
                    raise CustomError(
                        ErrorCodes.FORBIDDEN,
                        "Access denied",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )
                record_business_metric("cache_hit", tags={"type": "job_requirement"})
                return response
            
            record_business_metric("cache_miss", tags={"type": "job_requirement"})

            job = await JobRequirementRepository.get_job_requirement(job_id)
            if not job:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Job requirement not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            if user_id and str(job.user_id) != user_id and not await JobRequirementService._validate_user_company_access(
                user_id, str(job.company_branch_id)
            ):
                raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    "Access denied",
                    status_code=status.HTTP_403_FORBIDDEN,
                )
            
            await cache_backend.set_json(
                cache_key,
                JobRequirementService._to_response(job).model_dump(mode="json"),
                3600,
            )
            
            return JobRequirementService._to_response(job)
            
        except (HTTPException, CustomError):
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error getting job requirement {job_id}: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to get job requirement",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Job requirement not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            
            # Check ownership
            if str(job.user_id) != user_id:
                raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    "Only the creator can update this job requirement",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            
            # Check if job is still active
            if not job.is_active:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Cannot update an inactive job requirement",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate updates
            if update_data.expiration_time and ensure_utc(update_data.expiration_time) < now_utc():
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Expiration time cannot be in the past",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            salary_min = (
                update_data.salary_min if "salary_min" in update_data.model_fields_set else job.salary_min
            )
            salary_max = (
                update_data.salary_max if "salary_max" in update_data.model_fields_set else job.salary_max
            )
            salary_currency = (
                update_data.salary_currency
                if "salary_currency" in update_data.model_fields_set
                else job.salary_currency
            )
            if salary_min is not None and salary_max is not None and salary_min > salary_max:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Minimum salary cannot be greater than maximum salary",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            if (salary_min is not None or salary_max is not None) and not salary_currency:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Salary currency is required when salary is provided",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            
            # Update the job
            updated_job = await JobRequirementRepository.update_job_requirement(
                job_id, update_data
            )
            
            if not updated_job:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Job requirement not found after update",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            
            # Record business metric
            record_business_metric(
                "job_requirement_updated",
                tags={"user_id": user_id, "job_id": job_id}
            )
            
            # Invalidate related caches
            await JobRequirementService._invalidate_related_caches(updated_job)
            
            return JobRequirementService._to_response(updated_job)
            
        except (HTTPException, CustomError):
            end_trace(trace, success=False)
            raise
        except ValueError as e:
            end_trace(trace, success=False)
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                str(e),
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from e
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error updating job requirement {job_id}: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to update job requirement",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Job requirement not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            
            # Check ownership
            if str(job.user_id) != user_id:
                raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    "Only the creator can delete this job requirement",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            
            if hard_delete:
                # Hard delete (admin only - implement admin check in production)
                if not await JobRequirementService._is_admin(user_id):
                    raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    "Hard delete requires admin privileges",
                    status_code=status.HTTP_403_FORBIDDEN
                )
                
                # Actually delete from database
                await job.delete()
                action = "hard_deleted"
                
                # The document is already deleted, so invalidate from the
                # in-memory copy instead of trying to load it again.
                await JobRequirementService._invalidate_related_caches(job)
            else:
                # Soft delete
                success = await JobRequirementRepository.delete_job_requirement(job_id)
                if not success:
                    raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Job requirement not found",
                    status_code=status.HTTP_404_NOT_FOUND
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
            
        except (HTTPException, CustomError):
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error deleting job requirement {job_id}: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to delete job requirement",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
            cache_key = cache_backend.cache_key(
                "job-list",
                user_id or "all",
                company_branch_id or "all",
                is_open if is_open is not None else "all",
                is_active,
                page,
                size,
                sort_by,
                sort_order,
            )
            
            # Try cache first
            cached_data = await cache_backend.get_json(cache_key)
            if cached_data:
                record_business_metric("cache_hit", tags={"type": "job_list"})
                return JobRequirementListResponse(**cached_data)
            
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
                skip=skip,
                limit=size
            )

            # Cache the result
            await cache_backend.set_json(cache_key, response.model_dump(mode="json"), 300)
            
            record_business_metric(
                "job_requirement_listed",
                value=len(job_responses),
                tags={"user_id": user_id or "anonymous", "page": page}
            )
            
            return response
            
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error listing job requirements: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to list job requirements",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
        skip: int = 0,
        limit: int = 20
    ) -> JobRequirementListResponse:
        """Search job requirements with filters"""
        trace = start_trace("search_job_requirements")

        try:
            if skip < 0:
                skip = 0
            if limit < 1 or limit > 100:
                limit = 20

            search_payload = {
                "q": (search_term or "").strip().lower(),
                "languages": sorted(programming_languages or []),
                "skills": sorted(skills or []),
                "experience": experience_level,
                "skip": skip,
                "limit": limit,
            }
            search_digest = hashlib.sha256(
                json.dumps(search_payload, sort_keys=True).encode("utf-8")
            ).hexdigest()
            search_cache_key = cache_backend.cache_key("job-search", search_digest)
            cached_search = await cache_backend.get_json(search_cache_key)
            if cached_search:
                record_business_metric("cache_hit", tags={"type": "job_search"})
                return JobRequirementListResponse(**cached_search)

            # Search jobs
            jobs, total = await JobRequirementRepository.search_job_requirements(
                search_term=search_term or "",
                programming_languages=programming_languages,
                skills=skills,
                experience_level=experience_level,
                skip=skip,
                limit=limit
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

            response = JobRequirementListResponse(
                job_requirements=job_responses,
                total=total,
                skip=skip,
                limit=limit
            )
            await cache_backend.set_json(
                search_cache_key,
                response.model_dump(mode="json"),
                120,
            )
            return response

        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error searching job requirements: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to search job requirements",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
            cache_key = cache_backend.cache_key("job-stats", user_id or "all")

            cached = await cache_backend.get_json(cache_key)
            if cached:
                record_business_metric("cache_hit", tags={"type": "job_stats"})
                return cached
            
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
            await cache_backend.set_json(cache_key, stats, 60)
            
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
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "No job IDs provided",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            if is_open is None and is_active is None:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "At least one status field (is_open or is_active) must be provided",
                    status_code=status.HTTP_400_BAD_REQUEST
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
            
        except (HTTPException, CustomError):
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error in bulk update: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to bulk update job statuses",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "No job requirements found to export",
                    status_code=status.HTTP_404_NOT_FOUND
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
                    raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Excel export requires pandas and openpyxl packages",
                    status_code=status.HTTP_400_BAD_REQUEST
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
            
        except (HTTPException, CustomError):
            end_trace(trace, success=False)
            raise
        except Exception as e:
            end_trace(trace, success=False)
            logger.error(f"Error exporting job requirements: {e}", exc_info=True)
            raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Failed to export job requirements",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
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
            employment_type=job.employment_type,
            work_mode=job.work_mode,
            location=job.location,
            number_of_openings=job.number_of_openings,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            expiration_time=job.expiration_time,
            status=job.status,
            is_open=job.is_open,
            is_active=job.is_active,
            published_at=job.published_at,
            closed_at=job.closed_at,
            version=job.version,
            created_at=job.created_at,
            updated_at=job.updated_at
        )
    
    @staticmethod
    async def _validate_user_company_access(user_id: str, company_branch_id: str) -> bool:
        """Validate branch access from source-of-truth records (never cached)."""
        try:
            from app.models.company import Company
            from app.models.company_branch import CompanyBranch
            from app.models.user import User
            from app.models.user_company import UserCompany

            if not ObjectId.is_valid(user_id) or not ObjectId.is_valid(company_branch_id):
                return False
            user = await User.get(ObjectId(user_id))
            branch = await CompanyBranch.get(ObjectId(company_branch_id))
            if not user or not user.is_active or not branch or not branch.is_active:
                return False
            if user.is_superuser:
                return True
            company = await Company.get(branch.company_id)
            if not company or not company.is_active:
                return False
            if str(company.user_id) == user_id:
                return True
            now = now_utc()
            return bool(
                await UserCompany.find_one(
                    {
                        "user_id": user.id,
                        "company_branch_id": branch.id,
                        "is_active": True,
                        "$and": [
                            {"$or": [{"start_date": None}, {"start_date": {"$lte": now}}]},
                            {"$or": [{"end_date": None}, {"end_date": {"$gte": now}}]},
                        ],
                    }
                )
            )
        except Exception:
            logger.warning("Could not validate company branch access", exc_info=True)
            return False
    
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
            
            return popular_languages
            
        except Exception as e:
            logger.error(f"Error getting popular languages: {e}")
            return []
    
    @staticmethod
    async def _invalidate_related_caches(job: JobRequirement) -> None:
        """Invalidate all caches related to a job"""
        try:
            # Patterns to delete
            patterns = [
                cache_backend.cache_key("job", job.id),
                cache_backend.cache_key("job-list", "*"),
                cache_backend.cache_key("job-stats", job.user_id),
                cache_backend.cache_key("job-stats", "all"),
                cache_backend.cache_key("job-search", "*"),
            ]
            
            for pattern in patterns:
                await cache_backend.delete_pattern(pattern)
            logger.debug(f"Invalidated caches for job: {job.id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating caches for job {job.id}: {e}")
    
    @staticmethod
    async def cleanup_expired_jobs() -> Dict[str, Any]:
        """Clean up expired job requirements (cron job)"""
        trace = start_trace("cleanup_expired_jobs")
        
        try:
            from datetime import datetime
            
            # Find expired but still open jobs
            query = {
                "expiration_time": {"$lt": now_utc()},
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
                    job.status = JobStatus.CLOSED
                    job.closed_at = now_utc()
                    job.updated_at = now_utc()
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
