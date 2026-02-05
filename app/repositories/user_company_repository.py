from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import logging
from app.models.user_company import UserCompany
from app.models.company_branch import CompanyBranch
from app.models.company import Company
from app.models.user import User
from app.schemas.user_company import (
    AssignUserToCompanyBranch,
    UserCompanyResponse,
    UserCompanyStats
)
from app.core.redis import get_redis, is_redis_available
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_vn

logger = logging.getLogger(__name__)


class UserCompanyRepository:
    CACHE_PREFIX = "user_company:"
    ASSIGNMENT_CACHE_TTL = 3600  
    USER_ASSIGNMENTS_CACHE_TTL = 1800  
    BRANCH_ASSIGNMENTS_CACHE_TTL = 1800  
    
    @staticmethod
    def _get_assignment_cache_key(assignment_id: str) -> str:
        return f"{UserCompanyRepository.CACHE_PREFIX}assignment:{assignment_id}"
    
    @staticmethod
    def _get_user_assignments_cache_key(user_id: str, active_only: bool = True) -> str:
        return f"{UserCompanyRepository.CACHE_PREFIX}user_assignments:{user_id}:{'active' if active_only else 'all'}"
    
    @staticmethod
    def _get_branch_assignments_cache_key(branch_id: str, active_only: bool = True) -> str:
        return f"{UserCompanyRepository.CACHE_PREFIX}branch_assignments:{branch_id}:{'active' if active_only else 'all'}"
    
    @staticmethod
    def _get_user_branch_cache_key(user_id: str, branch_id: str) -> str:
        return f"{UserCompanyRepository.CACHE_PREFIX}user_branch:{user_id}:{branch_id}"
    
    @staticmethod
    def _get_branch_stats_cache_key(branch_id: str) -> str:
        return f"{UserCompanyRepository.CACHE_PREFIX}branch_stats:{branch_id}"
    
    @staticmethod
    @monitor_db_operation("user_company_assign")
    async def assign_user_to_branch(
        user_id: str,
        company_branch_id: str,
        assigned_by: str,
        role: str = "member",
        permissions: Optional[List[str]] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Optional[UserCompany]:
        try:
            existing_assignment = await UserCompany.find_one({
                "user_id": ObjectId(user_id),
                "company_branch_id": ObjectId(company_branch_id),
                "is_active": True
            })
            
            if existing_assignment:
                raise ValueError("User is already assigned to this branch")
            
            inactive_assignment = await UserCompany.find_one({
                "user_id": ObjectId(user_id),
                "company_branch_id": ObjectId(company_branch_id),
                "is_active": False
            })
            
            if inactive_assignment:
                inactive_assignment.is_active = True
                inactive_assignment.role = role
                inactive_assignment.permissions = permissions or []
                inactive_assignment.assigned_at = now_vn()
                inactive_assignment.assigned_by = ObjectId(assigned_by)
                inactive_assignment.unassigned_at = None
                inactive_assignment.unassigned_by = None
                inactive_assignment.updated_at = now_vn()
                await inactive_assignment.save()
                
                assignment = inactive_assignment
                logger.info(f"Reactivated assignment: {assignment.id} for user {user_id} to branch {company_branch_id}")
            else:
                assignment = UserCompany(
                    user_id=ObjectId(user_id),
                    company_branch_id=ObjectId(company_branch_id),
                    role=role,
                    permissions=permissions or [],
                    assigned_by=ObjectId(assigned_by),
                    assigned_at=start_date or now_vn(),
                    start_date=start_date or now_vn(),
                    end_date=end_date,
                    is_active=True,
                    created_at=now_vn(),
                    updated_at=now_vn()
                )
                await assignment.insert()
                logger.info(f"Created new assignment: {assignment.id} for user {user_id} to branch {company_branch_id}")
            
            await UserCompanyRepository._invalidate_assignment_caches(assignment)
            
            return assignment
            
        except ValueError as e:
            raise
        except DuplicateKeyError as e:
            logger.error(f"Duplicate key error assigning user to branch: {e}")
            raise ValueError("Assignment with similar criteria already exists")
        except Exception as e:
            logger.error(f"Error assigning user to branch: {e}", exc_info=True)
            raise
    
    @staticmethod
    @monitor_db_operation("user_company_unassign")
    async def unassign_user_from_branch(
        user_id: str,
        company_branch_id: str,
        unassigned_by: str,
        reason: Optional[str] = None
    ) -> bool:
        try:
            assignment = await UserCompany.find_one({
                "user_id": ObjectId(user_id),
                "company_branch_id": ObjectId(company_branch_id),
                "is_active": True
            })
            
            if not assignment:
                return False
            
            assignment.is_active = False
            assignment.unassigned_at = now_vn()
            assignment.unassigned_by = ObjectId(unassigned_by)
            assignment.unassign_reason = reason
            assignment.updated_at = now_vn()
            await assignment.save()
            
            await UserCompanyRepository._invalidate_assignment_caches(assignment)
            
            logger.info(f"Unassigned user {user_id} from branch {company_branch_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error unassigning user from branch: {e}", exc_info=True)
            return False
    
    @staticmethod
    @monitor_db_operation("user_company_delete")
    async def delete_assignment(
        assignment_id: str,
        deleted_by: str
    ) -> bool:
        try:
            assignment = await UserCompany.get(ObjectId(assignment_id))
            if not assignment:
                return False
            
            audit_data = {
                "deleted_assignment": assignment.dict(),
                "deleted_by": deleted_by,
                "deleted_at": now_vn()
            }
            
            await assignment.delete()
            
            await UserCompanyRepository._invalidate_assignment_caches(assignment)
            
            logger.warning(f"HARD DELETE assignment {assignment_id}: {audit_data}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error deleting assignment {assignment_id}: {e}", exc_info=True)
            return False
        
    @staticmethod
    @monitor_db_operation("user_company_get")
    @monitor_cache_operation("user_company_get")
    async def get_assignment(assignment_id: str) -> Optional[UserCompany]:
        cache_key = UserCompanyRepository._get_assignment_cache_key(assignment_id)
        cached_data = await UserCompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for assignment: {assignment_id}")
            assignment = UserCompany.model_validate(cached_data)
            setattr(assignment, '_from_cache', True)
            return assignment
        
        try:
            assignment = await UserCompany.get(ObjectId(assignment_id))
            if assignment:
                await UserCompanyRepository._set_cache(
                    cache_key,
                    assignment.dict(),
                    UserCompanyRepository.ASSIGNMENT_CACHE_TTL
                )
                logger.debug(f"Cache set for assignment: {assignment_id}")
            return assignment
        except Exception as e:
            logger.error(f"Error getting assignment {assignment_id}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_company_get_user_branch")
    @monitor_cache_operation("user_company_get_user_branch")
    async def get_user_branch_assignment(
        user_id: str,
        company_branch_id: str
    ) -> Optional[UserCompany]:
        cache_key = UserCompanyRepository._get_user_branch_cache_key(user_id, company_branch_id)
        cached_data = await UserCompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user-branch assignment: {user_id}:{company_branch_id}")
            assignment = UserCompany.model_validate(cached_data)
            setattr(assignment, '_from_cache', True)
            return assignment
        
        try:
            assignment = await UserCompany.find_one({
                "user_id": ObjectId(user_id),
                "company_branch_id": ObjectId(company_branch_id),
                "is_active": True
            })
            
            if assignment:
                await UserCompanyRepository._set_cache(
                    cache_key,
                    assignment.dict(),
                    UserCompanyRepository.ASSIGNMENT_CACHE_TTL
                )
                logger.debug(f"Cache set for user-branch assignment: {user_id}:{company_branch_id}")
            
            return assignment
        except Exception as e:
            logger.error(f"Error getting user-branch assignment: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_company_list_user")
    @monitor_cache_operation("user_company_list_user")
    async def list_user_assignments(
        user_id: str,
        active_only: bool = True,
        skip: int = 0,
        limit: int = 100
    ) -> List[UserCompany]:
        cache_key = UserCompanyRepository._get_user_assignments_cache_key(user_id, active_only)
        cached_data = await UserCompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user assignments: {user_id}")
            assignments = [UserCompany.model_validate(item) for item in cached_data]
            for assignment in assignments:
                setattr(assignment, '_from_cache', True)
            return assignments
        
        try:
            query = {"user_id": ObjectId(user_id)}
            if active_only:
                query["is_active"] = True
            
            cursor = UserCompany.find(query).sort([("assigned_at", -1)])
            
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit > 0:
                cursor = cursor.limit(limit)
            
            assignments = await cursor.to_list()
            
            if assignments:
                await UserCompanyRepository._set_cache(
                    cache_key,
                    [assignment.dict() for assignment in assignments],
                    UserCompanyRepository.USER_ASSIGNMENTS_CACHE_TTL
                )
                logger.debug(f"Cache set for user assignments: {user_id}")
            
            return assignments
        except Exception as e:
            logger.error(f"Error listing user assignments: {e}")
            return []
    
    @staticmethod
    @monitor_db_operation("user_company_list_branch")
    @monitor_cache_operation("user_company_list_branch")
    async def list_branch_assignments(
        company_branch_id: str,
        active_only: bool = True,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[UserCompany], int]:
        cache_key = UserCompanyRepository._get_branch_assignments_cache_key(company_branch_id, active_only)
        cached_data = await UserCompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for branch assignments: {company_branch_id}")
            assignments = [UserCompany.model_validate(item) for item in cached_data.get("assignments", [])]
            total = cached_data.get("total", 0)
            for assignment in assignments:
                setattr(assignment, '_from_cache', True)
            return assignments, total
        
        try:
            query = {"company_branch_id": ObjectId(company_branch_id)}
            if active_only:
                query["is_active"] = True
            
            total = await UserCompany.find(query).count()
            
            cursor = UserCompany.find(query).sort([("assigned_at", -1)])
            
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit > 0:
                cursor = cursor.limit(limit)
            
            assignments = await cursor.to_list()
            
            if assignments:
                cache_data = {
                    "assignments": [assignment.dict() for assignment in assignments],
                    "total": total
                }
                await UserCompanyRepository._set_cache(
                    cache_key,
                    cache_data,
                    UserCompanyRepository.BRANCH_ASSIGNMENTS_CACHE_TTL
                )
                logger.debug(f"Cache set for branch assignments: {company_branch_id}")
            
            return assignments, total
        except Exception as e:
            logger.error(f"Error listing branch assignments: {e}")
            return [], 0
    
    @staticmethod
    @monitor_db_operation("user_company_search")
    async def search_assignments(
        company_branch_id: Optional[str] = None,
        user_id: Optional[str] = None,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[UserCompany], int]:
        try:
            query = {}
            
            if company_branch_id:
                query["company_branch_id"] = ObjectId(company_branch_id)
            
            if user_id:
                query["user_id"] = ObjectId(user_id)
            
            if role:
                query["role"] = role
            
            if is_active is not None:
                query["is_active"] = is_active
            
            # Date range filters
            date_filters = {}
            if start_date:
                date_filters["$gte"] = start_date
            if end_date:
                date_filters["$lte"] = end_date
            
            if date_filters:
                query["assigned_at"] = date_filters
            
            total = await UserCompany.find(query).count()
            
            cursor = UserCompany.find(query).sort([("assigned_at", -1)])
            
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit > 0:
                cursor = cursor.limit(limit)
            
            assignments = await cursor.to_list()
            
            return assignments, total
            
        except Exception as e:
            logger.error(f"Error searching assignments: {e}")
            return [], 0
    
    
    @staticmethod
    @monitor_db_operation("user_company_update_role")
    async def update_assignment_role(
        assignment_id: str,
        role: str,
        updated_by: str
    ) -> Optional[UserCompany]:
        try:
            assignment = await UserCompany.get(ObjectId(assignment_id))
            if not assignment:
                return None
            
            if not assignment.is_active:
                raise ValueError("Cannot update role of inactive assignment")
            
            old_role = assignment.role
            assignment.role = role
            assignment.updated_by = ObjectId(updated_by)
            assignment.updated_at = now_vn()
            await assignment.save()
            
            await UserCompanyRepository._invalidate_assignment_caches(assignment)
            
            logger.info(f"Updated assignment {assignment_id} role from {old_role} to {role}")
            return assignment
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error updating assignment role: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_company_update_permissions")
    async def update_assignment_permissions(
        assignment_id: str,
        permissions: List[str],
        updated_by: str
    ) -> Optional[UserCompany]:
        try:
            assignment = await UserCompany.get(ObjectId(assignment_id))
            if not assignment:
                return None
            
            if not assignment.is_active:
                raise ValueError("Cannot update permissions of inactive assignment")
            
            assignment.permissions = permissions
            assignment.updated_by = ObjectId(updated_by)
            assignment.updated_at = now_vn()
            await assignment.save()
            
            await UserCompanyRepository._invalidate_assignment_caches(assignment)
            
            logger.info(f"Updated assignment {assignment_id} permissions")
            return assignment
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error updating assignment permissions: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_company_update_dates")
    async def update_assignment_dates(
        assignment_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        updated_by: str = None
    ) -> Optional[UserCompany]:
        try:
            assignment = await UserCompany.get(ObjectId(assignment_id))
            if not assignment:
                return None
            
            if start_date:
                assignment.start_date = start_date
            
            if end_date is not None:
                assignment.end_date = end_date
            
            if updated_by:
                assignment.updated_by = ObjectId(updated_by)
            
            assignment.updated_at = now_vn()
            await assignment.save()
            
            await UserCompanyRepository._invalidate_assignment_caches(assignment)
            
            logger.info(f"Updated assignment {assignment_id} dates")
            return assignment
            
        except Exception as e:
            logger.error(f"Error updating assignment dates: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_company_reactivate")
    async def reactivate_assignment(
        assignment_id: str,
        reactivated_by: str,
        new_role: Optional[str] = None,
        new_permissions: Optional[List[str]] = None
    ) -> Optional[UserCompany]:
        try:
            assignment = await UserCompany.get(ObjectId(assignment_id))
            if not assignment:
                return None
            
            if assignment.is_active:
                raise ValueError("Assignment is already active")
            
            assignment.is_active = True
            assignment.unassigned_at = None
            assignment.unassigned_by = None
            assignment.unassign_reason = None
            
            if new_role:
                assignment.role = new_role
            
            if new_permissions:
                assignment.permissions = new_permissions
            
            assignment.updated_by = ObjectId(reactivated_by)
            assignment.updated_at = now_vn()
            await assignment.save()
            
            await UserCompanyRepository._invalidate_assignment_caches(assignment)
            
            logger.info(f"Reactivated assignment {assignment_id}")
            return assignment
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error reactivating assignment: {e}")
            return None
    
    
    @staticmethod
    @monitor_db_operation("user_company_get_stats")
    @monitor_cache_operation("user_company_get_stats")
    async def get_branch_assignment_stats(company_branch_id: str) -> UserCompanyStats:
        cache_key = UserCompanyRepository._get_branch_stats_cache_key(company_branch_id)
        cached_data = await UserCompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for branch stats: {company_branch_id}")
            return UserCompanyStats(**cached_data)
        
        try:
            active_count = await UserCompany.find({
                "company_branch_id": ObjectId(company_branch_id),
                "is_active": True
            }).count()
            
            inactive_count = await UserCompany.find({
                "company_branch_id": ObjectId(company_branch_id),
                "is_active": False
            }).count()
            
            pipeline = [
                {"$match": {
                    "company_branch_id": ObjectId(company_branch_id),
                    "is_active": True
                }},
                {"$group": {
                    "_id": "$role",
                    "count": {"$sum": 1}
                }}
            ]
            
            role_counts = {}
            async for doc in UserCompany.aggregate(pipeline):
                role_counts[doc["_id"]] = doc["count"]
            
            thirty_days_ago = now_vn().replace(hour=0, minute=0, second=0, microsecond=0)
            thirty_days_ago = thirty_days_ago.replace(day=thirty_days_ago.day - 30)
            
            recent_assignments = await UserCompany.find({
                "company_branch_id": ObjectId(company_branch_id),
                "assigned_at": {"$gte": thirty_days_ago}
            }).count()
            
            seven_days_from_now = now_vn().replace(hour=23, minute=59, second=59, microsecond=999999)
            seven_days_from_now = seven_days_from_now.replace(day=seven_days_from_now.day + 7)
            
            ending_soon = await UserCompany.find({
                "company_branch_id": ObjectId(company_branch_id),
                "is_active": True,
                "end_date": {
                    "$ne": None,
                    "$lte": seven_days_from_now,
                    "$gte": now_vn()
                }
            }).count()
            
            stats = UserCompanyStats(
                company_branch_id=company_branch_id,
                total_assignments=active_count + inactive_count,
                active_assignments=active_count,
                inactive_assignments=inactive_count,
                assignments_by_role=role_counts,
                recent_assignments_30d=recent_assignments,
                assignments_ending_soon=ending_soon,
                calculated_at=datetime.now()
            )
            
            await UserCompanyRepository._set_cache(
                cache_key,
                stats.dict(),
                900  # 15 minutes for stats cache
            )
            logger.debug(f"Cache set for branch stats: {company_branch_id}")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting branch assignment stats: {e}")
            return UserCompanyStats(
                company_branch_id=company_branch_id,
                total_assignments=0,
                active_assignments=0,
                inactive_assignments=0,
                assignments_by_role={},
                recent_assignments_30d=0,
                assignments_ending_soon=0,
                calculated_at=datetime.now(),
                error=str(e)
            )
    
    @staticmethod
    @monitor_db_operation("user_company_get_user_stats")
    async def get_user_assignment_stats(user_id: str) -> Dict[str, Any]:
        try:
            total_assignments = await UserCompany.find({
                "user_id": ObjectId(user_id)
            }).count()
            
            active_assignments = await UserCompany.find({
                "user_id": ObjectId(user_id),
                "is_active": True
            }).count()
            
            pipeline = [
                {"$match": {
                    "user_id": ObjectId(user_id),
                    "is_active": True
                }},
                {"$group": {
                    "_id": "$role",
                    "count": {"$sum": 1}
                }}
            ]
            
            role_counts = {}
            async for doc in UserCompany.aggregate(pipeline):
                role_counts[doc["_id"]] = doc["count"]
            
            pipeline = [
                {"$match": {
                    "user_id": ObjectId(user_id),
                    "is_active": True
                }},
                {"$lookup": {
                    "from": "company_branches",
                    "localField": "company_branch_id",
                    "foreignField": "_id",
                    "as": "branch"
                }},
                {"$unwind": "$branch"},
                {"$group": {
                    "_id": "$branch.company_id",
                    "branches": {"$addToSet": "$branch._id"}
                }}
            ]
            
            company_ids = []
            async for doc in UserCompany.aggregate(pipeline):
                company_ids.append(str(doc["_id"]))
            
            stats = {
                "user_id": user_id,
                "total_assignments": total_assignments,
                "active_assignments": active_assignments,
                "inactive_assignments": total_assignments - active_assignments,
                "assignments_by_role": role_counts,
                "current_companies": len(company_ids),
                "company_ids": company_ids,
                "calculated_at": datetime.now().isoformat()
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting user assignment stats: {e}")
            return {
                "user_id": user_id,
                "error": str(e),
                "calculated_at": datetime.now().isoformat()
            }
    
    
    @staticmethod
    @monitor_db_operation("user_company_validate_access")
    async def validate_user_branch_access(
        user_id: str,
        company_branch_id: str
    ) -> bool:
        try:
            assignment = await UserCompanyRepository.get_user_branch_assignment(user_id, company_branch_id)
            
            if not assignment:
                return False
            
            current_time = now_vn()
            if assignment.start_date and assignment.start_date > current_time:
                return False
            
            if assignment.end_date and assignment.end_date < current_time:
                if assignment.is_active:
                    assignment.is_active = False
                    assignment.updated_at = current_time
                    await assignment.save()
                    await UserCompanyRepository._invalidate_assignment_caches(assignment)
                
                return False
            
            return assignment.is_active
            
        except Exception as e:
            logger.error(f"Error validating user branch access: {e}")
            return False
    
    @staticmethod
    async def get_user_role_in_branch(
        user_id: str,
        company_branch_id: str
    ) -> Optional[str]:
        try:
            assignment = await UserCompanyRepository.get_user_branch_assignment(user_id, company_branch_id)
            return assignment.role if assignment else None
        except Exception as e:
            logger.error(f"Error getting user role in branch: {e}")
            return None
    
    @staticmethod
    async def get_user_permissions_in_branch(
        user_id: str,
        company_branch_id: str
    ) -> List[str]:
        try:
            assignment = await UserCompanyRepository.get_user_branch_assignment(user_id, company_branch_id)
            return assignment.permissions if assignment else []
        except Exception as e:
            logger.error(f"Error getting user permissions in branch: {e}")
            return []
    
    
    @staticmethod
    @monitor_db_operation("user_company_bulk_assign")
    async def bulk_assign_users(
        user_ids: List[str],
        company_branch_id: str,
        assigned_by: str,
        role: str = "member",
        permissions: Optional[List[str]] = None
    ) -> Tuple[int, List[str]]:
        try:
            successful = []
            failed = []
            
            for user_id in user_ids:
                try:
                    assignment = await UserCompanyRepository.assign_user_to_branch(
                        user_id=user_id,
                        company_branch_id=company_branch_id,
                        assigned_by=assigned_by,
                        role=role,
                        permissions=permissions
                    )
                    
                    if assignment:
                        successful.append(user_id)
                    else:
                        failed.append(user_id)
                        
                except Exception as e:
                    logger.error(f"Failed to assign user {user_id} to branch {company_branch_id}: {e}")
                    failed.append(user_id)
            
            logger.info(f"Bulk assignment completed: {len(successful)} successful, {len(failed)} failed")
            return len(successful), failed
            
        except Exception as e:
            logger.error(f"Error in bulk assign users: {e}")
            return 0, user_ids
    
    @staticmethod
    @monitor_db_operation("user_company_bulk_unassign")
    async def bulk_unassign_users(
        user_ids: List[str],
        company_branch_id: str,
        unassigned_by: str,
        reason: Optional[str] = None
    ) -> Tuple[int, List[str]]:
        try:
            successful = []
            failed = []
            
            for user_id in user_ids:
                try:
                    success = await UserCompanyRepository.unassign_user_from_branch(
                        user_id=user_id,
                        company_branch_id=company_branch_id,
                        unassigned_by=unassigned_by,
                        reason=reason
                    )
                    
                    if success:
                        successful.append(user_id)
                    else:
                        failed.append(user_id)
                        
                except Exception as e:
                    logger.error(f"Failed to unassign user {user_id} from branch {company_branch_id}: {e}")
                    failed.append(user_id)
            
            logger.info(f"Bulk unassignment completed: {len(successful)} successful, {len(failed)} failed")
            return len(successful), failed
            
        except Exception as e:
            logger.error(f"Error in bulk unassign users: {e}")
            return 0, user_ids
    
    
    @staticmethod
    async def _get_from_cache(key: str) -> Optional[Any]:
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
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            import json
            await redis_client.setex(
                key,
                ttl or UserCompanyRepository.ASSIGNMENT_CACHE_TTL,
                json.dumps(data, default=str)
            )
        except Exception as e:
            logger.warning(f"Cache set error for key {key}: {e}")
    
    @staticmethod
    async def _delete_cache(key: str) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            await redis_client.delete(key)
        except Exception as e:
            logger.warning(f"Cache delete error for key {key}: {e}")
    
    @staticmethod
    async def _invalidate_assignment_caches(assignment: UserCompany) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            keys_to_delete = [
                UserCompanyRepository._get_assignment_cache_key(str(assignment.id)),
                UserCompanyRepository._get_user_branch_cache_key(str(assignment.user_id), str(assignment.company_branch_id)),
                UserCompanyRepository._get_branch_stats_cache_key(str(assignment.company_branch_id)),
            ]
            
            keys_to_delete.append(
                UserCompanyRepository._get_user_assignments_cache_key(str(assignment.user_id), True)
            )
            keys_to_delete.append(
                UserCompanyRepository._get_user_assignments_cache_key(str(assignment.user_id), False)
            )
            
            keys_to_delete.append(
                UserCompanyRepository._get_branch_assignments_cache_key(str(assignment.company_branch_id), True)
            )
            keys_to_delete.append(
                UserCompanyRepository._get_branch_assignments_cache_key(str(assignment.company_branch_id), False)
            )
            
            if keys_to_delete:
                await redis_client.delete(*keys_to_delete)
                logger.debug(f"Invalidated caches for assignment: {assignment.id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating assignment caches for {assignment.id}: {e}")
    
    @staticmethod
    async def clear_all_cache() -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            pattern = f"{UserCompanyRepository.CACHE_PREFIX}*"
            keys = await redis_client.keys(pattern)
            
            if keys:
                await redis_client.delete(*keys)
                logger.info(f"Cleared all user_company cache ({len(keys)} keys)")
            
        except Exception as e:
            logger.warning(f"Error clearing user_company cache: {e}")