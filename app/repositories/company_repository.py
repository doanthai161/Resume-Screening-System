from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from motor.motor_asyncio import AsyncIOMotorCursor
import logging
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user import User
from app.schemas.company import (
    CompanyCreate, 
    CompanyUpdate,
)
from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchUpdate
from app.core.redis import get_redis, is_redis_available
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_utc

logger = logging.getLogger(__name__)


class CompanyRepository:
    CACHE_PREFIX = "company:"
    COMPANY_CACHE_TTL = 3600 
    BRANCH_CACHE_TTL = 3600 
    USER_COMPANY_CACHE_TTL = 1800 
        
    @staticmethod
    def _get_company_cache_key(company_id: str) -> str:
        return f"{CompanyRepository.CACHE_PREFIX}company:{company_id}"
    
    @staticmethod
    def _get_branch_cache_key(branch_id: str) -> str:
        return f"{CompanyRepository.CACHE_PREFIX}branch:{branch_id}"
    
    @staticmethod
    def _get_user_companies_cache_key(user_id: str) -> str:
        return f"{CompanyRepository.CACHE_PREFIX}user_companies:{user_id}"
    
    @staticmethod
    def _get_user_branches_cache_key(user_id: str) -> str:
        return f"{CompanyRepository.CACHE_PREFIX}user_branches:{user_id}"
    
    @staticmethod
    def _get_company_branches_cache_key(company_id: str) -> str:
        return f"{CompanyRepository.CACHE_PREFIX}company_branches:{company_id}"
    
    @staticmethod
    def _get_user_branch_access_cache_key(user_id: str, branch_id: str) -> str:
        return f"{CompanyRepository.CACHE_PREFIX}user_access:{user_id}:{branch_id}"
    
    
    @staticmethod
    @monitor_db_operation("company_create")
    async def create_company(company_data: CompanyCreate, owner_id: str) -> Company:
        try:
            owner_id_obj = ObjectId(owner_id)
            
            owner = await User.get(owner_id_obj)
            if not owner:
                raise ValueError(f"Owner with ID {owner_id} does not exist")
            
            company_dict = company_data.model_dump()
            # company_dict["owner_id"] = owner_id_obj
            company_dict["user_id"] = owner_id_obj 
            company_dict["created_at"] = now_utc()
            company_dict["updated_at"] = now_utc()
            
            company_dict["members"] = [{
                "user_id": owner_id_obj,
                "role": "owner",
                "joined_at": now_utc(),
                "permissions": ["admin", "manage_company", "manage_branches", "manage_members"]
            }]
            
            company = Company(**company_dict)
            await company.insert()
            
            await CompanyRepository._delete_cache(CompanyRepository._get_user_companies_cache_key(owner_id))
            
            logger.info(f"Company created: {company.id} - {company.name}")
            return company
            
        except DuplicateKeyError as e:
            logger.error(f"Duplicate key error creating company: {e}")
            raise ValueError("Company with similar criteria already exists")
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error creating company: {e}", exc_info=True)
            raise
    


    @staticmethod
    @monitor_db_operation("company_list_all_active")
    async def list_all_active_companies(
        page: int = 1,
        size: int = 10
    ) -> Tuple[List[Company], int]:
        """
        Lấy danh sách tất cả các công ty đang hoạt động, có phân trang.

        Args:
            page (int): Số trang hiện tại.
            size (int): Số lượng mục trên mỗi trang.

        Returns:
            Tuple[List[Company], int]: Một tuple chứa danh sách các đối tượng Company
                                    và tổng số lượng công ty đang hoạt động.
        """
        skip = (page - 1) * size
        
        try:
            # Xây dựng bộ lọc: chỉ lấy các công ty đang hoạt động
            filter_dict = {"is_active": True}
            
            # Tạo cursor để tìm kiếm
            cursor: AsyncIOMotorCursor = Company.find(filter_dict)
            
            # Lấy tổng số lượng công ty khớp bộ lọc
            total = await cursor.count()
            
            # Lấy danh sách công ty đã phân trang
            companies = await cursor.skip(skip).limit(size).to_list()
            
            logger.info(f"Found {len(companies)} active companies (page {page})")
            return companies, total
            
        except Exception as e:
            logger.error(f"Error listing all active companies: {e}", exc_info=True)
            # Trả về danh sách rỗng và tổng số 0 nếu có lỗi
            return [], 0


    @staticmethod
    @monitor_db_operation("company_get")
    @monitor_cache_operation("company_get")
    async def get_company(company_id: str) -> Optional[Company]:
        cache_key = CompanyRepository._get_company_cache_key(company_id)
        cached_data = await CompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for company: {company_id}")
            company = Company.model_validate(cached_data)
            setattr(company, '_from_cache', True)
            return company
        
        try:
            company = await Company.get(ObjectId(company_id))
            if company:
                await CompanyRepository._set_cache(
                    cache_key, 
                    company.dict(), 
                    CompanyRepository.COMPANY_CACHE_TTL
                )
                logger.debug(f"Cache set for company: {company_id}")
            return company
        except Exception as e:
            logger.error(f"Error getting company {company_id}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("company_update")
    async def update_company(company_id: str, update_data: CompanyUpdate) -> Optional[Company]:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                return None
            
            update_dict = update_data.model_dump(exclude_unset=True)
            for field, value in update_dict.items():
                setattr(company, field, value)
            
            company.updated_at = now_utc()
            await company.save()
            
            cache_key = CompanyRepository._get_company_cache_key(company_id)
            await CompanyRepository._delete_cache(cache_key)
            
            # member_ids = [str(member["user_id"]) for member in company.members]
            # for user_id in member_ids:
            #     user_cache_key = CompanyRepository._get_user_companies_cache_key(user_id)
            #     await CompanyRepository._delete_cache(user_cache_key)
            
            logger.info(f"Company updated: {company_id}")
            return company
            
        except Exception as e:
            logger.error(f"Error updating company {company_id}: {e}", exc_info=True)
            raise
    
    @staticmethod
    @monitor_db_operation("company_delete")
    async def delete_company(company_id: str, user_id: str) -> bool:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                return False
            
            # is_owner = False
            # for member in company.members:
            #     if str(member["user_id"]) == user_id and member["role"] == "owner":
            #         is_owner = True
            #         break
            
            # if not is_owner:
            #     raise ValueError("Only the owner can delete the company")
            
            company.is_active = False
            company.updated_at = now_utc()
            await company.save()
            
            await CompanyRepository._invalidate_company_caches(company)
            
            logger.info(f"Company soft deleted: {company_id}")
            return True
            
        except ValueError as e:
            logger.error(f"Authorization error deleting company: {e}")
            raise
        except Exception as e:
            logger.error(f"Error deleting company {company_id}: {e}", exc_info=True)
            return False
    
    
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
            
            branch_dict = branch_data.dict()
            branch_dict["company_id"] = ObjectId(company_id)
            branch_dict["created_by"] = ObjectId(created_by)
            branch_dict["created_at"] = now_utc()
            branch_dict["updated_at"] = now_utc()
            branch_dict["is_active"] = True
            
            branch = CompanyBranch(**branch_dict)
            await branch.insert()
            await CompanyRepository._invalidate_branch_caches(branch)
            
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
        cache_key = CompanyRepository._get_branch_cache_key(branch_id)
        cached_data = await CompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for branch: {branch_id}")
            branch = CompanyBranch.model_validate(cached_data)
            setattr(branch, '_from_cache', True)
            return branch
        
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if branch:
                await CompanyRepository._set_cache(
                    cache_key, 
                    branch.dict(), 
                    CompanyRepository.BRANCH_CACHE_TTL
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
        user_id: str
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
                if str(member["user_id"]) == user_id:
                    if "manage_branches" in member.get("permissions", []) or member["role"] == "owner":
                        has_permission = True
                    break
            
            if not has_permission:
                raise ValueError("User does not have permission to update this branch")
            
            update_dict = update_data.dict(exclude_unset=True)
            for field, value in update_dict.items():
                setattr(branch, field, value)
            
            branch.updated_at = now_utc()
            await branch.save()
            
            await CompanyRepository._invalidate_branch_caches(branch)
            
            logger.info(f"Company branch updated: {branch_id}")
            return branch
            
        except ValueError as e:
            logger.error(f"Authorization error updating branch: {e}")
            raise
        except Exception as e:
            logger.error(f"Error updating branch {branch_id}: {e}", exc_info=True)
            raise
    
    @staticmethod
    @monitor_db_operation("branch_delete")
    async def delete_company_branch(branch_id: str, user_id: str) -> bool:
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if not branch:
                return False
            
            company = await Company.get(branch.company_id)
            if not company:
                raise ValueError("Company not found")
            
            has_permission = False
            for member in company.members:
                if str(member["user_id"]) == user_id:
                    if "manage_branches" in member.get("permissions", []) or member["role"] == "owner":
                        has_permission = True
                    break
            
            if not has_permission:
                raise ValueError("User does not have permission to delete this branch")
            
            branch.is_active = False
            branch.updated_at = now_utc()
            await branch.save()
            
            await CompanyRepository._invalidate_branch_caches(branch)
            
            logger.info(f"Company branch soft deleted: {branch_id}")
            return True
            
        except ValueError as e:
            logger.error(f"Authorization error deleting branch: {e}")
            raise
        except Exception as e:
            logger.error(f"Error deleting branch {branch_id}: {e}", exc_info=True)
            return False
    
    
    @staticmethod
    @monitor_db_operation("company_list_user")
    @monitor_cache_operation("company_list_user")
    async def get_user_companies(user_id: str) -> List[Company]:
        cache_key = CompanyRepository._get_user_companies_cache_key(user_id)
        cached_data = await CompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user companies: {user_id}")
            companies = [Company.model_validate(item) for item in cached_data]
            for company in companies:
                setattr(company, '_from_cache', True)
            return companies
        
        try:
            companies = await Company.find({
                "members.user_id": ObjectId(user_id),
                "is_active": True
            }).to_list()
            
            if companies:
                await CompanyRepository._set_cache(
                    cache_key, 
                    [company.dict() for company in companies],
                    CompanyRepository.USER_COMPANY_CACHE_TTL
                )
                logger.debug(f"Cache set for user companies: {user_id}")
            
            return companies
            
        except Exception as e:
            logger.error(f"Error getting user companies for {user_id}: {e}")
            return []
    
    @staticmethod
    @monitor_db_operation("branch_list_user")
    @monitor_cache_operation("branch_list_user")
    async def get_user_company_branches(user_id: str) -> List[CompanyBranch]:
        cache_key = CompanyRepository._get_user_branches_cache_key(user_id)
        cached_data = await CompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user branches: {user_id}")
            branches = [CompanyBranch.model_validate(item) for item in cached_data]
            for branch in branches:
                setattr(branch, '_from_cache', True)
            return branches
        
        try:
            companies = await CompanyRepository.get_user_companies(user_id)
            company_ids = [company.id for company in companies]
            
            branches = await CompanyBranch.find({
                "company_id": {"$in": company_ids},
                "is_active": True
            }).to_list()
            
            if branches:
                await CompanyRepository._set_cache(
                    cache_key, 
                    [branch.dict() for branch in branches],
                    CompanyRepository.USER_COMPANY_CACHE_TTL
                )
                logger.debug(f"Cache set for user branches: {user_id}")
            
            return branches
            
        except Exception as e:
            logger.error(f"Error getting user branches for {user_id}: {e}")
            return []
    
    @staticmethod
    @monitor_db_operation("branch_list_company")
    @monitor_cache_operation("branch_list_company")
    async def get_company_branches(company_id: str) -> List[CompanyBranch]:
        cache_key = CompanyRepository._get_company_branches_cache_key(company_id)
        cached_data = await CompanyRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for company branches: {company_id}")
            branches = [CompanyBranch.model_validate(item) for item in cached_data]
            for branch in branches:
                setattr(branch, '_from_cache', True)
            return branches
        
        try:
            branches = await CompanyBranch.find({
                "company_id": ObjectId(company_id),
                "is_active": True
            }).sort("created_at").to_list()
            
            if branches:
                await CompanyRepository._set_cache(
                    cache_key, 
                    [branch.dict() for branch in branches],
                    CompanyRepository.BRANCH_CACHE_TTL
                )
                logger.debug(f"Cache set for company branches: {company_id}")
            
            return branches
            
        except Exception as e:
            logger.error(f"Error getting branches for company {company_id}: {e}")
            return []
    
    @staticmethod
    @monitor_db_operation("company_search")
    async def search_companies(
        search_term: Optional[str] = None,
        industry: Optional[str] = None,
        location: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[Company], int]:
        try:
            query = {"is_active": True}
            
            if search_term:
                query["$or"] = [
                    {"name": {"$regex": search_term, "$options": "i"}},
                    {"description": {"$regex": search_term, "$options": "i"}},
                ]
            
            if industry:
                query["industry"] = {"$regex": industry, "$options": "i"}
            
            if location:
                query["$or"] = [
                    {"city": {"$regex": location, "$options": "i"}},
                    {"country": {"$regex": location, "$options": "i"}},
                ]
            
            cursor = Company.find(query)
            total = await cursor.count()
            
            companies = await cursor.sort([("created_at", -1)]) \
                                   .skip(skip) \
                                   .limit(limit) \
                                   .to_list()
            
            return companies, total
            
        except Exception as e:
            logger.error(f"Error searching companies: {e}", exc_info=True)
            return [], 0
    
    @staticmethod
    @monitor_db_operation("company_add_member")
    async def add_company_member(
        company_id: str,
        user_id: str,
        role: str = "member",
        permissions: Optional[List[str]] = None,
        added_by: str = None
    ) -> bool:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                raise ValueError(f"Company with ID {company_id} does not exist")
            
            if added_by:
                can_add = False
                for member in company.members:
                    if str(member["user_id"]) == added_by:
                        if "manage_members" in member.get("permissions", []) or member["role"] == "owner":
                            can_add = True
                        break
                
                if not can_add:
                    raise ValueError("User does not have permission to add members")
            
            for member in company.members:
                if str(member["user_id"]) == user_id:
                    raise ValueError("User is already a member of this company")
            
            new_member = {
                "user_id": ObjectId(user_id),
                "role": role,
                "permissions": permissions or ["view"],
                "joined_at": now_utc(),
                "added_by": ObjectId(added_by) if added_by else None
            }
            
            company.members.append(new_member)
            company.updated_at = now_utc()
            await company.save()
            
            await CompanyRepository._delete_cache(CompanyRepository._get_user_companies_cache_key(user_id))
            await CompanyRepository._delete_cache(CompanyRepository._get_user_branches_cache_key(user_id))
            
            logger.info(f"Member added to company: user={user_id}, company={company_id}")
            return True
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error adding member to company: {e}", exc_info=True)
            return False
    
    @staticmethod
    @monitor_db_operation("company_remove_member")
    async def remove_company_member(
        company_id: str,
        user_id: str,
        removed_by: str
    ) -> bool:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                raise ValueError(f"Company with ID {company_id} does not exist")
            can_remove = False
            is_owner_removing = False
            
            for member in company.members:
                if str(member["user_id"]) == removed_by:
                    if "manage_members" in member.get("permissions", []) or member["role"] == "owner":
                        can_remove = True
                    if member["role"] == "owner" and str(member["user_id"]) == user_id:
                        is_owner_removing = True
                    break
            
            if not can_remove:
                raise ValueError("User does not have permission to remove members")
            
            if is_owner_removing:
                owner_count = sum(1 for m in company.members if m["role"] == "owner" and str(m["user_id"]) != user_id)
                if owner_count == 0:
                    raise ValueError("Cannot remove the only owner of the company")
            
            original_length = len(company.members)
            company.members = [m for m in company.members if str(m["user_id"]) != user_id]
            
            if len(company.members) == original_length:
                raise ValueError("User is not a member of this company")
            
            company.updated_at = now_utc()
            await company.save()
            
            await CompanyRepository._delete_cache(CompanyRepository._get_user_companies_cache_key(user_id))
            await CompanyRepository._delete_cache(CompanyRepository._get_user_branches_cache_key(user_id))
            
            logger.info(f"Member removed from company: user={user_id}, company={company_id}")
            return True
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error removing member from company: {e}", exc_info=True)
            return False
    
    @staticmethod
    @monitor_db_operation("company_update_member")
    async def update_company_member(
        company_id: str,
        user_id: str,
        role: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        updated_by: str = None
    ) -> bool:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                raise ValueError(f"Company with ID {company_id} does not exist")
            
            if updated_by:
                can_update = False
                for member in company.members:
                    if str(member["user_id"]) == updated_by:
                        if "manage_members" in member.get("permissions", []) or member["role"] == "owner":
                            can_update = True
                        break
                
                if not can_update:
                    raise ValueError("User does not have permission to update members")
            
            member_found = False
            for member in company.members:
                if str(member["user_id"]) == user_id:
                    if role:
                        if member["role"] == "owner" and role != "owner":
                            owner_count = sum(1 for m in company.members if m["role"] == "owner")
                            if owner_count <= 1:
                                raise ValueError("Cannot change the only owner's role")
                        member["role"] = role
                    
                    if permissions is not None:
                        member["permissions"] = permissions
                    
                    member_found = True
                    break
            
            if not member_found:
                raise ValueError("User is not a member of this company")
            
            company.updated_at = now_utc()
            await company.save()
            
            await CompanyRepository._delete_cache(CompanyRepository._get_user_companies_cache_key(user_id))
            await CompanyRepository._delete_cache(CompanyRepository._get_user_branches_cache_key(user_id))
            
            logger.info(f"Member updated in company: user={user_id}, company={company_id}")
            return True
            
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error updating company member: {e}", exc_info=True)
            return False
    
    @staticmethod
    @monitor_db_operation("company_validate_user_access")
    @monitor_cache_operation("company_validate_user_access")
    async def validate_user_access(
        user_id: str,
        company_branch_id: str
    ) -> bool:
        cache_key = CompanyRepository._get_user_branch_access_cache_key(user_id, company_branch_id)
        cached_data = await CompanyRepository._get_from_cache(cache_key)
        
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
                    result = any(str(member["user_id"]) == user_id for member in company.members)
            
            await CompanyRepository._set_cache(cache_key, result, 300)
            
            return result
            
        except Exception as e:
            logger.error(f"Error validating user access: {e}")
            return False
    
    @staticmethod
    @monitor_db_operation("company_get_user_role")
    async def get_user_company_role(user_id: str, company_id: str) -> Optional[str]:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                return None
            
            for member in company.members:
                if str(member["user_id"]) == user_id:
                    return member["role"]
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting user role: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("company_stats")
    async def get_company_statistics(company_id: str) -> Dict[str, Any]:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                raise ValueError("Company not found")
            
            branch_count = await CompanyBranch.find({
                "company_id": ObjectId(company_id),
                "is_active": True
            }).count()
            
            member_stats = {
                "total": len(company.members),
                "owners": sum(1 for m in company.members if m["role"] == "owner"),
                "admins": sum(1 for m in company.members if "admin" in m.get("permissions", [])),
                "managers": sum(1 for m in company.members if "manage_members" in m.get("permissions", [])),
                "members": sum(1 for m in company.members if m["role"] == "member")
            }
            
            avg_members_per_branch = member_stats["total"] / branch_count if branch_count > 0 else 0
            
            stats = {
                "company_id": company_id,
                "company_name": company.name,
                "branch_count": branch_count,
                "member_stats": member_stats,
                "avg_members_per_branch": round(avg_members_per_branch, 2),
                "company_created": company.created_at.isoformat(),
                "last_updated": company.updated_at.isoformat(),
                "calculated_at": datetime.now().isoformat()
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting company statistics: {e}")
            return {
                "company_id": company_id,
                "error": str(e),
                "calculated_at": datetime.now().isoformat()
            }
    
    
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
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            import json
            await redis_client.setex(key, ttl or CompanyRepository.COMPANY_CACHE_TTL, 
                                   json.dumps(data, default=str))
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
    async def _invalidate_company_caches(company: Company) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            patterns = [
                f"{CompanyRepository.CACHE_PREFIX}company:{company.id}",
                f"{CompanyRepository.CACHE_PREFIX}company_branches:{company.id}",
            ]
            
            for member in company.members:
                user_id = str(member["user_id"])
                patterns.append(f"{CompanyRepository.CACHE_PREFIX}user_companies:{user_id}")
                patterns.append(f"{CompanyRepository.CACHE_PREFIX}user_branches:{user_id}")
            
            import asyncio
            delete_tasks = []
            for pattern in patterns:
                keys = await redis_client.keys(pattern)
                if keys:
                    delete_tasks.append(redis_client.delete(*keys))
            
            if delete_tasks:
                await asyncio.gather(*delete_tasks, return_exceptions=True)
                logger.debug(f"Invalidated caches for company: {company.id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating company caches for {company.id}: {e}")
    
    @staticmethod
    async def _invalidate_branch_caches(branch: CompanyBranch) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            
            patterns = [
                f"{CompanyRepository.CACHE_PREFIX}branch:{branch.id}",
                f"{CompanyRepository.CACHE_PREFIX}company_branches:{branch.company_id}",
            ]
            
            company = await Company.get(branch.company_id)
            if company:
                for member in company.members:
                    user_id = str(member["user_id"])
                    patterns.append(f"{CompanyRepository.CACHE_PREFIX}user_branches:{user_id}")
                    patterns.append(f"{CompanyRepository.CACHE_PREFIX}user_access:{user_id}:{branch.id}")
            
            import asyncio
            delete_tasks = []
            for pattern in patterns:
                keys = await redis_client.keys(pattern)
                if keys:
                    delete_tasks.append(redis_client.delete(*keys))
            
            if delete_tasks:
                await asyncio.gather(*delete_tasks, return_exceptions=True)
                logger.debug(f"Invalidated caches for branch: {branch.id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating branch caches for {branch.id}: {e}")
    
    @staticmethod
    async def clear_all_cache() -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            pattern = f"{CompanyRepository.CACHE_PREFIX}*"
            keys = await redis_client.keys(pattern)
            
            if keys:
                await redis_client.delete(*keys)
                logger.info(f"Cleared all company cache ({len(keys)} keys)")
            
        except Exception as e:
            logger.warning(f"Error clearing company cache: {e}")