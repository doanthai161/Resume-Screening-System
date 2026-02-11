from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import logging
from app.models.job_requirement import JobRequirement
from app.schemas.job_requirement import JobRequirementCreate, JobRequirementUpdate
from app.core.redis import get_redis, is_redis_available
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_utc

logger = logging.getLogger(__name__)


class JobRequirementRepository:
    CACHE_PREFIX = "job_req:"
    SINGLE_CACHE_TTL = 3600
    LIST_CACHE_TTL = 300
    STATS_CACHE_TTL = 60
    
    @staticmethod
    def _get_single_cache_key(job_id: str) -> str:
        return f"{JobRequirementRepository.CACHE_PREFIX}single:{job_id}"
    
    @staticmethod
    def _get_list_cache_key(
        user_id: Optional[str] = None,
        company_branch_id: Optional[str] = None,
        is_open: Optional[bool] = None,
        is_active: Optional[bool] = True,
        page: int = 1,
        size: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> str:
        key_parts = [JobRequirementRepository.CACHE_PREFIX, "list"]
        
        if user_id:
            key_parts.append(f"user:{user_id}")
        else:
            key_parts.append("user:all")
        
        if company_branch_id:
            key_parts.append(f"branch:{company_branch_id}")
        else:
            key_parts.append("branch:all")
        
        if is_open is not None:
            key_parts.append(f"open:{is_open}")
        else:
            key_parts.append("open:all")
        
        key_parts.append(f"active:{is_active}")
        key_parts.append(f"page:{page}")
        key_parts.append(f"size:{size}")
        key_parts.append(f"sort:{sort_by}:{sort_order}")
        
        return ":".join(key_parts)
    
    @staticmethod
    def _get_stats_cache_key(user_id: Optional[str] = None) -> str:
        return f"{JobRequirementRepository.CACHE_PREFIX}stats:user:{user_id or 'all'}"
    
    @staticmethod
    def _get_popular_skills_cache_key(user_id: Optional[str] = None) -> str:
        return f"{JobRequirementRepository.CACHE_PREFIX}popular_skills:user:{user_id or 'all'}"
    
    @staticmethod
    def _get_popular_languages_cache_key(user_id: Optional[str] = None) -> str:
        return f"{JobRequirementRepository.CACHE_PREFIX}popular_languages:user:{user_id or 'all'}"
    
    @staticmethod
    @monitor_db_operation("job_req_create")
    async def create_job_requirement(job_data: JobRequirementCreate) -> JobRequirement:
        try:
            job_dict = job_data.dict()
            job_dict["user_id"] = ObjectId(job_dict["user_id"])
            job_dict["company_branch_id"] = ObjectId(job_dict["company_branch_id"])
            
            current_time = now_utc()
            job_dict["created_at"] = current_time
            job_dict["updated_at"] = current_time
            
            job = JobRequirement(**job_dict)
            await job.insert()
            
            logger.info(f"Job requirement created: {job.id} - {job.title}")
            return job
            
        except DuplicateKeyError as e:
            logger.error(f"Duplicate key error creating job requirement: {e}")
            raise ValueError("Job requirement with similar criteria already exists")
        except Exception as e:
            logger.error(f"Error creating job requirement: {e}", exc_info=True)
            raise
    
    @staticmethod
    @monitor_db_operation("job_req_get")
    @monitor_cache_operation("job_req_get")
    async def get_job_requirement(job_id: str) -> Optional[JobRequirement]:
        cache_key = JobRequirementRepository._get_single_cache_key(job_id)
        cached_data = await JobRequirementRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for job requirement: {job_id}")
            job = JobRequirement.parse_obj(cached_data)
            setattr(job, '_from_cache', True)
            return job
        
        try:
            job = await JobRequirement.get(ObjectId(job_id))
            if job:
                await JobRequirementRepository._set_cache(
                    cache_key, 
                    job.dict(), 
                    JobRequirementRepository.SINGLE_CACHE_TTL
                )
                logger.debug(f"Cache set for job requirement: {job_id}")
            return job
        except Exception as e:
            logger.error(f"Error getting job requirement {job_id}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("job_req_update")
    async def update_job_requirement(job_id: str, update_data: JobRequirementUpdate) -> Optional[JobRequirement]:
        try:
            job = await JobRequirement.get(ObjectId(job_id))
            if not job:
                return None
            
            update_dict = update_data.dict(exclude_unset=True)
            for field, value in update_dict.items():
                setattr(job, field, value)
            
            job.updated_at = now_utc()
            await job.save()
            
            cache_key = JobRequirementRepository._get_single_cache_key(job_id)
            await JobRequirementRepository._delete_cache(cache_key)
            
            logger.info(f"Job requirement updated: {job_id}")
            return job
            
        except Exception as e:
            logger.error(f"Error updating job requirement {job_id}: {e}", exc_info=True)
            raise
    
    @staticmethod
    @monitor_db_operation("job_req_delete")
    async def delete_job_requirement(job_id: str) -> bool:
        try:
            job = await JobRequirement.get(ObjectId(job_id))
            if not job:
                return False
            
            job.is_active = False
            job.is_open = False
            job.updated_at = now_utc()
            await job.save()
            
            cache_key = JobRequirementRepository._get_single_cache_key(job_id)
            await JobRequirementRepository._delete_cache(cache_key)
            
            logger.info(f"Job requirement soft deleted: {job_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting job requirement {job_id}: {e}", exc_info=True)
            return False
    
    @staticmethod
    @monitor_db_operation("job_req_hard_delete")
    async def hard_delete_job_requirement(job_id: str) -> bool:
        try:
            job = await JobRequirement.get(ObjectId(job_id))
            if not job:
                return False
            
            await job.delete()
            
            cache_key = JobRequirementRepository._get_single_cache_key(job_id)
            await JobRequirementRepository._delete_cache(cache_key)
            
            logger.info(f"Job requirement hard deleted: {job_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error hard deleting job requirement {job_id}: {e}", exc_info=True)
            return False
    
    @staticmethod
    @monitor_db_operation("job_req_list")
    @monitor_cache_operation("job_req_list")
    async def list_job_requirements(
        user_id: Optional[str] = None,
        company_branch_id: Optional[str] = None,
        is_open: Optional[bool] = None,
        is_active: Optional[bool] = True,
        skip: int = 0,
        limit: int = 20,
        sort_by: str = "created_at",
        sort_order: int = -1
    ) -> Tuple[List[JobRequirement], int]:
        page = (skip // limit) + 1 if limit > 0 else 1
        sort_order_str = "desc" if sort_order == -1 else "asc"
        
        cache_key = JobRequirementRepository._get_list_cache_key(
            user_id=user_id,
            company_branch_id=company_branch_id,
            is_open=is_open,
            is_active=is_active,
            page=page,
            size=limit,
            sort_by=sort_by,
            sort_order=sort_order_str
        )
        
        cached_data = await JobRequirementRepository._get_from_cache(cache_key)
        if cached_data:
            logger.debug(f"Cache hit for job requirements list: {cache_key}")
            jobs = [JobRequirement.parse_obj(item) for item in cached_data.get("jobs", [])]
            total = cached_data.get("total", 0)
            
            for job in jobs:
                setattr(job, '_from_cache', True)
                setattr(job, '_cache_key', cache_key)
            
            return jobs, total
        
        # Build query
        query = {"is_active": is_active}
        if user_id:
            query["user_id"] = ObjectId(user_id)
        if company_branch_id:
            query["company_branch_id"] = ObjectId(company_branch_id)
        if is_open is not None:
            query["is_open"] = is_open
        
        # Execute query
        cursor = JobRequirement.find(query)
        
        # Get total count
        total = await cursor.count()
        
        # Apply sorting and pagination
        jobs = await cursor.sort([(sort_by, sort_order)]) \
                          .skip(skip) \
                          .limit(limit) \
                          .to_list()
        
        # Cache the result
        if jobs:
            cache_data = {
                "jobs": [job.dict() for job in jobs],
                "total": total,
                "cached_at": datetime.now().isoformat()
            }
            await JobRequirementRepository._set_cache(
                cache_key, 
                cache_data, 
                JobRequirementRepository.LIST_CACHE_TTL
            )
            logger.debug(f"Cache set for job requirements list: {cache_key}")
        
        return jobs, total
    
    @staticmethod
    @monitor_db_operation("job_req_search")
    async def search_job_requirements(
        search_term: str,
        programming_languages: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        experience_level: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[JobRequirement], int]:
        """Search job requirements with advanced filtering"""
        try:
            # Build search query
            query = {
                "is_active": True,
                "is_open": True,
            }
            
            # Text search
            if search_term:
                query["$or"] = [
                    {"title": {"$regex": search_term, "$options": "i"}},
                    {"description": {"$regex": search_term, "$options": "i"}},
                ]
            
            # Filter by programming languages
            if programming_languages:
                query["programming_languages"] = {"$in": programming_languages}
            
            # Filter by skills
            if skills:
                query["skills_required"] = {"$in": skills}
            
            # Filter by experience level
            if experience_level:
                query["experience_level"] = experience_level
            
            cursor = JobRequirement.find(query)
            total = await cursor.count()
            
            jobs = await cursor.sort([("created_at", -1)]) \
                              .skip(skip) \
                              .limit(limit) \
                              .to_list()
            
            return jobs, total
            
        except Exception as e:
            logger.error(f"Error searching job requirements: {e}", exc_info=True)
            return [], 0
    
    @staticmethod
    @monitor_db_operation("job_req_stats")
    @monitor_cache_operation("job_req_stats")
    async def get_job_statistics(user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get statistics about job requirements"""
        # Try cache first
        cache_key = JobRequirementRepository._get_stats_cache_key(user_id)
        cached_data = await JobRequirementRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for job statistics: {cache_key}")
            cached_data["_from_cache"] = True
            return cached_data
        
        try:
            # Build base query
            base_query = {}
            if user_id:
                base_query["user_id"] = ObjectId(user_id)
            
            # Get counts
            total_jobs = await JobRequirement.find(base_query).count()
            
            active_query = {**base_query, "is_active": True, "is_open": True}
            active_jobs = await JobRequirement.find(active_query).count()
            
            closed_query = {**base_query, "is_active": True, "is_open": False}
            closed_jobs = await JobRequirement.find(closed_query).count()
            
            expired_query = {
                **base_query,
                "is_active": True,
                "is_open": True,
                "expiration_time": {"$lt": now_utc()}
            }
            expired_jobs = await JobRequirement.find(expired_query).count()
            
            # Get recent jobs
            recent_jobs = await JobRequirement.find({**base_query, "is_active": True}) \
                .sort([("created_at", -1)]) \
                .limit(5) \
                .to_list()
            
            # Calculate average salary
            pipeline = [
                {"$match": {**base_query, "is_active": True, "salary_min": {"$ne": None}, "salary_max": {"$ne": None}}},
                {"$group": {
                    "_id": None,
                    "avg_min": {"$avg": "$salary_min"},
                    "avg_max": {"$avg": "$salary_max"},
                    "count": {"$sum": 1}
                }}
            ]
            
            salary_stats = await JobRequirement.aggregate(pipeline).to_list()
            avg_min = salary_stats[0]["avg_min"] if salary_stats else 0
            avg_max = salary_stats[0]["avg_max"] if salary_stats else 0
            
            stats = {
                "total_jobs": total_jobs,
                "active_jobs": active_jobs,
                "closed_jobs": closed_jobs,
                "expired_jobs": expired_jobs,
                "recent_jobs_count": len(recent_jobs),
                "salary_stats": {
                    "average_min": round(float(avg_min), 2),
                    "average_max": round(float(avg_max), 2),
                    "jobs_with_salary": salary_stats[0]["count"] if salary_stats else 0
                },
                "calculated_at": datetime.now().isoformat()
            }
            
            # Cache the result
            await JobRequirementRepository._set_cache(
                cache_key, 
                stats, 
                JobRequirementRepository.STATS_CACHE_TTL
            )
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting job statistics: {e}", exc_info=True)
            return {
                "total_jobs": 0,
                "active_jobs": 0,
                "closed_jobs": 0,
                "expired_jobs": 0,
                "recent_jobs_count": 0,
                "salary_stats": {"average_min": 0, "average_max": 0, "jobs_with_salary": 0},
                "calculated_at": datetime.now().isoformat(),
                "error": str(e)
            }
    
    @staticmethod
    @monitor_db_operation("job_req_active_count")
    async def get_active_job_count(user_id: Optional[str] = None) -> int:
        """Get count of active job requirements"""
        try:
            query = {"is_active": True, "is_open": True}
            if user_id:
                query["user_id"] = ObjectId(user_id)
            
            return await JobRequirement.find(query).count()
        except Exception as e:
            logger.error(f"Error getting active job count: {e}")
            return 0
    
    @staticmethod
    @monitor_db_operation("job_req_popular_skills")
    @monitor_cache_operation("job_req_popular_skills")
    async def get_popular_skills(
        user_id: Optional[str] = None, 
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get most popular skills from job requirements"""
        # Try cache first
        cache_key = JobRequirementRepository._get_popular_skills_cache_key(user_id)
        cached_data = await JobRequirementRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for popular skills: {cache_key}")
            cached_data["_from_cache"] = True
            return cached_data
        
        try:
            # Build base match
            match_stage = {"is_active": True, "is_open": True}
            if user_id:
                match_stage["user_id"] = ObjectId(user_id)
            
            # Aggregation pipeline for popular skills
            pipeline = [
                {"$match": match_stage},
                {"$unwind": "$skills_required"},
                {"$group": {
                    "_id": "$skills_required",
                    "count": {"$sum": 1},
                    "jobs": {"$addToSet": "$_id"}
                }},
                {"$sort": {"count": -1}},
                {"$limit": limit},
                {"$project": {
                    "skill": "$_id",
                    "count": 1,
                    "job_count": {"$size": "$jobs"},
                    "_id": 0
                }}
            ]
            
            results = await JobRequirement.aggregate(pipeline).to_list()
            
            # Cache the result
            await JobRequirementRepository._set_cache(
                cache_key, 
                results, 
                JobRequirementRepository.STATS_CACHE_TTL
            )
            
            return results
            
        except Exception as e:
            logger.error(f"Error getting popular skills: {e}", exc_info=True)
            return []
    
    @staticmethod
    @monitor_db_operation("job_req_popular_languages")
    @monitor_cache_operation("job_req_popular_languages")
    async def get_popular_programming_languages(
        user_id: Optional[str] = None, 
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get most popular programming languages from job requirements"""
        # Try cache first
        cache_key = JobRequirementRepository._get_popular_languages_cache_key(user_id)
        cached_data = await JobRequirementRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for popular languages: {cache_key}")
            cached_data["_from_cache"] = True
            return cached_data
        
        try:
            # Build base match
            match_stage = {"is_active": True, "is_open": True}
            if user_id:
                match_stage["user_id"] = ObjectId(user_id)
            
            # Aggregation pipeline for popular languages
            pipeline = [
                {"$match": match_stage},
                {"$unwind": "$programming_languages"},
                {"$group": {
                    "_id": "$programming_languages",
                    "count": {"$sum": 1},
                    "jobs": {"$addToSet": "$_id"}
                }},
                {"$sort": {"count": -1}},
                {"$limit": limit},
                {"$project": {
                    "language": "$_id",
                    "count": 1,
                    "job_count": {"$size": "$jobs"},
                    "_id": 0
                }}
            ]
            
            results = await JobRequirement.aggregate(pipeline).to_list()
            
            # Cache the result
            await JobRequirementRepository._set_cache(
                cache_key, 
                results, 
                JobRequirementRepository.STATS_CACHE_TTL
            )
            
            return results
            
        except Exception as e:
            logger.error(f"Error getting popular languages: {e}", exc_info=True)
            return []
    
    # ==================== BULK OPERATIONS ====================
    
    @staticmethod
    @monitor_db_operation("job_req_bulk_update")
    async def bulk_update_status(
        job_ids: List[str],
        update_data: Dict[str, Any]
    ) -> Tuple[int, List[str]]:
        """Bulk update job requirement statuses"""
        try:
            if not job_ids:
                return 0, []
            
            # Convert job IDs to ObjectId
            object_ids = [ObjectId(job_id) for job_id in job_ids]
            
            # Prepare update
            update_dict = {**update_data, "updated_at": now_utc()}
            
            # Perform bulk update
            result = await JobRequirement.find({"_id": {"$in": object_ids}}) \
                .update_many({"$set": update_dict})
            
            # Invalidate cache for updated jobs
            cache_keys = [JobRequirementRepository._get_single_cache_key(job_id) for job_id in job_ids]
            await JobRequirementRepository._delete_cache_many(cache_keys)
            
            logger.info(f"Bulk updated {result.modified_count} job requirements")
            return result.modified_count, job_ids
            
        except Exception as e:
            logger.error(f"Error in bulk update: {e}", exc_info=True)
            return 0, []
    
    @staticmethod
    @monitor_db_operation("job_req_find_expired")
    async def find_expired_jobs() -> List[JobRequirement]:
        """Find expired but still open job requirements"""
        try:
            expired_jobs = await JobRequirement.find({
                "expiration_time": {"$lt": now_utc()},
                "is_open": True,
                "is_active": True
            }).to_list()
            
            return expired_jobs
            
        except Exception as e:
            logger.error(f"Error finding expired jobs: {e}", exc_info=True)
            return []
    
    # ==================== CACHE HELPER METHODS ====================
    
    @staticmethod
    async def _get_from_cache(key: str) -> Optional[Any]:
        """Get data from Redis cache"""
        if not is_redis_available():
            return None
        
        try:
            redis_client = get_redis()
            import json
            cached = await redis_client.get(key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Cache get error for key {key}: {e}")
        return None
    
    @staticmethod
    async def _set_cache(key: str, data: Any, ttl: Optional[int] = None) -> None:
        """Set data in Redis cache"""
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            import json
            await redis_client.setex(key, ttl or JobRequirementRepository.SINGLE_CACHE_TTL, 
                                   json.dumps(data, default=str))
        except Exception as e:
            logger.warning(f"Cache set error for key {key}: {e}")
    
    @staticmethod
    async def _delete_cache(key: str) -> None:
        """Delete data from Redis cache"""
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            await redis_client.delete(key)
        except Exception as e:
            logger.warning(f"Cache delete error for key {key}: {e}")
    
    @staticmethod
    async def _delete_cache_many(keys: List[str]) -> None:
        """Delete multiple keys from Redis cache"""
        if not is_redis_available() or not keys:
            return
        
        try:
            redis_client = get_redis()
            await redis_client.delete(*keys)
        except Exception as e:
            logger.warning(f"Cache delete many error: {e}")
    
    @staticmethod
    async def invalidate_user_cache(user_id: str) -> None:
        """Invalidate all cache for a specific user"""
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            # Patterns to delete
            patterns = [
                f"{JobRequirementRepository.CACHE_PREFIX}*user:{user_id}*",
                f"{JobRequirementRepository.CACHE_PREFIX}*user:all*",  # Also invalidate global cache
            ]
            
            # Delete all matching keys
            import asyncio
            delete_tasks = []
            for pattern in patterns:
                keys = await redis_client.keys(pattern)
                if keys:
                    delete_tasks.append(redis_client.delete(*keys))
            
            if delete_tasks:
                await asyncio.gather(*delete_tasks, return_exceptions=True)
                logger.info(f"Invalidated all cache for user: {user_id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating user cache for {user_id}: {e}")
    
    @staticmethod
    async def invalidate_company_cache(company_branch_id: str) -> None:
        """Invalidate all cache for a specific company branch"""
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            # Patterns to delete
            patterns = [
                f"{JobRequirementRepository.CACHE_PREFIX}*branch:{company_branch_id}*",
                f"{JobRequirementRepository.CACHE_PREFIX}*branch:all*",  # Also invalidate global cache
            ]
            
            # Delete all matching keys
            import asyncio
            delete_tasks = []
            for pattern in patterns:
                keys = await redis_client.keys(pattern)
                if keys:
                    delete_tasks.append(redis_client.delete(*keys))
            
            if delete_tasks:
                await asyncio.gather(*delete_tasks, return_exceptions=True)
                logger.info(f"Invalidated all cache for company branch: {company_branch_id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating company cache for {company_branch_id}: {e}")
    
    @staticmethod
    async def clear_all_cache() -> None:
        """Clear all job requirement cache"""
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            pattern = f"{JobRequirementRepository.CACHE_PREFIX}*"
            keys = await redis_client.keys(pattern)
            
            if keys:
                await redis_client.delete(*keys)
                logger.info(f"Cleared all job requirement cache ({len(keys)} keys)")
            
        except Exception as e:
            logger.warning(f"Error clearing all cache: {e}")