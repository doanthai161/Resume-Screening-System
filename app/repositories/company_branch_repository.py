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
from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchUpdate
from app.core.redis import get_redis, is_redis_available
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_utc
from app.utils.helpers import generate_cache_key, batch_process

logger = logging.getLogger(__name__)


class CompanyBranchRepository:
    CACHE_PREFIX = "company_branch:"
    BRANCH_CACHE_TTL = 3600
    BRANCH_LIST_CACHE_TTL = 300
    USER_BRANCHES_CACHE_TTL = 1800
    PERMISSION_CACHE_TTL = 300
    STATS_CACHE_TTL = 600
    
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
    def _get_headquarters_key(company_id: str) -> str:
        return CompanyBranchRepository._get_cache_key("hq", company_id)
    
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
                    cls = args[0] if args else None
                    func_name = func.__name__
                    arg_str = str(args[1:] if cls else args) + str(kwargs)
                    cache_key = CompanyBranchRepository._get_cache_key(
                        "func", func_name, hash(arg_str)
                    )
                
                cached = await CompanyBranchRepository._get_cached(cache_key)
                if cached is not None:
                    logger.debug(f"Cache hit for {func.__name__}")
                    return cached
                
                result = await func(*args, **kwargs)
                
                if result is not None:
                    await CompanyBranchRepository._set_cached(
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
    async def _set_cached(key: str, value: Any, ttl: Optional[int] = None) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            await redis_client.setex(
                key,
                ttl or CompanyBranchRepository.BRANCH_CACHE_TTL,
                json.dumps(value, default=str)
            )
        except Exception as e:
            logger.debug(f"Cache set error for key {key}: {e}")
    
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
            redis_client = get_redis()
            keys = await redis_client.keys(pattern)
            if keys:
                await redis_client.delete(*keys)
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
    
    @staticmethod
    def _with_user_member_pipeline(user_id: str) -> List[Dict]:
        user_oid = ObjectId(user_id)
        return [
            {
                "$addFields": {
                    "user_member": {
                        "$arrayElemAt": [
                            {
                                "$filter": {
                                    "input": "$company.members",
                                    "as": "member",
                                    "cond": {
                                        "$eq": ["$$member.user_id", user_oid]
                                    }
                                }
                            },
                            0
                        ]
                    }
                }
            }
        ]
    
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
            if return_model and "_id" in data:
                branch_data = {**data}
                branch_data.pop("company", None)
                branch_data.pop("user_member", None)
                return CompanyBranch(**branch_data)
            
            return data
        except Exception as e:
            logger.error(f"Aggregation error: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("branch_create")
    async def create_company_branch(
        company_id: str,
        branch_data: CompanyBranchCreate,
        created_by: str
    ) -> CompanyBranch:
        try:
            permission_pipeline = [
                {
                    "$match": {
                        "_id": ObjectId(company_id),
                        "is_active": True
                    }
                },
                {
                    "$project": {
                        "has_permission": {
                            "$anyElementTrue": {
                                "$map": {
                                    "input": "$members",
                                    "as": "member",
                                    "in": {
                                        "$and": [
                                            {
                                                "$eq": [
                                                    "$$member.user_id",
                                                    ObjectId(created_by)
                                                ]
                                            },
                                            {
                                                "$or": [
                                                    {
                                                        "$eq": [
                                                            "$$member.role",
                                                            "owner"
                                                        ]
                                                    },
                                                    {
                                                        "$in": [
                                                            "manage_branches",
                                                            "$$member.permissions"
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                }
                            }
                        },
                        "existing_branches_count": {
                            "$size": {
                                "$ifNull": ["$branch_ids", []]
                            }
                        }
                    }
                }
            ]
            
            result = await Company.aggregate(permission_pipeline).to_list(length=1)
            if not result or not result[0].get("has_permission"):
                raise ValueError("User does not have permission to create branches")
            
            branch_dict = branch_data.model_dump()
            branch_dict["company_id"] = ObjectId(company_id)
            branch_dict["created_by"] = ObjectId(created_by)
            branch_dict["is_headquarters"] = result[0].get("existing_branches_count", 0) == 0
            branch_dict["is_active"] = True
            branch_dict["created_at"] = now_utc()
            branch_dict["updated_at"] = now_utc()
            
            branch = CompanyBranch(**branch_dict)
            await branch.insert()
            
            await Company.find_one({"_id": ObjectId(company_id)}).update({
                "$push": {"branch_ids": branch.id},
                "$set": {"updated_at": now_utc()}
            })
            
            await CompanyBranchRepository._invalidate_branch_creation(branch)
            
            logger.info(f"Company branch created: {branch.id}")
            return branch
            
        except DuplicateKeyError as e:
            logger.error(f"Duplicate key error: {e}")
            raise ValueError("Branch with similar criteria already exists")
        except Exception as e:
            logger.error(f"Error creating branch: {e}")
            raise
    
    @staticmethod
    @monitor_db_operation("branch_get")
    @monitor_cache_operation("branch_get")
    @cache_result(ttl=BRANCH_CACHE_TTL, key_func=lambda self, branch_id: 
                  CompanyBranchRepository._get_branch_key(branch_id))
    async def get_company_branch(branch_id: str) -> Optional[CompanyBranch]:
        pipeline = [
            {
                "$match": {
                    "_id": ObjectId(branch_id)
                }
            },
            *CompanyBranchRepository._branch_base_pipeline(),
            {
                "$project": {
                    "_id": 1,
                    "name": 1,
                    "description": 1,
                    "address": 1,
                    "city": 1,
                    "country": 1,
                    "email": 1,
                    "phone": 1,
                    "is_headquarters": 1,
                    "is_active": 1,
                    "company_id": 1,
                    "created_by": 1,
                    "created_at": 1,
                    "updated_at": 1,
                    "company_name": "$company.name",
                    "company_status": "$company.is_active"
                }
            }
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
            pipeline = [
                {
                    "$match": {
                        "_id": ObjectId(branch_id),
                        "is_active": True
                    }
                },
                *CompanyBranchRepository._branch_base_pipeline(),
                *CompanyBranchRepository._with_user_member_pipeline(updated_by),
                {
                    "$project": {
                        "branch": "$$ROOT",
                        "has_permission": {
                            "$or": [
                                {"$eq": ["$user_member.role", "owner"]},
                                {"$in": ["manage_branches", "$user_member.permissions"]}
                            ]
                        },
                        "current_is_headquarters": "$is_headquarters"
                    }
                }
            ]
            
            result = await CompanyBranch.aggregate(pipeline).to_list(length=1)
            if not result or not result[0].get("has_permission"):
                raise ValueError("User does not have permission to update this branch")
            
            branch_data = result[0]["branch"]
            
            if (update_data.is_headquarters is not None and 
                not update_data.is_headquarters and 
                result[0]["current_is_headquarters"]):
                
                hq_count = await CompanyBranch.find({
                    "company_id": branch_data["company_id"],
                    "is_headquarters": True,
                    "is_active": True,
                    "_id": {"$ne": ObjectId(branch_id)}
                }).count()
                
                if hq_count == 0:
                    raise ValueError("Cannot remove headquarters status from the only headquarters")
            
            update_dict = update_data.model_dump(exclude_unset=True)
            update_dict["updated_at"] = now_utc()
            
            await CompanyBranch.find_one({"_id": ObjectId(branch_id)}).update({
                "$set": update_dict
            })
            
            updated_branch = await CompanyBranchRepository.get_company_branch(branch_id)
            
            await CompanyBranchRepository._invalidate_branch_update(updated_branch)
            
            return updated_branch
            
        except Exception as e:
            logger.error(f"Error updating branch: {e}")
            raise
    
    @staticmethod
    @monitor_db_operation("branch_get_permissions")
    @monitor_cache_operation("branch_get_permissions")
    @cache_result(ttl=PERMISSION_CACHE_TTL, key_func=lambda self, user_id, branch_id: 
                  CompanyBranchRepository._get_permissions_key(user_id, branch_id))
    async def get_user_branch_permissions(
        user_id: str,
        branch_id: str
    ) -> Dict[str, Any]:
        pipeline = [
            {
                "$match": {
                    "_id": ObjectId(branch_id),
                    "is_active": True
                }
            },
            *CompanyBranchRepository._branch_base_pipeline(),
            {
                "$match": {
                    "company.is_active": True
                }
            },
            *CompanyBranchRepository._with_user_member_pipeline(user_id),
            {
                "$project": {
                    "has_access": {
                        "$cond": [
                            {"$ne": ["$user_member", None]},
                            True,
                            False
                        ]
                    },
                    "role": "$user_member.role",
                    "permissions": "$user_member.permissions",
                    "branch_is_headquarters": "$is_headquarters",
                    "user_member": 1
                }
            }
        ]
        
        result = await CompanyBranchRepository._aggregate_branch(pipeline, return_model=False)
        
        if not result or not result.get("has_access"):
            return {
                "has_access": False,
                "reason": "User not a member" if result else "Branch or company not found"
            }
        
        role = result.get("role", "")
        permissions = result.get("permissions", [])
        can_manage = role == "owner" or "manage_branches" in permissions
        
        return {
            "has_access": True,
            "role": role,
            "company_permissions": permissions,
            "branch_permissions": [],
            "can_manage_branch": can_manage,
            "can_view_branch": True,
            "can_edit_branch": can_manage,
            "can_delete_branch": role == "owner",
            "branch_is_headquarters": result.get("branch_is_headquarters", False),
            "user_id": user_id,
            "branch_id": branch_id
        }
    
    @staticmethod
    @monitor_db_operation("branch_list_company")
    @monitor_cache_operation("branch_list_company")
    @cache_result(ttl=BRANCH_LIST_CACHE_TTL, key_func=lambda self, company_id, active_only: 
                  CompanyBranchRepository._get_company_branches_key(company_id, active_only))
    async def get_company_branches(
        company_id: str,
        active_only: bool = True
    ) -> List[CompanyBranch]:
        pipeline = [
            {
                "$match": {
                    "company_id": ObjectId(company_id)
                }
            }
        ]
        
        if active_only:
            pipeline[0]["$match"]["is_active"] = True
        
        pipeline.extend([
            *CompanyBranchRepository._branch_base_pipeline(),
            {
                "$sort": {"name": 1}
            },
            {
                "$project": {
                    "_id": 1,
                    "name": 1,
                    "description": 1,
                    "address": 1,
                    "city": 1,
                    "country": 1,
                    "email": 1,
                    "phone": 1,
                    "is_headquarters": 1,
                    "is_active": 1,
                    "company_id": 1,
                    "created_at": 1,
                    "updated_at": 1,
                    "company_name": "$company.name"
                }
            }
        ])
        
        try:
            branches = []
            async for doc in CompanyBranch.aggregate(pipeline):
                branch_data = {k: v for k, v in doc.items() if k != "company_name"}
                branches.append(CompanyBranch(**branch_data))
            return branches
        except Exception as e:
            logger.error(f"Error getting company branches: {e}")
            return []
    
    @staticmethod
    @monitor_db_operation("branch_list_user")
    @monitor_cache_operation("branch_list_user")
    @cache_result(ttl=USER_BRANCHES_CACHE_TTL, key_func=lambda self, user_id, active_only: 
                  CompanyBranchRepository._get_user_branches_key(user_id, active_only))
    async def get_user_company_branches(
        user_id: str,
        active_only: bool = True
    ) -> List[CompanyBranch]:
        pipeline = [
            {
                "$match": {
                    "members.user_id": ObjectId(user_id),
                    "is_active": True
                }
            },
            {
                "$lookup": {
                    "from": "company_branches",
                    "let": {"company_id": "$_id"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$company_id", "$$company_id"]},
                                        {"$eq": ["$is_active", True]} if active_only else {}
                                    ]
                                }
                            }
                        },
                        {
                            "$sort": {"name": 1}
                        }
                    ],
                    "as": "branches"
                }
            },
            {
                "$unwind": "$branches"
            },
            {
                "$replaceRoot": {"newRoot": "$branches"}
            }
        ]
        
        try:
            branches = []
            async for doc in Company.aggregate(pipeline):
                branches.append(CompanyBranch(**doc))
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
        is_headquarters: Optional[bool] = None,
        is_active: bool = True,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[CompanyBranch], int]:
        match_stage = {"is_active": is_active}
        
        if company_id:
            match_stage["company_id"] = ObjectId(company_id)
        
        if city:
            match_stage["city"] = {"$regex": city, "$options": "i"}
        
        if country:
            match_stage["country"] = {"$regex": country, "$options": "i"}
        
        if is_headquarters is not None:
            match_stage["is_headquarters"] = is_headquarters
        
        if search_term:
            match_stage["$or"] = [
                {"name": {"$regex": search_term, "$options": "i"}},
                {"description": {"$regex": search_term, "$options": "i"}},
                {"address": {"$regex": search_term, "$options": "i"}},
                {"email": {"$regex": search_term, "$options": "i"}},
                {"phone": {"$regex": search_term, "$options": "i"}}
            ]
        
        pipeline = [
            {"$match": match_stage},
            *CompanyBranchRepository._branch_base_pipeline(),
            {
                "$facet": {
                    "metadata": [
                        {"$count": "total"}
                    ],
                    "branches": [
                        {"$sort": {"name": 1}},
                        {"$skip": skip},
                        {"$limit": limit},
                        {
                            "$project": {
                                "_id": 1,
                                "name": 1,
                                "description": 1,
                                "address": 1,
                                "city": 1,
                                "country": 1,
                                "email": 1,
                                "phone": 1,
                                "is_headquarters": 1,
                                "is_active": 1,
                                "company_id": 1,
                                "created_at": 1,
                                "updated_at": 1,
                                "company_name": "$company.name"
                            }
                        }
                    ]
                }
            }
        ]
        
        try:
            result = await CompanyBranch.aggregate(pipeline).to_list(length=1)
            if not result:
                return [], 0
            
            data = result[0]
            total = data["metadata"][0]["total"] if data["metadata"] else 0
            
            branches = []
            for doc in data["branches"]:
                branch_data = {k: v for k, v in doc.items() if k != "company_name"}
                branches.append(CompanyBranch(**branch_data))
            
            return branches, total
        except Exception as e:
            logger.error(f"Error searching branches: {e}")
            return [], 0
    
    @staticmethod
    @monitor_db_operation("branch_get_statistics")
    @monitor_cache_operation("branch_get_statistics")
    @cache_result(ttl=STATS_CACHE_TTL, key_func=lambda self, branch_id: 
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
                    "branch_name": "$name",
                    "company_id": {"$toString": "$company_id"},
                    "company_name": "$company.name",
                    "is_headquarters": "$is_headquarters",
                    "is_active": "$is_active",
                    "address": "$address",
                    "city": "$city",
                    "country": "$country",
                    "contact_email": "$email",
                    "contact_phone": "$phone",
                    "created_at": "$created_at",
                    "updated_at": "$updated_at",
                    "created_by_name": "$creator.full_name",
                    "company_member_count": {
                        "$size": {
                            "$ifNull": ["$company.members", []]
                        }
                    },
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
    @cache_result(ttl=STATS_CACHE_TTL, key_func=lambda self, company_id: 
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
                    "headquarters_count": {
                        "$sum": {"$cond": [{"$eq": ["$is_headquarters", True]}, 1, 0]}
                    },
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
                    "headquarters_count": 1,
                    "regular_branches_count": {
                        "$subtract": ["$total_branches", "$headquarters_count"]
                    },
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
                "headquarters_count": 0,
                "regular_branches_count": 0,
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
            permission_pipeline = [
                {
                    "$match": {
                        "_id": {"$in": [ObjectId(bid) for bid in branch_ids]}
                    }
                },
                *CompanyBranchRepository._branch_base_pipeline(),
                *CompanyBranchRepository._with_user_member_pipeline(updated_by),
                {
                    "$group": {
                        "_id": None,
                        "authorized_branches": {
                            "$push": {
                                "$cond": [
                                    {
                                        "$or": [
                                            {"$eq": ["$user_member.role", "owner"]},
                                            {"$in": ["manage_branches", "$user_member.permissions"]}
                                        ]
                                    },
                                    {"$toString": "$_id"},
                                    "$$REMOVE"
                                ]
                            }
                        }
                    }
                }
            ]
            
            result = await CompanyBranch.aggregate(permission_pipeline).to_list(length=1)
            authorized_ids = result[0]["authorized_branches"] if result else []
            
            if not authorized_ids:
                return 0, 0
            
            update_dict = {
                k: v for k, v in update_data.items()
                if k not in {"_id", "company_id", "is_headquarters"}
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
            CompanyBranchRepository._get_headquarters_key(str(branch.company_id)),
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
        
        pipeline = [
            {
                "$match": {
                    "_id": {"$in": [ObjectId(bid) for bid in branch_ids]}
                }
            },
            {
                "$group": {
                    "_id": "$company_id"
                }
            }
        ]
        
        company_ids = []
        async for doc in CompanyBranch.aggregate(pipeline):
            company_ids.append(str(doc["_id"]))
        
        delete_tasks = []
        for branch_id in branch_ids:
            delete_tasks.extend([
                CompanyBranchRepository._delete_cached(
                    CompanyBranchRepository._get_branch_key(branch_id),
                    CompanyBranchRepository._get_branch_stats_key(branch_id)
                )
            ])
        
        for company_id in company_ids:
            delete_tasks.extend([
                CompanyBranchRepository._delete_cached(
                    CompanyBranchRepository._get_company_branches_key(company_id, True),
                    CompanyBranchRepository._get_company_branches_key(company_id, False),
                    CompanyBranchRepository._get_company_stats_key(company_id)
                )
            ])
        
        await asyncio.gather(*delete_tasks, return_exceptions=True)
    
    @staticmethod
    async def clear_all_cache() -> None:
        await CompanyBranchRepository._invalidate_pattern(
            f"{CompanyBranchRepository.CACHE_PREFIX}*"
        )
        logger.info("Cleared all company branch cache")