from typing import List, Optional, Dict, Any, Tuple, Union
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import logging
import json
import asyncio
from functools import wraps
from app.models.company_branch import CompanyBranch
from app.models.company import Company
from app.models.user_company import UserCompany
from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchUpdate
from app.core.redis import get_redis, is_redis_available
from app.core import cache as shared_cache
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_utc
from beanie.exceptions import RevisionIdWasChanged

logger = logging.getLogger(__name__)


class CompanyBranchRepository:
    CACHE_PREFIX = shared_cache.cache_key("company-branch") + ":"
    BRANCH_CACHE_TTL = 3600
    BRANCH_LIST_CACHE_TTL = 300
    USER_BRANCHES_CACHE_TTL = 1800
    PERMISSION_CACHE_TTL = 300
    STATS_CACHE_TTL = 600
    NULL_CACHE_VALUE = "__NULL__"
    NULL_CACHE_TTL = 60

    @staticmethod
    def _get_cache_key(*parts: str) -> str:
        return f"{CompanyBranchRepository.CACHE_PREFIX}{':'.join(str(p) for p in parts)}"

    @staticmethod
    def _get_branch_key(branch_id: str) -> str:
        return CompanyBranchRepository._get_cache_key("branch", branch_id)

    @staticmethod
    def _get_branch_stats_key(branch_id: str) -> str:
        return CompanyBranchRepository._get_cache_key("stats", branch_id)

    @staticmethod
    def _get_company_branches_key(company_id: str, active_only: bool = True) -> str:
        status = "active" if active_only else "all"
        return CompanyBranchRepository._get_cache_key("company", company_id, "branches", status)

    @staticmethod
    def _get_user_branches_key(user_id: str, active_only: bool = True) -> str:
        status = "active" if active_only else "all"
        return CompanyBranchRepository._get_cache_key("user", user_id, "branches", status)

    @staticmethod
    def _get_permissions_key(user_id: str, branch_id: str) -> str:
        return CompanyBranchRepository._get_cache_key("perms", user_id, branch_id)

    @staticmethod
    def _get_company_stats_key(company_id: str) -> str:
        return CompanyBranchRepository._get_cache_key("company", company_id, "stats")

    @staticmethod
    def _get_search_key(search_term: str, filters: Dict[str, Any], skip: int, limit: int) -> str:
        filter_hash = hash(frozenset(filters.items()))
        return CompanyBranchRepository._get_cache_key(
            "search", search_term, filter_hash, skip, limit
        )

    @staticmethod
    def cache_result(ttl: int = 300, key_func=None):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                if not is_redis_available():
                    return await func(*args, **kwargs)

                cache_key = key_func(*args, **kwargs) if key_func else None
                if cache_key is None:
                    func_name = func.__name__
                    arg_str = str(args) + str(kwargs)
                    cache_key = CompanyBranchRepository._get_cache_key(
                        "func", func_name, hash(arg_str)
                    )

                cached = await CompanyBranchRepository._get_cached(cache_key)
                if cached is not None:
                    logger.debug(f"Cache hit for {func.__name__}")
                    return cached

                result = await func(*args, **kwargs)

                if result is not None:
                    await CompanyBranchRepository._set_cache(
                        cache_key, result, ttl
                    )

                return result
            return wrapper
        return decorator

    @staticmethod
    async def _get_cached(key: str) -> Optional[Any]:
        if not is_redis_available():
            return None

        try:
            redis_client = get_redis()
            cached = await redis_client.get(key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.debug(f"Cache get error for key {key}: {e}")
        return None

    @staticmethod
    async def _set_cache(key: str, data: Any, ttl: Optional[int] = None) -> None:
        if not is_redis_available():
            return
        try:
            redis_client = get_redis()

            if data is None:
                value_to_store = CompanyBranchRepository.NULL_CACHE_VALUE
                effective_ttl = CompanyBranchRepository.NULL_CACHE_TTL
            elif isinstance(data, str):
                value_to_store = data
                effective_ttl = ttl
            else:
                value_to_store = json.dumps(data, default=str)
                effective_ttl = ttl

            await redis_client.setex(
                key,
                effective_ttl or CompanyBranchRepository.BRANCH_CACHE_TTL,
                value_to_store
            )
        except Exception as e:
            logger.warning(f"Cache set error for key {key}: {e}")

    @staticmethod
    async def _delete_cached(*keys: str) -> None:
        if not is_redis_available() or not keys:
            return

        try:
            redis_client = get_redis()
            await redis_client.delete(*keys)
        except Exception as e:
            logger.debug(f"Cache delete error: {e}")

    @staticmethod
    async def _invalidate_pattern(pattern: str) -> None:
        if not is_redis_available():
            return

        try:
            await shared_cache.delete_pattern(pattern)
        except Exception as e:
            logger.debug(f"Pattern delete error: {e}")

    @staticmethod
    def _branch_base_pipeline() -> List[Dict]:
        return [
            {
                "$lookup": {
                    "from": "companies",
                    "localField": "company_id",
                    "foreignField": "_id",
                    "as": "company"
                }
            },
            {
                "$unwind": {
                    "path": "$company",
                    "preserveNullAndEmptyArrays": False
                }
            }
        ]

    BRANCH_FIELDS = [
        "_id", "company_id", "bussiness_type", "branch_name", "phone_number",
        "address", "city", "description", "company_type", "company_industry",
        "country", "company_size", "working_days", "overtime_policy",
        "is_active", "created_by", "updated_by", "created_at", "updated_at",
    ]

    @staticmethod
    def _branch_project_stage() -> Dict:
        return {"$project": {field: 1 for field in CompanyBranchRepository.BRANCH_FIELDS}}

    @staticmethod
    async def _aggregate_branch(
        pipeline: List[Dict],
        return_model: bool = True
    ) -> Optional[Union[Dict, CompanyBranch]]:
        try:
            result = await CompanyBranch.aggregate(pipeline).to_list(length=1)
            if not result:
                return None

            data = result[0]
            if return_model:
                return CompanyBranch.model_validate(data)

            return data
        except Exception as e:
            logger.error(f"Aggregation error: {e}", exc_info=True)
            return None

    @staticmethod
    @monitor_db_operation("branch_create")
    async def create_company_branch(
        company_id: str,
        branch_data: CompanyBranchCreate,
        created_by_id: str
    ) -> CompanyBranch:
        try:
            branch_dict = branch_data.model_dump()
            branch_dict["company_id"] = ObjectId(company_id)
            branch_dict["created_by"] = ObjectId(created_by_id)
            branch_dict["is_active"] = True
            branch_dict["created_at"] = now_utc()
            branch_dict["updated_at"] = now_utc()

            company = await Company.find_one({"_id": ObjectId(company_id)})
            if not company or not company.is_active:
                raise ValueError(f"Company with id {company_id} not found.")

            branch = CompanyBranch(**branch_dict)
            await branch.insert()

            await CompanyBranchRepository._invalidate_branch_creation(branch)

            logger.info(f"Company branch created: {branch.id} for company {company_id}")
            return branch

        except DuplicateKeyError as e:
            logger.error(f"Duplicate key error while creating branch: {e}")
            raise ValueError("A branch with these details already exists.")
        except RevisionIdWasChanged as e:
            logger.error(f"Concurrency conflict while updating company: {e}")
            raise ValueError("The company data was modified by another process. Please try again.")
        except Exception as e:
            logger.error(f"Error creating branch in repository: {e}", exc_info=True)
            raise

    @staticmethod
    @monitor_db_operation("branch_get")
    @monitor_cache_operation("branch_get")
    @cache_result(ttl=BRANCH_CACHE_TTL, key_func=lambda branch_id:
                  CompanyBranchRepository._get_branch_key(branch_id))
    async def get_company_branch(branch_id: str) -> Optional[CompanyBranch]:
        pipeline = [
            {
                "$match": {
                    "_id": ObjectId(branch_id)
                }
            },
            *CompanyBranchRepository._branch_base_pipeline(),
            CompanyBranchRepository._branch_project_stage()
        ]

        return await CompanyBranchRepository._aggregate_branch(pipeline)

    @staticmethod
    @monitor_db_operation("branch_update")
    async def update_company_branch(
        branch_id: str,
        update_data: CompanyBranchUpdate,
        updated_by: str
    ) -> Optional[CompanyBranch]:
        try:
            from app.repositories.company_repository import CompanyRepository

            branch = await CompanyBranch.get(ObjectId(branch_id))
            if not branch or not branch.is_active:
                return None

            role = await CompanyRepository.get_user_company_role(updated_by, str(branch.company_id))
            if role not in ["owner", "admin"]:
                raise ValueError("User does not have permission to update this branch")

            update_dict = update_data.model_dump(exclude_unset=True)
            update_dict["updated_by"] = ObjectId(updated_by)
            update_dict["updated_at"] = now_utc()

            await CompanyBranch.find_one({"_id": ObjectId(branch_id)}).update({
                "$set": update_dict
            })

            updated_branch = await CompanyBranchRepository.get_company_branch(branch_id)

            await CompanyBranchRepository._invalidate_branch_update(updated_branch)

            return updated_branch

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error updating branch: {e}")
            raise

    @staticmethod
    @monitor_db_operation("branch_get_permissions")
    @monitor_cache_operation("branch_get_permissions")
    @cache_result(ttl=PERMISSION_CACHE_TTL, key_func=lambda user_id, branch_id:
                  CompanyBranchRepository._get_permissions_key(user_id, branch_id))
    async def get_user_branch_permissions(
        user_id: str,
        branch_id: str
    ) -> Dict[str, Any]:
        from app.repositories.user_company_repository import UserCompanyRepository
        from app.repositories.company_repository import CompanyRepository

        branch = await CompanyBranch.get(ObjectId(branch_id))
        if not branch or not branch.is_active:
            return {"has_access": False, "reason": "Branch not found"}

        company = await Company.get(branch.company_id)
        if not company or not company.is_active:
            return {"has_access": False, "reason": "Company not found"}

        if str(company.user_id) == user_id:
            role = "owner"
            permissions: List[str] = []
        else:
            role = await UserCompanyRepository.get_user_role_in_branch(user_id, branch_id)
            permissions = await UserCompanyRepository.get_user_permissions_in_branch(user_id, branch_id)

        if not role:
            return {"has_access": False, "reason": "User not a member"}

        can_manage = role in ["owner", "admin"] or "manage_branches" in permissions

        return {
            "has_access": True,
            "role": role,
            "company_permissions": permissions,
            "branch_permissions": permissions,
            "can_manage_branch": can_manage,
            "can_view_branch": True,
            "can_edit_branch": can_manage,
            "can_delete_branch": role == "owner",
            "user_id": user_id,
            "branch_id": branch_id
        }

    @staticmethod
    @monitor_db_operation("branch_list_company")
    @monitor_cache_operation("branch_list_company")
    @cache_result(ttl=BRANCH_LIST_CACHE_TTL, key_func=lambda company_id, active_only=True:
                  CompanyBranchRepository._get_company_branches_key(company_id, active_only))
    async def get_company_branches(
        company_id: str,
        active_only: bool = True
    ) -> List[CompanyBranch]:
        query: Dict[str, Any] = {"company_id": ObjectId(company_id)}
        if active_only:
            query["is_active"] = True

        try:
            branches = await CompanyBranch.find(query).sort("branch_name").to_list()
            return branches
        except Exception as e:
            logger.error(f"Error getting company branches: {e}")
            return []

    @staticmethod
    @monitor_db_operation("branch_list_user")
    @monitor_cache_operation("branch_list_user")
    @cache_result(ttl=USER_BRANCHES_CACHE_TTL, key_func=lambda user_id, active_only=True:
                  CompanyBranchRepository._get_user_branches_key(user_id, active_only))
    async def get_user_company_branches(
        user_id: str,
        active_only: bool = True
    ) -> List[CompanyBranch]:
        try:
            owned_companies = await Company.find({"user_id": ObjectId(user_id)}).to_list()
            owned_company_ids = [c.id for c in owned_companies]

            assignments = await UserCompany.find({
                "user_id": ObjectId(user_id),
                "is_active": True
            }).to_list()
            assigned_branch_ids = [a.company_branch_id for a in assignments]

            query: Dict[str, Any] = {
                "$or": [
                    {"company_id": {"$in": owned_company_ids}},
                    {"_id": {"$in": assigned_branch_ids}}
                ]
            }
            if active_only:
                query["is_active"] = True

            branches = await CompanyBranch.find(query).sort("branch_name").to_list()
            return branches
        except Exception as e:
            logger.error(f"Error getting user branches: {e}")
            return []

    @staticmethod
    @monitor_db_operation("branch_search")
    @monitor_cache_operation("branch_search")
    async def search_branches(
        search_term: Optional[str] = None,
        company_id: Optional[str] = None,
        city: Optional[str] = None,
        country: Optional[str] = None,
        is_active: bool = True,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[CompanyBranch], int]:
        match_stage: Dict[str, Any] = {"is_active": is_active}

        if company_id:
            match_stage["company_id"] = ObjectId(company_id)

        if city:
            match_stage["city"] = {"$regex": city, "$options": "i"}

        if country:
            match_stage["country"] = {"$regex": country, "$options": "i"}

        if search_term:
            match_stage["$or"] = [
                {"branch_name": {"$regex": search_term, "$options": "i"}},
                {"description": {"$regex": search_term, "$options": "i"}},
                {"address": {"$regex": search_term, "$options": "i"}},
            ]

        try:
            total = await CompanyBranch.find(match_stage).count()
            branches = await CompanyBranch.find(match_stage) \
                .sort("branch_name") \
                .skip(skip) \
                .limit(limit) \
                .to_list()

            return branches, total
        except Exception as e:
            logger.error(f"Error searching branches: {e}")
            return [], 0

    @staticmethod
    @monitor_db_operation("branch_get_statistics")
    @monitor_cache_operation("branch_get_statistics")
    @cache_result(ttl=STATS_CACHE_TTL, key_func=lambda branch_id:
                  CompanyBranchRepository._get_branch_stats_key(branch_id))
    async def get_branch_statistics(branch_id: str) -> Dict[str, Any]:
        pipeline = [
            {
                "$match": {
                    "_id": ObjectId(branch_id)
                }
            },
            *CompanyBranchRepository._branch_base_pipeline(),
            {
                "$lookup": {
                    "from": "users",
                    "localField": "created_by",
                    "foreignField": "_id",
                    "as": "creator"
                }
            },
            {
                "$unwind": {
                    "path": "$creator",
                    "preserveNullAndEmptyArrays": True
                }
            },
            {
                "$project": {
                    "branch_id": {"$toString": "$_id"},
                    "branch_name": "$branch_name",
                    "company_id": {"$toString": "$company_id"},
                    "company_name": "$company.name",
                    "is_active": "$is_active",
                    "address": "$address",
                    "city": "$city",
                    "country": "$country",
                    "contact_phone": "$phone_number",
                    "created_at": "$created_at",
                    "updated_at": "$updated_at",
                    "created_by_name": "$creator.full_name",
                    "company_active": "$company.is_active",
                    "calculated_at": {"$literal": datetime.now().isoformat()}
                }
            }
        ]

        result = await CompanyBranchRepository._aggregate_branch(pipeline, return_model=False)
        return result or {"branch_id": branch_id, "error": "Branch not found"}

    @staticmethod
    @monitor_db_operation("branch_get_company_statistics")
    @monitor_cache_operation("branch_get_company_statistics")
    @cache_result(ttl=STATS_CACHE_TTL, key_func=lambda company_id:
                  CompanyBranchRepository._get_company_stats_key(company_id))
    async def get_company_branch_statistics(company_id: str) -> Dict[str, Any]:
        pipeline = [
            {
                "$match": {
                    "company_id": ObjectId(company_id),
                    "is_active": True
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_branches": {"$sum": 1},
                    "cities": {
                        "$push": {
                            "$cond": [
                                {"$ne": ["$city", None]},
                                "$city",
                                "$$REMOVE"
                            ]
                        }
                    },
                    "countries": {
                        "$push": {
                            "$cond": [
                                {"$ne": ["$country", None]},
                                "$country",
                                "$$REMOVE"
                            ]
                        }
                    },
                    "recent_branches": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        "$created_at",
                                        datetime.now() - timedelta(days=30)
                                    ]
                                },
                                1,
                                0
                            ]
                        }
                    }
                }
            },
            {
                "$project": {
                    "company_id": {"$literal": company_id},
                    "total_branches": 1,
                    "branches_by_city": {
                        "$arrayToObject": {
                            "$map": {
                                "input": {"$setUnion": ["$cities", []]},
                                "as": "city",
                                "in": {
                                    "k": "$$city",
                                    "v": {
                                        "$size": {
                                            "$filter": {
                                                "input": "$cities",
                                                "as": "c",
                                                "cond": {"$eq": ["$$c", "$$city"]}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "branches_by_country": {
                        "$arrayToObject": {
                            "$map": {
                                "input": {"$setUnion": ["$countries", []]},
                                "as": "country",
                                "in": {
                                    "k": "$$country",
                                    "v": {
                                        "$size": {
                                            "$filter": {
                                                "input": "$countries",
                                                "as": "c",
                                                "cond": {"$eq": ["$$c", "$$country"]}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "branches_created_last_30d": "$recent_branches",
                    "calculated_at": {"$literal": datetime.now().isoformat()}
                }
            }
        ]

        try:
            result = await CompanyBranch.aggregate(pipeline).to_list(length=1)
            return result[0] if result else {
                "company_id": company_id,
                "total_branches": 0,
                "branches_by_city": {},
                "branches_by_country": {},
                "branches_created_last_30d": 0,
                "calculated_at": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting company stats: {e}")
            return {"company_id": company_id, "error": str(e)}

    @staticmethod
    @monitor_db_operation("branch_bulk_update")
    async def bulk_update_branches(
        branch_ids: List[str],
        update_data: Dict[str, Any],
        updated_by: str
    ) -> Tuple[int, int]:
        try:
            from app.repositories.company_repository import CompanyRepository

            branches = await CompanyBranch.find({
                "_id": {"$in": [ObjectId(bid) for bid in branch_ids]}
            }).to_list()

            authorized_ids = []
            checked_companies: Dict[str, Optional[str]] = {}
            for branch in branches:
                company_id = str(branch.company_id)
                if company_id not in checked_companies:
                    checked_companies[company_id] = await CompanyRepository.get_user_company_role(
                        updated_by, company_id
                    )
                if checked_companies[company_id] in ["owner", "admin"]:
                    authorized_ids.append(str(branch.id))

            if not authorized_ids:
                return 0, 0

            update_dict = {
                k: v for k, v in update_data.items()
                if k not in {"_id", "company_id"}
            }
            update_dict["updated_at"] = now_utc()

            result = await CompanyBranch.find({
                "_id": {"$in": [ObjectId(bid) for bid in authorized_ids]}
            }).update_many({"$set": update_dict})

            await CompanyBranchRepository._invalidate_bulk_update(authorized_ids)

            return result.modified_count, len(authorized_ids)

        except Exception as e:
            logger.error(f"Error in bulk update: {e}")
            return 0, 0

    @staticmethod
    async def _invalidate_branch_creation(branch: CompanyBranch) -> None:
        keys_to_delete = [
            CompanyBranchRepository._get_company_branches_key(str(branch.company_id), True),
            CompanyBranchRepository._get_company_branches_key(str(branch.company_id), False),
            CompanyBranchRepository._get_company_stats_key(str(branch.company_id)),
        ]
        await CompanyBranchRepository._delete_cached(*keys_to_delete)

    @staticmethod
    async def _invalidate_branch_update(branch: CompanyBranch) -> None:
        keys_to_delete = [
            CompanyBranchRepository._get_branch_key(str(branch.id)),
            CompanyBranchRepository._get_branch_stats_key(str(branch.id)),
            CompanyBranchRepository._get_company_branches_key(str(branch.company_id), True),
            CompanyBranchRepository._get_company_branches_key(str(branch.company_id), False),
            CompanyBranchRepository._get_company_stats_key(str(branch.company_id)),
        ]

        await CompanyBranchRepository._invalidate_pattern(
            f"{CompanyBranchRepository.CACHE_PREFIX}perms:*:{branch.id}"
        )

        await CompanyBranchRepository._delete_cached(*keys_to_delete)

    @staticmethod
    async def _invalidate_bulk_update(branch_ids: List[str]) -> None:
        if not branch_ids:
            return

        branches = await CompanyBranch.find({
            "_id": {"$in": [ObjectId(bid) for bid in branch_ids]}
        }).to_list()
        company_ids = list({str(b.company_id) for b in branches})

        delete_tasks = []
        for branch_id in branch_ids:
            delete_tasks.append(
                CompanyBranchRepository._delete_cached(
                    CompanyBranchRepository._get_branch_key(branch_id),
                    CompanyBranchRepository._get_branch_stats_key(branch_id)
                )
            )

        for company_id in company_ids:
            delete_tasks.append(
                CompanyBranchRepository._delete_cached(
                    CompanyBranchRepository._get_company_branches_key(company_id, True),
                    CompanyBranchRepository._get_company_branches_key(company_id, False),
                    CompanyBranchRepository._get_company_stats_key(company_id)
                )
            )

        await asyncio.gather(*delete_tasks, return_exceptions=True)

    @staticmethod
    async def clear_all_cache() -> None:
        await CompanyBranchRepository._invalidate_pattern(
            f"{CompanyBranchRepository.CACHE_PREFIX}*"
        )
        logger.info("Cleared all company branch cache")
