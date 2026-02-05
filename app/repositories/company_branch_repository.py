from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import logging
from app.models.company_branch import CompanyBranch
from app.models.company import Company
from app.models.user import User
from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchUpdate
from app.core.redis import get_redis, is_redis_available
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_vn

logger = logging.getLogger(__name__)


class CompanyBranchRepository:
    CACHE_PREFIX = "company_branch:"
    BRANCH_CACHE_TTL = 3600
    BRANCH_LIST_CACHE_TTL = 300 
    USER_BRANCHES_CACHE_TTL = 1800
        
    @staticmethod
    def _get_branch_cache_key(branch_id: str) -> str:
        return f"{CompanyBranchRepository.CACHE_PREFIX}branch:{branch_id}"
    
    @staticmethod
    def _get_company_branches_cache_key(company_id: str, active_only: bool = True) -> str:
        return f"{CompanyBranchRepository.CACHE_PREFIX}company_branches:{company_id}:{'active' if active_only else 'all'}"
    
    @staticmethod
    def _get_user_branches_cache_key(user_id: str, active_only: bool = True) -> str:
        return f"{CompanyBranchRepository.CACHE_PREFIX}user_branches:{user_id}:{'active' if active_only else 'all'}"
    
    @staticmethod
    def _get_branch_search_cache_key(
        search_term: str,
        filters: Dict[str, Any],
        skip: int,
        limit: int
    ) -> str:
        filter_str = str(sorted(filters.items()))
        return f"{CompanyBranchRepository.CACHE_PREFIX}search:{search_term}:{filter_str}:{skip}:{limit}"
    
    @staticmethod
    def _get_user_branch_access_cache_key(user_id: str, branch_id: str) -> str:
        return f"{CompanyBranchRepository.CACHE_PREFIX}user_access:{user_id}:{branch_id}"
    
    
    @staticmethod
    @monitor_db_operation("branch_create")
    async def create_company_branch(
        company_id: str,
        branch_data: CompanyBranchCreate,
        created_by: str
    ) -> CompanyBranch:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                raise ValueError(f"Company with ID {company_id} does not exist")
            
            has_permission = False
            for member in company.members:
                if str(member["user_id"]) == created_by:
                    if "manage_branches" in member.get("permissions", []) or member["role"] == "owner":
                        has_permission = True
                    break
            
            if not has_permission:
                raise ValueError("User does not have permission to create branches")
            
            existing_branches = await CompanyBranch.find({
                "company_id": ObjectId(company_id),
                "is_active": True
            }).count()
            
            is_headquarters = existing_branches == 0
            
            branch_dict = branch_data.dict()
            branch_dict["company_id"] = ObjectId(company_id)
            branch_dict["created_by"] = ObjectId(created_by)
            branch_dict["is_headquarters"] = is_headquarters
            branch_dict["is_active"] = True
            branch_dict["created_at"] = now_vn()
            branch_dict["updated_at"] = now_vn()
            
            branch = CompanyBranch(**branch_dict)
            await branch.insert()
            
            await CompanyBranchRepository._invalidate_branch_caches(branch)
            
            logger.info(f"Company branch created: {branch.id} - {branch.name}")
            return branch
            
        except ValueError as e:
            raise
        except DuplicateKeyError as e:
            logger.error(f"Duplicate key error creating branch: {e}")
            raise ValueError("Branch with similar criteria already exists")
        except Exception as e:
            logger.error(f"Error creating company branch: {e}", exc_info=True)
            raise
    
    @staticmethod
    @monitor_db_operation("branch_get")
    @monitor_cache_operation("branch_get")
    async def get_company_branch(branch_id: str) -> Optional[CompanyBranch]:
        cache_key = CompanyBranchRepository._get_branch_cache_key(branch_id)
        cached_data = await CompanyBranchRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for branch: {branch_id}")
            branch = CompanyBranch.model_validate(cached_data)
            setattr(branch, '_from_cache', True)
            return branch
        
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if branch:
                await CompanyBranchRepository._set_cache(
                    cache_key,
                    branch.dict(),
                    CompanyBranchRepository.BRANCH_CACHE_TTL
                )
                logger.debug(f"Cache set for branch: {branch_id}")
            return branch
        except Exception as e:
            logger.error(f"Error getting branch {branch_id}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("branch_update")
    async def update_company_branch(
        branch_id: str,
        update_data: CompanyBranchUpdate,
        updated_by: str
    ) -> Optional[CompanyBranch]:
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if not branch:
                return None
            
            company = await Company.get(branch.company_id)
            if not company:
                raise ValueError("Company not found")
            
            has_permission = False
            for member in company.members:
                if str(member["user_id"]) == updated_by:
                    if "manage_branches" in member.get("permissions", []) or member["role"] == "owner":
                        has_permission = True
                    break
            
            if not has_permission:
                raise ValueError("User does not have permission to update this branch")
            
            update_dict = update_data.model_dump(exclude_unset=True)
            
            if "is_headquarters" in update_dict and not update_dict["is_headquarters"]:
                if branch.is_headquarters:
                    other_headquarters = await CompanyBranch.find({
                        "company_id": branch.company_id,
                        "is_headquarters": True,
                        "_id": {"$ne": branch.id},
                        "is_active": True
                    }).count()
                    
                    if other_headquarters == 0:
                        raise ValueError("Cannot remove headquarters status from the only headquarters")
            
            for field, value in update_dict.items():
                setattr(branch, field, value)
            
            branch.updated_at = now_vn()
            await branch.save()
            
            await CompanyBranchRepository._invalidate_branch_caches(branch)
            
            logger.info(f"Company branch updated: {branch_id}")
            return branch
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error updating branch {branch_id}: {e}", exc_info=True)
            raise
    
    @staticmethod
    @monitor_db_operation("branch_delete")
    async def delete_company_branch(
        branch_id: str,
        deleted_by: str,
        reason: Optional[str] = None
    ) -> bool:
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if not branch:
                return False
            
            company = await Company.get(branch.company_id)
            if not company:
                raise ValueError("Company not found")
            
            has_permission = False
            for member in company.members:
                if str(member["user_id"]) == deleted_by:
                    if "manage_branches" in member.get("permissions", []) or member["role"] == "owner":
                        has_permission = True
                    break
            
            if not has_permission:
                raise ValueError("User does not have permission to delete this branch")
            
            if branch.is_headquarters:
                other_headquarters = await CompanyBranch.find({
                    "company_id": branch.company_id,
                    "is_headquarters": True,
                    "_id": {"$ne": branch.id},
                    "is_active": True
                }).count()
                
                if other_headquarters == 0:
                    raise ValueError("Cannot delete the only headquarters")
            
            branch.is_active = False
            branch.deleted_at = now_vn()
            branch.deleted_by = ObjectId(deleted_by)
            branch.deletion_reason = reason
            branch.updated_at = now_vn()
            await branch.save()
            
            await CompanyBranchRepository._invalidate_branch_caches(branch)
            
            logger.info(f"Company branch soft deleted: {branch_id}")
            return True
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error deleting branch {branch_id}: {e}", exc_info=True)
            return False
    
    @staticmethod
    @monitor_db_operation("branch_restore")
    async def restore_company_branch(
        branch_id: str,
        restored_by: str
    ) -> bool:
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if not branch:
                return False
            
            company = await Company.get(branch.company_id)
            if not company:
                raise ValueError("Company not found")
            
            has_permission = False
            for member in company.members:
                if str(member["user_id"]) == restored_by:
                    if "manage_branches" in member.get("permissions", []) or member["role"] == "owner":
                        has_permission = True
                    break
            
            if not has_permission:
                raise ValueError("User does not have permission to restore this branch")
            
            branch.is_active = True
            branch.deleted_at = None
            branch.deleted_by = None
            branch.deletion_reason = None
            branch.updated_at = now_vn()
            await branch.save()
            
            await CompanyBranchRepository._invalidate_branch_caches(branch)
            
            logger.info(f"Company branch restored: {branch_id}")
            return True
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error restoring branch {branch_id}: {e}", exc_info=True)
            return False
    
    
    @staticmethod
    @monitor_db_operation("branch_list_company")
    @monitor_cache_operation("branch_list_company")
    async def get_company_branches(
        company_id: str,
        active_only: bool = True
    ) -> List[CompanyBranch]:
        cache_key = CompanyBranchRepository._get_company_branches_cache_key(company_id, active_only)
        cached_data = await CompanyBranchRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for company branches: {company_id}")
            branches = [CompanyBranch.model_validate(item) for item in cached_data]
            for branch in branches:
                setattr(branch, '_from_cache', True)
            return branches
        
        try:
            query = {"company_id": ObjectId(company_id)}
            if active_only:
                query["is_active"] = True
            
            branches = await CompanyBranch.find(query).sort("name").to_list()
            
            if branches:
                await CompanyBranchRepository._set_cache(
                    cache_key,
                    [branch.dict() for branch in branches],
                    CompanyBranchRepository.BRANCH_LIST_CACHE_TTL
                )
                logger.debug(f"Cache set for company branches: {company_id}")
            
            return branches
        except Exception as e:
            logger.error(f"Error getting branches for company {company_id}: {e}")
            return []
    
    @staticmethod
    @monitor_db_operation("branch_list_user")
    @monitor_cache_operation("branch_list_user")
    async def get_user_company_branches(
        user_id: str,
        active_only: bool = True
    ) -> List[CompanyBranch]:
        cache_key = CompanyBranchRepository._get_user_branches_cache_key(user_id, active_only)
        cached_data = await CompanyBranchRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user branches: {user_id}")
            branches = [CompanyBranch.model_validate(item) for item in cached_data]
            for branch in branches:
                setattr(branch, '_from_cache', True)
            return branches
        
        try:
            companies = await Company.find({
                "members.user_id": ObjectId(user_id),
                "is_active": True
            }).to_list()
            
            company_ids = [company.id for company in companies]
            
            if not company_ids:
                return []
            
            query = {"company_id": {"$in": company_ids}}
            if active_only:
                query["is_active"] = True
            
            branches = await CompanyBranch.find(query).sort("name").to_list()
            
            if branches:
                await CompanyBranchRepository._set_cache(
                    cache_key,
                    [branch.dict() for branch in branches],
                    CompanyBranchRepository.USER_BRANCHES_CACHE_TTL
                )
                logger.debug(f"Cache set for user branches: {user_id}")
            
            return branches
        except Exception as e:
            logger.error(f"Error getting user branches for {user_id}: {e}")
            return []
    
    @staticmethod
    @monitor_db_operation("branch_search")
    @monitor_cache_operation("branch_search")
    async def search_branches(
        search_term: Optional[str] = None,
        company_id: Optional[str] = None,
        city: Optional[str] = None,
        country: Optional[str] = None,
        is_headquarters: Optional[bool] = None,
        is_active: bool = True,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[CompanyBranch], int]:
        filters = {
            "company_id": company_id,
            "city": city,
            "country": country,
            "is_headquarters": is_headquarters,
            "is_active": is_active
        }
        cache_key = CompanyBranchRepository._get_branch_search_cache_key(
            search_term or "", filters, skip, limit
        )
        
        cached_data = await CompanyBranchRepository._get_from_cache(cache_key)
        if cached_data:
            logger.debug(f"Cache hit for branch search: {search_term}")
            branches = [CompanyBranch.model_validate(item) for item in cached_data.get("branches", [])]
            total = cached_data.get("total", 0)
            for branch in branches:
                setattr(branch, '_from_cache', True)
            return branches, total
        
        try:
            query = {}
            
            if is_active is not None:
                query["is_active"] = is_active
            
            if company_id:
                query["company_id"] = ObjectId(company_id)
            
            if city:
                query["city"] = {"$regex": city, "$options": "i"}
            
            if country:
                query["country"] = {"$regex": country, "$options": "i"}
            
            if is_headquarters is not None:
                query["is_headquarters"] = is_headquarters
            
            if search_term:
                query["$or"] = [
                    {"name": {"$regex": search_term, "$options": "i"}},
                    {"description": {"$regex": search_term, "$options": "i"}},
                    {"address": {"$regex": search_term, "$options": "i"}},
                    {"email": {"$regex": search_term, "$options": "i"}},
                    {"phone": {"$regex": search_term, "$options": "i"}}
                ]
            
            total = await CompanyBranch.find(query).count()
            
            cursor = CompanyBranch.find(query).sort([("name", 1)])
            branches = await cursor.skip(skip).limit(limit).to_list()
            
            if branches:
                cache_data = {
                    "branches": [branch.dict() for branch in branches],
                    "total": total
                }
                await CompanyBranchRepository._set_cache(
                    cache_key,
                    cache_data,
                    CompanyBranchRepository.BRANCH_LIST_CACHE_TTL
                )
                logger.debug(f"Cache set for branch search: {search_term}")
            
            return branches, total
            
        except Exception as e:
            logger.error(f"Error searching branches: {e}", exc_info=True)
            return [], 0
    
    @staticmethod
    @monitor_db_operation("branch_get_headquarters")
    async def get_company_headquarters(company_id: str) -> Optional[CompanyBranch]:
        try:
            branch = await CompanyBranch.find_one({
                "company_id": ObjectId(company_id),
                "is_headquarters": True,
                "is_active": True
            })
            return branch
        except Exception as e:
            logger.error(f"Error getting company headquarters: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("branch_validate_user_access")
    @monitor_cache_operation("branch_validate_user_access")
    async def validate_user_access(
        user_id: str,
        company_branch_id: str
    ) -> bool:
        cache_key = CompanyBranchRepository._get_user_branch_access_cache_key(user_id, company_branch_id)
        cached_data = await CompanyBranchRepository._get_from_cache(cache_key)
        
        if cached_data is not None:
            logger.debug(f"Cache hit for user access: {user_id} -> {company_branch_id}")
            return cached_data
        
        try:
            branch = await CompanyBranch.get(ObjectId(company_branch_id))
            if not branch or not branch.is_active:
                result = False
            else:
                company = await Company.get(branch.company_id)
                if not company or not company.is_active:
                    result = False
                else:
                    result = any(
                        str(member["user_id"]) == user_id 
                        for member in company.members
                    )
            
            await CompanyBranchRepository._set_cache(cache_key, result, 300)  # 5 minutes
            
            return result
            
        except Exception as e:
            logger.error(f"Error validating user access: {e}")
            return False
    
    @staticmethod
    @monitor_db_operation("branch_get_user_permissions")
    async def get_user_branch_permissions(
        user_id: str,
        company_branch_id: str
    ) -> Dict[str, Any]:
        try:
            branch = await CompanyBranch.get(ObjectId(company_branch_id))
            if not branch or not branch.is_active:
                return {"has_access": False}
            
            company = await Company.get(branch.company_id)
            if not company or not company.is_active:
                return {"has_access": False}
            
            user_member = None
            for member in company.members:
                if str(member["user_id"]) == user_id:
                    user_member = member
                    break
            
            if not user_member:
                return {"has_access": False}
            
            permissions = {
                "has_access": True,
                "role": user_member["role"],
                "company_permissions": user_member.get("permissions", []),
                "branch_permissions": [],
                "can_manage_branch": "manage_branches" in user_member.get("permissions", []) or user_member["role"] == "owner",
                "can_view_branch": True,
                "can_edit_branch": "manage_branches" in user_member.get("permissions", []) or user_member["role"] == "owner",
                "can_delete_branch": user_member["role"] == "owner",  # Only owner can delete
                "branch_is_headquarters": branch.is_headquarters
            }
            
            return permissions
            
        except Exception as e:
            logger.error(f"Error getting user branch permissions: {e}")
            return {"has_access": False, "error": str(e)}
    
    @staticmethod
    @monitor_db_operation("branch_update_headquarters")
    async def update_headquarters(
        company_id: str,
        new_headquarters_id: str,
        updated_by: str
    ) -> bool:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                raise ValueError("Company not found")
            
            has_permission = False
            for member in company.members:
                if str(member["user_id"]) == updated_by:
                    if member["role"] == "owner":
                        has_permission = True
                    break
            
            if not has_permission:
                raise ValueError("Only company owner can change headquarters")
            
            new_hq = await CompanyBranch.get(ObjectId(new_headquarters_id))
            if not new_hq or not new_hq.is_active:
                raise ValueError("New headquarters branch not found or inactive")
            
            if str(new_hq.company_id) != company_id:
                raise ValueError("Branch does not belong to this company")
            
            # Start transaction (simulated with multiple updates)
            # 1. Remove headquarters status from current headquarters
            await CompanyBranch.find({
                "company_id": ObjectId(company_id),
                "is_headquarters": True,
                "is_active": True
            }).update_many({"$set": {"is_headquarters": False, "updated_at": now_vn()}})
            
            # 2. Set new headquarters
            new_hq.is_headquarters = True
            new_hq.updated_at = now_vn()
            await new_hq.save()
            
            # Invalidate caches for all company branches
            company_branches = await CompanyBranch.find({
                "company_id": ObjectId(company_id)
            }).to_list()
            
            for branch in company_branches:
                await CompanyBranchRepository._invalidate_branch_caches(branch)
            
            logger.info(f"Updated headquarters for company {company_id} to branch {new_headquarters_id}")
            return True
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error updating headquarters: {e}")
            return False
    
    
    @staticmethod
    @monitor_db_operation("branch_get_statistics")
    async def get_branch_statistics(branch_id: str) -> Dict[str, Any]:
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if not branch:
                return {"error": "Branch not found"}
            
            company = await Company.get(branch.company_id)
            if not company:
                return {"error": "Company not found"}
            
            stats = {
                "branch_id": branch_id,
                "branch_name": branch.name,
                "company_id": str(branch.company_id),
                "company_name": company.name if company else None,
                "is_headquarters": branch.is_headquarters,
                "is_active": branch.is_active,
                "created_at": branch.created_at.isoformat() if branch.created_at else None,
                "updated_at": branch.updated_at.isoformat() if branch.updated_at else None,
                "address": branch.address,
                "city": branch.city,
                "country": branch.country,
                "contact_email": branch.email,
                "contact_phone": branch.phone,
                "calculated_at": datetime.now().isoformat()
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting branch statistics: {e}")
            return {
                "branch_id": branch_id,
                "error": str(e),
                "calculated_at": datetime.now().isoformat()
            }
    
    @staticmethod
    @monitor_db_operation("branch_get_company_statistics")
    async def get_company_branch_statistics(company_id: str) -> Dict[str, Any]:
        try:
            branches = await CompanyBranch.find({
                "company_id": ObjectId(company_id),
                "is_active": True
            }).to_list()
            
            total_branches = len(branches)
            headquarters_count = sum(1 for b in branches if b.is_headquarters)
            
            cities = {}
            countries = {}
            
            for branch in branches:
                if branch.city:
                    cities[branch.city] = cities.get(branch.city, 0) + 1
                if branch.country:
                    countries[branch.country] = countries.get(branch.country, 0) + 1
            
            stats = {
                "company_id": company_id,
                "total_branches": total_branches,
                "headquarters_count": headquarters_count,
                "regular_branches_count": total_branches - headquarters_count,
                "branches_by_city": cities,
                "branches_by_country": countries,
                "branches_created_last_30d": 0,
                "calculated_at": datetime.now().isoformat()
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting company branch statistics: {e}")
            return {
                "company_id": company_id,
                "error": str(e),
                "calculated_at": datetime.now().isoformat()
            }
    
    
    @staticmethod
    @monitor_db_operation("branch_bulk_update")
    async def bulk_update_branches(
        branch_ids: List[str],
        update_data: Dict[str, Any],
        updated_by: str
    ) -> Tuple[int, int]:
        try:
            if not branch_ids:
                return 0, 0
            
            restricted_fields = {"is_headquarters", "company_id"}
            update_data = {k: v for k, v in update_data.items() 
                          if k not in restricted_fields}
            
            if not update_data:
                return 0, len(branch_ids)
            
            update_data["updated_at"] = now_vn()
            
            result = await CompanyBranch.find({
                "_id": {"$in": [ObjectId(bid) for bid in branch_ids]}
            }).update_many({"$set": update_data})
            
            for branch_id in branch_ids:
                await CompanyBranchRepository._delete_cache(
                    CompanyBranchRepository._get_branch_cache_key(branch_id)
                )
            
            branches = await CompanyBranch.find({
                "_id": {"$in": [ObjectId(bid) for bid in branch_ids]}
            }).to_list()
            
            company_ids = set(str(branch.company_id) for branch in branches)
            for company_id in company_ids:
                await CompanyBranchRepository._delete_cache(
                    CompanyBranchRepository._get_company_branches_cache_key(company_id, True)
                )
                await CompanyBranchRepository._delete_cache(
                    CompanyBranchRepository._get_company_branches_cache_key(company_id, False)
                )
            
            logger.info(f"Bulk updated {result.modified_count} branches")
            return result.modified_count, len(branch_ids)
            
        except Exception as e:
            logger.error(f"Error bulk updating branches: {e}")
            return 0, 0
    
    @staticmethod
    @monitor_db_operation("branch_bulk_deactivate")
    async def bulk_deactivate_branches(
        branch_ids: List[str],
        deactivated_by: str,
        reason: Optional[str] = None
    ) -> int:
        try:
            if not branch_ids:
                return 0
            
            headquarters_branches = await CompanyBranch.find({
                "_id": {"$in": [ObjectId(bid) for bid in branch_ids]},
                "is_headquarters": True,
                "is_active": True
            }).to_list()
            
            if headquarters_branches:
                hq_branch_ids = [str(branch.id) for branch in headquarters_branches]
                branch_ids = [bid for bid in branch_ids if bid not in hq_branch_ids]
            
            update_data = {
                "is_active": False,
                "deleted_at": now_vn(),
                "deleted_by": ObjectId(deactivated_by),
                "deletion_reason": reason,
                "updated_at": now_vn()
            }
            
            result = await CompanyBranch.find({
                "_id": {"$in": [ObjectId(bid) for bid in branch_ids]}
            }).update_many({"$set": update_data})
            
            for branch_id in branch_ids:
                await CompanyBranchRepository._invalidate_branch_cache_completely(branch_id)
            
            logger.info(f"Bulk deactivated {result.modified_count} branches")
            return result.modified_count
            
        except Exception as e:
            logger.error(f"Error bulk deactivating branches: {e}")
            return 0
    
    
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
                ttl or CompanyBranchRepository.BRANCH_CACHE_TTL,
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
    async def _invalidate_branch_caches(branch: CompanyBranch) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            keys_to_delete = [
                CompanyBranchRepository._get_branch_cache_key(str(branch.id)),
                CompanyBranchRepository._get_company_branches_cache_key(str(branch.company_id), True),
                CompanyBranchRepository._get_company_branches_cache_key(str(branch.company_id), False),
                CompanyBranchRepository._get_branch_stats_cache_key(str(branch.id)),
            ]
            
            if keys_to_delete:
                await redis_client.delete(*keys_to_delete)
                logger.debug(f"Invalidated caches for branch: {branch.id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating branch caches for {branch.id}: {e}")
    
    @staticmethod
    async def _invalidate_branch_cache_completely(branch_id: str) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            patterns = [
                f"{CompanyBranchRepository.CACHE_PREFIX}*{branch_id}*",
                f"{CompanyBranchRepository.CACHE_PREFIX}*:{branch_id}:*",
            ]
            
            import asyncio
            delete_tasks = []
            for pattern in patterns:
                keys = await redis_client.keys(pattern)
                if keys:
                    delete_tasks.append(redis_client.delete(*keys))
            
            if delete_tasks:
                await asyncio.gather(*delete_tasks, return_exceptions=True)
                logger.debug(f"Completely invalidated caches for branch: {branch_id}")
            
        except Exception as e:
            logger.warning(f"Error completely invalidating branch caches for {branch_id}: {e}")
    
    @staticmethod
    async def clear_all_cache() -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            pattern = f"{CompanyBranchRepository.CACHE_PREFIX}*"
            keys = await redis_client.keys(pattern)
            
            if keys:
                await redis_client.delete(*keys)
                logger.info(f"Cleared all company branch cache ({len(keys)} keys)")
            
        except Exception as e:
            logger.warning(f"Error clearing company branch cache: {e}")
    
    @staticmethod
    def _get_branch_stats_cache_key(branch_id: str) -> str:
        return f"{CompanyBranchRepository.CACHE_PREFIX}stats:{branch_id}"