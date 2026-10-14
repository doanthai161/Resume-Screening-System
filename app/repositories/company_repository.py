from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from motor.motor_asyncio import AsyncIOMotorCursor
import logging
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user import User
from app.models.user_company import UserCompany
from app.schemas.company import (
    CompanyCreate,
    CompanyUpdate,
)
from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchUpdate
from app.core.redis import get_redis, is_redis_available
from app.core import cache as shared_cache
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_utc
from app.core.errors import CustomError, ErrorCodes
from app.core.tenant_policy import active_membership_filter
from app.core.transactions import current_session

logger = logging.getLogger(__name__)


def restore_cached_document(data: Any, document_model):
    """Restore BSON identity fields before validating cached business metadata."""
    if not isinstance(data, dict):
        raise ValueError("Cached document must be an object")
    data = dict(data)
    for key in ("_id", "id", "user_id", "company_id", "created_by", "updated_by"):
        if isinstance(data.get(key), str):
            if not ObjectId.is_valid(data[key]):
                raise ValueError("Invalid cached document identity")
            data[key] = ObjectId(data[key])
    return document_model.model_validate(data)


class CompanyRepository:
    CACHE_PREFIX = shared_cache.cache_key("company") + ":"
    COMPANY_CACHE_TTL = 3600
    BRANCH_CACHE_TTL = 3600
    USER_COMPANY_CACHE_TTL = 1800

    @staticmethod
    async def accessible_company_filter(user_id: str) -> dict:
        """Authorize from current DB state; cached company lists never grant access."""
        if not ObjectId.is_valid(user_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid user ID", 422)
        user_id_obj = ObjectId(user_id)
        caller = await User.get(user_id_obj, session=current_session())
        if not caller or not caller.is_active:
            raise CustomError(ErrorCodes.FORBIDDEN, "Active user required", 403)
        if caller.is_superuser:
            return {"is_active": True}
        assignments = await UserCompany.find(
            {"user_id": user_id_obj, **active_membership_filter()}, session=current_session()
        ).to_list()
        branches = await CompanyBranch.find(
            {"_id": {"$in": [item.company_branch_id for item in assignments]}, "is_active": True},
            session=current_session(),
        ).to_list() if assignments else []
        return {"is_active": True, "$or": [
            {"user_id": user_id_obj}, {"_id": {"$in": list({item.company_id for item in branches})}},
        ]}

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
            company_dict["user_id"] = owner_id_obj
            company_dict["created_at"] = now_utc()
            company_dict["updated_at"] = now_utc()

            company = Company(**company_dict)
            await company.insert()

            await CompanyRepository._delete_cache(
                CompanyRepository._get_user_companies_cache_key(owner_id)
            )

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
        page: int = 1, size: int = 10
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
            try:
                company = restore_cached_document(cached_data, Company)
                if str(company.id) != company_id:
                    raise ValueError("Cached company identity mismatch")
                return company
            except (ValueError, TypeError):
                await CompanyRepository._delete_cache(cache_key)

        try:
            company = await Company.get(ObjectId(company_id))
            if company:
                await CompanyRepository._set_cache(
                    cache_key, company.dict(), CompanyRepository.COMPANY_CACHE_TTL
                )
                logger.debug(f"Cache set for company: {company_id}")
            return company
        except Exception as e:
            logger.error(f"Error getting company {company_id}: {e}")
            return None

    @staticmethod
    @monitor_db_operation("company_update")
    async def update_company(
        company_id: str, update_data: CompanyUpdate
    ) -> Optional[Company]:
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

            if str(company.user_id) != user_id:
                raise ValueError("Only the owner can delete the company")

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
        company_id: str, branch_data: CompanyBranchCreate, created_by: str
    ) -> CompanyBranch:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company:
                raise ValueError(f"Company with ID {company_id} does not exist")

            role = await CompanyRepository.get_user_company_role(created_by, company_id)
            if role not in ["owner", "admin"]:
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
            try:
                branch = restore_cached_document(cached_data, CompanyBranch)
                if str(branch.id) != branch_id:
                    raise ValueError("Cached branch identity mismatch")
                return branch
            except (ValueError, TypeError):
                await CompanyRepository._delete_cache(cache_key)

        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if branch:
                await CompanyRepository._set_cache(
                    cache_key, branch.dict(), CompanyRepository.BRANCH_CACHE_TTL
                )
                logger.debug(f"Cache set for branch: {branch_id}")
            return branch
        except Exception as e:
            logger.error(f"Error getting branch {branch_id}: {e}")
            return None

    @staticmethod
    @monitor_db_operation("branch_update")
    async def update_company_branch(
        branch_id: str, update_data: CompanyBranchUpdate, user_id: str
    ) -> Optional[CompanyBranch]:
        try:
            branch = await CompanyBranch.get(ObjectId(branch_id))
            if not branch:
                return None

            company = await Company.get(branch.company_id)
            if not company:
                raise ValueError("Company not found")

            role = await CompanyRepository.get_user_company_role(
                user_id, str(company.id)
            )
            if role not in ["owner", "admin"]:
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

            role = await CompanyRepository.get_user_company_role(
                user_id, str(company.id)
            )
            if role not in ["owner", "admin"]:
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
        query = await CompanyRepository.accessible_company_filter(user_id)
        return await Company.find(query, session=current_session()).sort(
            [("created_at", -1), ("_id", -1)]
        ).to_list()

    @staticmethod
    @monitor_db_operation("branch_list_user")
    @monitor_cache_operation("branch_list_user")
    async def get_user_company_branches(user_id: str) -> List[CompanyBranch]:
        companies = await CompanyRepository.get_user_companies(user_id)
        caller = await User.get(ObjectId(user_id), session=current_session())
        if not caller or not caller.is_active:
            raise CustomError(ErrorCodes.FORBIDDEN, "Active user required", 403)
        query = {"company_id": {"$in": [item.id for item in companies]}, "is_active": True}
        if not caller.is_superuser:
            links = await UserCompany.find(
                {"user_id": caller.id, **active_membership_filter()}, session=current_session()
            ).to_list()
            owned = [item.id for item in companies if item.user_id == caller.id]
            query["$or"] = [{"company_id": {"$in": owned}},
                            {"_id": {"$in": [item.company_branch_id for item in links]}}]
        return await CompanyBranch.find(query, session=current_session()).sort(
            [("branch_name", 1), ("_id", 1)]
        ).to_list()

    @staticmethod
    @monitor_db_operation("branch_list_company")
    @monitor_cache_operation("branch_list_company")
    async def get_company_branches(company_id: str) -> List[CompanyBranch]:
        cache_key = CompanyRepository._get_company_branches_cache_key(company_id)
        cached_data = await CompanyRepository._get_from_cache(cache_key)

        if cached_data:
            try:
                if not isinstance(cached_data, list):
                    raise ValueError("Cached branches must be a list")
                branches = [restore_cached_document(item, CompanyBranch) for item in cached_data]
                if any(str(item.company_id) != company_id for item in branches):
                    raise ValueError("Cached branches do not belong to company")
                return branches
            except (ValueError, TypeError):
                await CompanyRepository._delete_cache(cache_key)

        try:
            branches = (
                await CompanyBranch.find(
                    {"company_id": ObjectId(company_id), "is_active": True}
                )
                .sort("created_at")
                .to_list()
            )

            if branches:
                await CompanyRepository._set_cache(
                    cache_key,
                    [branch.dict() for branch in branches],
                    CompanyRepository.BRANCH_CACHE_TTL,
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
        limit: int = 20,
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

            companies = (
                await cursor.sort([("created_at", -1)])
                .skip(skip)
                .limit(limit)
                .to_list()
            )

            return companies, total

        except Exception as e:
            logger.error(f"Error searching companies: {e}", exc_info=True)
            return [], 0

    @staticmethod
    @monitor_db_operation("company_validate_user_access")
    @monitor_cache_operation("company_validate_user_access")
    async def validate_user_access(user_id: str, company_branch_id: str) -> bool:
        from app.core.tenant_policy import branch_role

        return await branch_role(user_id, company_branch_id) is not None

    @staticmethod
    @monitor_db_operation("company_get_user_role")
    async def get_user_company_role(user_id: str, company_id: str) -> Optional[str]:
        try:
            company = await Company.get(ObjectId(company_id))
            if not company or not company.is_active:
                return None

            from app.models.user import User

            caller = await User.get(ObjectId(user_id))
            if not caller or not caller.is_active:
                return None
            if caller.is_superuser or str(company.user_id) == user_id:
                return "owner"

            from app.repositories.user_company_repository import UserCompanyRepository

            branches = await CompanyBranch.find(
                {
                    "company_id": company.id,
                    "is_active": True,
                }
            ).to_list()
            roles = []
            for branch in branches:
                role = await UserCompanyRepository.get_user_role_in_branch(
                    user_id=user_id, company_branch_id=str(branch.id)
                )
                if role in {"member", "manager", "admin", "owner"}:
                    roles.append(role)
            ranks = {"member": 1, "manager": 2, "admin": 3, "owner": 4}
            return max(roles, key=ranks.get) if roles else None

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

            branches = await CompanyBranch.find(
                {"company_id": ObjectId(company_id), "is_active": True}
            ).to_list()
            branch_count = len(branches)

            assignments = await UserCompany.find(
                {
                    "company_branch_id": {"$in": [b.id for b in branches]},
                    "is_active": True,
                }
            ).to_list()

            distinct_members = {str(a.user_id) for a in assignments}
            member_stats = {
                "total": len(distinct_members) + 1,  # +1 for the company owner
                "owners": 1,
                "admins": sum(1 for a in assignments if a.role == "admin"),
                "managers": sum(1 for a in assignments if a.role == "manager"),
                "members": sum(
                    1 for a in assignments if a.role not in ("admin", "manager")
                ),
            }

            avg_members_per_branch = (
                member_stats["total"] / branch_count if branch_count > 0 else 0
            )

            stats = {
                "company_id": company_id,
                "company_name": company.name,
                "branch_count": branch_count,
                "member_stats": member_stats,
                "avg_members_per_branch": round(avg_members_per_branch, 2),
                "company_created": company.created_at.isoformat(),
                "last_updated": company.updated_at.isoformat(),
                "calculated_at": datetime.now().isoformat(),
            }

            return stats

        except Exception as e:
            logger.error(f"Error getting company statistics: {e}")
            return {
                "company_id": company_id,
                "error": str(e),
                "calculated_at": datetime.now().isoformat(),
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

            await redis_client.setex(
                key,
                ttl or CompanyRepository.COMPANY_CACHE_TTL,
                json.dumps(data, default=str),
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
    async def _invalidate_company_caches(company: Company) -> None:
        if not is_redis_available():
            return

        try:
            patterns = [
                f"{CompanyRepository.CACHE_PREFIX}company:{company.id}",
                f"{CompanyRepository.CACHE_PREFIX}company_branches:{company.id}",
                f"{CompanyRepository.CACHE_PREFIX}user_companies:*",
                f"{CompanyRepository.CACHE_PREFIX}user_branches:*",
            ]

            for pattern in patterns:
                await shared_cache.delete_pattern(pattern)
            logger.debug(f"Invalidated caches for company: {company.id}")

        except Exception as e:
            logger.warning(f"Error invalidating company caches for {company.id}: {e}")

    @staticmethod
    async def _invalidate_branch_caches(branch: CompanyBranch) -> None:
        if not is_redis_available():
            return

        try:
            patterns = [
                f"{CompanyRepository.CACHE_PREFIX}branch:{branch.id}",
                f"{CompanyRepository.CACHE_PREFIX}company_branches:{branch.company_id}",
                f"{CompanyRepository.CACHE_PREFIX}user_branches:*",
                f"{CompanyRepository.CACHE_PREFIX}user_access:*:{branch.id}",
            ]

            for pattern in patterns:
                await shared_cache.delete_pattern(pattern)
            logger.debug(f"Invalidated caches for branch: {branch.id}")

        except Exception as e:
            logger.warning(f"Error invalidating branch caches for {branch.id}: {e}")

    @staticmethod
    async def clear_all_cache() -> None:
        if not is_redis_available():
            return

        try:
            pattern = f"{CompanyRepository.CACHE_PREFIX}*"
            await shared_cache.delete_pattern(pattern)
            logger.info("Cleared all company cache")

        except Exception as e:
            logger.warning(f"Error clearing company cache: {e}")
