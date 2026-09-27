from app.core.transactions import transactional, current_session, defer_after_commit
from app.core.errors import CustomError, ErrorCodes
from typing import List, Optional, Dict, Any, Tuple, Union
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import logging
import bcrypt
import secrets
import hashlib
import re
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate, UserFilter
from app.core.redis import get_redis, is_redis_available
from app.core import cache as shared_cache
from app.core.monitoring import monitor_db_operation, monitor_cache_operation, monitor
from app.utils.time import now_utc
from app.core.config import settings
from app.core.database import get_database_info
from app.core.security import get_password_hash_async, verify_password_async

logger = logging.getLogger(__name__)


class UserRepository:
    CACHE_PREFIX = shared_cache.cache_key("user")
    USER_CACHE_TTL = 1800
    USER_LIST_CACHE_TTL = 300
    USER_SEARCH_CACHE_TTL = 300
    RESET_TOKEN_TTL = 3600
    NULL_CACHE_VALUE = "__NULL__"
    NULL_CACHE_TTL = 60

    @staticmethod
    def _get_user_cache_key(user_id: str) -> str:
        return f"{UserRepository.CACHE_PREFIX}:id:{user_id}"

    @staticmethod
    def _get_user_email_cache_key(email: str) -> str:
        digest = hashlib.sha256(str(email).strip().lower().encode("utf-8")).hexdigest()
        return f"{UserRepository.CACHE_PREFIX}:email:{digest}"

    @staticmethod
    def _get_user_username_cache_key(username: str) -> str:
        digest = hashlib.sha256(
            str(username).strip().lower().encode("utf-8")
        ).hexdigest()
        return f"{UserRepository.CACHE_PREFIX}:username:{digest}"

    LIST_VERSION_KEY = f"{CACHE_PREFIX}:list-version"

    @staticmethod
    async def _get_list_cache_version() -> str:
        if not is_redis_available():
            return "noversion"
        try:
            redis_client = get_redis()
            version = await redis_client.get(UserRepository.LIST_VERSION_KEY)
            if version:
                return (
                    version.decode("utf-8") if isinstance(version, bytes) else version
                )

            await redis_client.set(UserRepository.LIST_VERSION_KEY, "1")
            return "1"
        except Exception as e:
            logger.warning(f"Error getting list version: {e}")
            return "err_version"

    @staticmethod
    async def _increment_list_cache_version() -> None:
        if not is_redis_available():
            return

        try:
            redis_client = get_redis()
            await redis_client.incr(UserRepository.LIST_VERSION_KEY)
            logger.info("User list cache version incremented (invalidated old lists).")
        except Exception as e:
            logger.warning(f"Error incrementing list version: {e}")

    @staticmethod
    async def _get_user_list_cache_key(page: int, size: int, filters: dict) -> str:
        import json

        filter_str = json.dumps(
            filters, sort_keys=True, default=str, separators=(",", ":")
        )
        filter_hash = hashlib.sha256(filter_str.encode("utf-8")).hexdigest()
        version = await UserRepository._get_list_cache_version()

        return (
            f"{UserRepository.CACHE_PREFIX}:list:v{version}:{page}:{size}:{filter_hash}"
        )

    @staticmethod
    def _get_user_search_cache_key(search_term: str, skip: int, limit: int) -> str:
        digest = hashlib.sha256(search_term.strip().lower().encode("utf-8")).hexdigest()
        return f"{UserRepository.CACHE_PREFIX}:search:{digest}:{skip}:{limit}"

    @staticmethod
    def _get_reset_token_cache_key(token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{UserRepository.CACHE_PREFIX}reset_token:{digest}"

    @staticmethod
    def _generate_reset_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    @monitor_db_operation("user_create")
    async def create_user(user_data: Union[UserCreate, dict]) -> User:
        if isinstance(user_data, UserCreate):
            email = str(user_data.email).strip().lower()
            phone_number = user_data.phone_number
            password = user_data.password
            data = user_data.model_dump(exclude={"password"})
        else:
            email = str(user_data.get("email", "")).strip().lower()
            phone_number = user_data.get("phone_number")
            password = user_data.get("password")
            data = user_data.copy()
            data.pop("password", None)

        if not email or not password:
            raise ValueError("Email and password are required")

        if await User.find_one(User.email == email):
            raise ValueError("User with this email already exists")

        if phone_number and await User.find_one(User.phone_number == phone_number):
            raise ValueError("User with this phone number already exists")

        data.pop("email", None)
        data.pop("phone_number", None)
        data.pop("is_active", None)
        data.pop("is_verified", None)
        data.pop("is_superuser", None)
        data.pop("created_at", None)
        data.pop("updated_at", None)
        user = User(
            **data,
            email=email,
            phone_number=phone_number,
            hashed_password=await get_password_hash_async(password),
            is_active=False,
            is_verified=False,
            is_superuser=False,
            created_at=now_utc(),
            updated_at=now_utc(),
        )

        try:
            await user.insert()
        except DuplicateKeyError as exc:
            raise ValueError(
                "User with this email or phone number already exists"
            ) from exc
        await UserRepository._delete_cache(
            UserRepository._get_user_email_cache_key(email)
        )
        await UserRepository._clear_user_list_caches()

        logger.info(f"User created: {user.id} - {user.email}")
        return user

    @staticmethod
    @monitor_db_operation("user_get")
    @monitor_cache_operation("user_get")
    async def get_user(user_id: str) -> Optional[User]:
        cache_key = UserRepository._get_user_cache_key(user_id)
        cached_data = await UserRepository._get_from_cache(cache_key)

        if cached_data == UserRepository.NULL_CACHE_VALUE:
            return None

        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                await UserRepository._set_cache(
                    cache_key,
                    UserRepository.NULL_CACHE_VALUE,
                    UserRepository.NULL_CACHE_TTL,
                )
            return user
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None

    @staticmethod
    @monitor_db_operation("user_get_by_email")
    @monitor_cache_operation("user_get_by_email")
    async def get_user_by_email(email: str) -> Optional[User]:
        email = str(email).strip().lower()
        cache_key = UserRepository._get_user_email_cache_key(email)
        cached = await UserRepository._get_from_cache(cache_key)

        if cached == UserRepository.NULL_CACHE_VALUE:
            return None

        user = await User.find_one(User.email == email)
        if not user:
            await UserRepository._set_cache(
                cache_key,
                UserRepository.NULL_CACHE_VALUE,
                UserRepository.NULL_CACHE_TTL,
            )
            return None

        return user

    @staticmethod
    @monitor_db_operation("user_get_by_username")
    @monitor_cache_operation("user_get_by_username")
    async def get_user_by_username(username: str) -> Optional[User]:
        cache_key = UserRepository._get_user_username_cache_key(username)
        cached_data = await UserRepository._get_from_cache(cache_key)

        if cached_data == UserRepository.NULL_CACHE_VALUE:
            return None

        try:
            user = await User.find_one({"username": username})
            if not user:
                await UserRepository._set_cache(
                    cache_key,
                    UserRepository.NULL_CACHE_VALUE,
                    UserRepository.NULL_CACHE_TTL,
                )
            return user
        except Exception as e:
            logger.error(f"Error getting user by username {username}: {e}")
            return None

    @staticmethod
    @monitor_db_operation("user_update")
    async def update_user(
        user_id: str, update_data: UserUpdate, *, allow_superuser: bool = False
    ) -> Optional[User]:
        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                return None

            update_dict = update_data.model_dump(
                exclude_unset=True, exclude={"password"}
            )

            original_superuser = user.is_superuser
            if original_superuser and (
                not allow_superuser
                or update_dict.get("is_active") is False
                or update_dict.get("is_superuser") is False
            ):
                raise CustomError(
                    ErrorCodes.FORBIDDEN, "Cannot modify this superuser", 403
                )
            for field, value in update_dict.items():
                setattr(user, field, value)

            update_dict = {field: getattr(user, field) for field in update_dict}
            update_dict["updated_at"] = now_utc()
            command = {"$set": update_dict}
            if "is_active" in update_dict:
                command["$inc"] = {"auth_version": 1}
                update_dict["deactivated_at"] = (
                    None if update_dict["is_active"] else now_utc()
                )
            result = await User.find_one(
                {"_id": user.id, "is_superuser": original_superuser}
            ).update(command)
            if not result or result.matched_count != 1:
                raise CustomError(
                    ErrorCodes.CONFLICT, "User privileges changed; retry", 409
                )
            user = await User.get(user.id)

            await UserRepository._invalidate_user_caches(user)

            logger.info(f"User updated: {user_id}")
            return user

        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}", exc_info=True)
            raise

    @staticmethod
    @monitor_db_operation("user_delete")
    async def delete_user(user_id: str, deleted_by: Optional[str] = None) -> bool:
        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                return False

            if user.is_superuser:
                raise CustomError(
                    ErrorCodes.FORBIDDEN, "Superuser removal is prohibited", 403
                )

            result = await User.find_one(
                {"_id": user.id, "is_superuser": False}
            ).update(
                {
                    "$set": {
                        "is_active": False,
                        "deleted_at": now_utc(),
                        "deleted_by": ObjectId(deleted_by) if deleted_by else None,
                    },
                    "$inc": {"auth_version": 1},
                }
            )

            if not result or result.matched_count != 1:
                raise CustomError(ErrorCodes.CONFLICT, "User changed concurrently", 409)

            await UserRepository._invalidate_user_caches(user)
            await UserRepository._clear_user_list_caches()

            logger.info(f"User soft deleted: {user_id}")
            return True

        except CustomError:
            raise
        except ValueError as e:
            logger.error(f"Authorization error deleting user: {e}")
            raise
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
            return False

    @staticmethod
    @monitor_db_operation("user_hard_delete")
    async def hard_delete_user(user_id: str) -> bool:
        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                return False

            if user.is_active:
                raise ValueError("Cannot hard delete active user")

            if user.is_superuser:
                raise CustomError(
                    ErrorCodes.FORBIDDEN, "Superuser removal is prohibited", 403
                )
            result = await User.find_one(
                {"_id": user.id, "is_superuser": False, "is_active": False}
            ).delete()
            if not result or result.deleted_count != 1:
                raise CustomError(ErrorCodes.CONFLICT, "User changed concurrently", 409)

            await UserRepository._invalidate_user_caches(user)

            logger.warning(f"User hard deleted: {user_id}")
            return True

        except CustomError:
            raise
        except ValueError as e:
            logger.error(f"Validation error hard deleting user: {e}")
            raise
        except Exception as e:
            logger.error(f"Error hard deleting user {user_id}: {e}", exc_info=True)
            return False

    @staticmethod
    @monitor_db_operation("user_list")
    @monitor_cache_operation("user_list")
    async def list_users(
        page: int = 1,
        size: int = 20,
        filters: Optional[UserFilter] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> Tuple[List[User], int]:
        filter_dict = filters.model_dump(exclude_unset=True) if filters else {}
        try:
            query = {"is_active": True}

            if filters:
                if filters.email:
                    query["email"] = {
                        "$regex": re.escape(filters.email),
                        "$options": "i",
                    }
                if filters.full_name:
                    query["full_name"] = {
                        "$regex": re.escape(filters.full_name),
                        "$options": "i",
                    }
                if filters.phone:
                    query["phone_number"] = {
                        "$regex": re.escape(filters.phone),
                        "$options": "i",
                    }
                if filters.is_verified is not None:
                    query["is_verified"] = filters.is_verified
                if filters.role:
                    query["role"] = filters.role

            total = await User.find(query).count()

            sort_direction = -1 if sort_desc else 1
            cursor = User.find(query).sort([(sort_by, sort_direction)])

            size = min(size, settings.MAX_PAGE_SIZE)
            skip = (page - 1) * size
            users = await cursor.skip(skip).limit(size).to_list()

            return users, total

        except Exception as e:
            logger.error(f"Error listing users: {e}", exc_info=True)
            raise

    @staticmethod
    @monitor_db_operation("user_search")
    @monitor_cache_operation("user_search")
    async def search_users(
        search_term: str, skip: int = 0, limit: int = 20
    ) -> Tuple[List[User], int]:
        try:
            if not search_term or len(search_term.strip()) < 2:
                return [], 0

            search_term = re.escape(search_term.strip()[:100])

            query = {
                "is_active": True,
                "$or": [
                    {"email": {"$regex": search_term, "$options": "i"}},
                    {"username": {"$regex": search_term, "$options": "i"}},
                    {"full_name": {"$regex": search_term, "$options": "i"}},
                    {"phone_number": {"$regex": search_term, "$options": "i"}},
                ],
            }

            total = await User.find(query).count()

            limit = min(limit, settings.MAX_PAGE_SIZE)
            cursor = User.find(query).sort([("created_at", -1)])
            users = await cursor.skip(skip).limit(limit).to_list()

            return users, total

        except Exception as e:
            logger.error(f"Error searching users: {e}", exc_info=True)
            raise

    @staticmethod
    @monitor_db_operation("user_authenticate")
    async def authenticate_user(email: str, password: str) -> Optional[User]:
        try:
            user = await UserRepository.get_user_by_email(email)

            if not user:
                return None

            if not await verify_password_async(password, user.hashed_password):
                logger.warning(f"Failed authentication attempt for email: {email}")
                return None

            await User.find_one(
                {
                    "_id": user.id,
                    "auth_version": user.auth_version,
                    "hashed_password": user.hashed_password,
                }
            ).update({"$set": {"last_login": now_utc()}})

            await UserRepository._delete_cache(
                UserRepository._get_user_cache_key(str(user.id))
            )

            logger.info(f"User authenticated: {email}")
            return user

        except Exception as e:
            logger.error(f"Error authenticating user {email}: {e}")
            return None

    @staticmethod
    @monitor_db_operation("user_verify")
    async def verify_user(user_id: str, *, activate_registration: bool = False) -> bool:
        query = {
            "_id": ObjectId(user_id),
            "is_verified": False,
            "deactivated_at": None,
            "deleted_at": None,
        }
        values = {
            "is_verified": True,
            "verified_at": now_utc(),
            "updated_at": now_utc(),
        }
        if activate_registration:
            values["is_active"] = True
        result = await User.find_one(query).update({"$set": values})
        if not result or result.modified_count != 1:
            return False
        user = await User.get(ObjectId(user_id))
        await UserRepository._invalidate_user_caches(user)
        return True

    @staticmethod
    @monitor_db_operation("user_generate_reset_token")
    async def generate_password_reset_token(email: str) -> Optional[str]:
        user = await UserRepository.get_user_by_email(email)
        if not user or not user.is_active:
            return None
        token = UserRepository._generate_reset_token()
        result = await User.find_one(
            {"_id": user.id, "is_active": True, "auth_version": user.auth_version}
        ).update(
            {
                "$set": {
                    "password_reset_digest": hashlib.sha256(token.encode()).hexdigest(),
                    "password_reset_expires_at": now_utc()
                    + timedelta(seconds=UserRepository.RESET_TOKEN_TTL),
                    "password_reset_version": user.auth_version,
                }
            }
        )
        return token if result and result.modified_count == 1 else None

    @staticmethod
    def _reset_filter(token: str) -> dict:
        return {
            "password_reset_digest": hashlib.sha256(token.encode()).hexdigest(),
            "password_reset_expires_at": {"$gt": now_utc()},
            "is_active": True,
            "$expr": {"$eq": ["$auth_version", "$password_reset_version"]},
        }

    @staticmethod
    async def validate_password_reset_token(token: str) -> Optional[str]:
        user = await User.find_one(UserRepository._reset_filter(token))
        return str(user.id) if user else None

    @staticmethod
    @monitor_db_operation("user_reset_password")
    async def reset_password(token: str, new_password: str) -> bool:
        user = await User.find_one(UserRepository._reset_filter(token))
        if not user:
            return False
        # Token consumption and password/version change are one document CAS.
        query = UserRepository._reset_filter(token)
        query["_id"] = user.id
        query["auth_version"] = user.auth_version
        result = await User.find_one(query).update(
            {
                "$set": {
                    "hashed_password": await get_password_hash_async(new_password),
                    "updated_at": now_utc(),
                },
                "$inc": {"auth_version": 1},
                "$unset": {
                    "password_reset_digest": "",
                    "password_reset_expires_at": "",
                    "password_reset_version": "",
                },
            }
        )
        if not result or result.modified_count != 1:
            return False
        await UserRepository._invalidate_user_caches(user)
        await UserRepository._invalidate_user_sessions(str(user.id))
        return True

    @staticmethod
    @monitor_db_operation("user_change_password")
    async def change_password(
        user_id: str, current_password: str, new_password: str
    ) -> bool:
        try:
            user = await User.get(ObjectId(user_id))
            if not user or not user.is_active:
                return False

            if not await verify_password_async(current_password, user.hashed_password):
                logger.warning(f"Password change failed for user: {user_id}")
                return False

            result = await User.find_one(
                {
                    "_id": user.id,
                    "is_active": True,
                    "auth_version": user.auth_version,
                    "hashed_password": user.hashed_password,
                }
            ).update(
                {
                    "$set": {
                        "hashed_password": await get_password_hash_async(new_password),
                        "updated_at": now_utc(),
                    },
                    "$inc": {"auth_version": 1},
                    "$unset": {
                        "password_reset_digest": "",
                        "password_reset_expires_at": "",
                        "password_reset_version": "",
                    },
                }
            )
            if not result or result.modified_count != 1:
                return False
            await UserRepository._invalidate_user_caches(user)

            await UserRepository._invalidate_user_sessions(user_id)

            logger.info(f"Password changed for user: {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error changing password for user {user_id}: {e}")
            return False

    @staticmethod
    @monitor_db_operation("user_stats")
    async def get_user_statistics() -> Dict[str, Any]:
        try:
            total_users = await User.find({}).count()
            active_users = await User.find({"is_active": True}).count()
            verified_users = await User.find({"is_verified": True}).count()
            superusers = await User.find({"is_superuser": True}).count()

            # Get daily signups (last 7 days)
            seven_days_ago = datetime.now() - timedelta(days=7)
            recent_signups = await User.find(
                {"created_at": {"$gte": seven_days_ago}}
            ).count()

            roles = {}
            if hasattr(User, "role"):
                pipeline = [
                    {"$match": {"is_active": True}},
                    {"$group": {"_id": "$role", "count": {"$sum": 1}}},
                ]
                role_cursor = User.aggregate(pipeline)
                async for role_data in role_cursor:
                    roles[role_data["_id"]] = role_data["count"]

            stats = {
                "total_users": total_users,
                "active_users": active_users,
                "verified_users": verified_users,
                "superusers": superusers,
                "recent_signups_7d": recent_signups,
                "inactive_users": total_users - active_users,
                "unverified_users": total_users - verified_users,
                "user_roles": roles,
                "calculated_at": datetime.now().isoformat(),
            }

            return stats

        except Exception as e:
            logger.error(f"Error getting user statistics: {e}")
            return {"error": str(e), "calculated_at": datetime.now().isoformat()}

    @staticmethod
    @monitor_db_operation("user_activity_stats")
    async def get_user_activity_statistics(user_id: str) -> Dict[str, Any]:
        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                return {"error": "User not found"}

            account_age_days = (
                (datetime.now() - user.created_at).days if user.created_at else 0
            )

            last_activity = max(
                user.last_login or user.created_at, user.updated_at or user.created_at
            )

            stats = {
                "user_id": user_id,
                "email": user.email,
                "username": user.username,
                "full_name": user.full_name,
                "is_active": user.is_active,
                "is_verified": user.is_verified,
                "is_superuser": user.is_superuser,
                "account_created": (
                    user.created_at.isoformat() if user.created_at else None
                ),
                "account_age_days": account_age_days,
                "last_login": user.last_login.isoformat() if user.last_login else None,
                "last_activity": last_activity.isoformat(),
                "email_verified_at": (
                    user.verified_at.isoformat() if user.verified_at else None
                ),
                "phone_verified": bool(user.phone_verified_at),
                "calculated_at": datetime.now().isoformat(),
            }

            return stats

        except Exception as e:
            logger.error(f"Error getting user activity stats for {user_id}: {e}")
            return {
                "user_id": user_id,
                "error": str(e),
                "calculated_at": datetime.now().isoformat(),
            }

    @staticmethod
    @monitor_db_operation("user_bulk_update")
    @transactional
    async def bulk_update_users(
        user_ids: List[str],
        update_data: Dict[str, Any],
        *,
        allow_superuser: bool = False,
    ) -> Tuple[int, int]:
        ids = list({ObjectId(uid) for uid in user_ids})
        update_data = {
            k: v
            for k, v in update_data.items()
            if k not in {"hashed_password", "password"}
        }
        users = await User.find(
            {"_id": {"$in": ids}}, session=current_session()
        ).to_list()
        if len(users) != len(ids):
            raise CustomError(ErrorCodes.NOT_FOUND, "User not found", 404)
        protected = (
            not allow_superuser
            or update_data.get("is_active") is False
            or update_data.get("is_superuser") is False
        )
        if protected and any(user.is_superuser for user in users):
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Cannot modify these superusers", 403
            )
        update_data["updated_at"] = now_utc()
        command = {"$set": update_data}
        if "is_active" in update_data:
            command["$inc"] = {"auth_version": 1}
            update_data["deactivated_at"] = (
                None if update_data["is_active"] else now_utc()
            )
        query = {"_id": {"$in": ids}}
        if protected:
            query["is_superuser"] = False
        result = await User.find(query, session=current_session()).update_many(
            command, session=current_session()
        )
        if result.matched_count != len(ids):
            raise CustomError(
                ErrorCodes.CONFLICT, "User privileges changed; retry", 409
            )
        for user in users:
            defer_after_commit(
                lambda user=user: UserRepository._invalidate_user_caches(user)
            )
        defer_after_commit(UserRepository._clear_user_list_caches)
        return result.modified_count, len(ids)

    @staticmethod
    @monitor_db_operation("user_bulk_deactivate")
    @transactional
    async def bulk_deactivate_users(user_ids: List[str], deactivated_by: str) -> int:
        ids = list({ObjectId(uid) for uid in user_ids})
        if ObjectId(deactivated_by) in ids:
            raise CustomError(ErrorCodes.FORBIDDEN, "Cannot deactivate yourself", 403)
        users = await User.find(
            {"_id": {"$in": ids}}, session=current_session()
        ).to_list()
        if len(users) != len(ids):
            raise CustomError(ErrorCodes.NOT_FOUND, "User not found", 404)
        if any(user.is_superuser for user in users):
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Superuser deactivation is prohibited", 403
            )
        result = await User.find(
            {"_id": {"$in": ids}, "is_superuser": False}, session=current_session()
        ).update_many(
            {
                "$set": {
                    "is_active": False,
                    "deactivated_at": now_utc(),
                    "deactivated_by": ObjectId(deactivated_by),
                    "updated_at": now_utc(),
                },
                "$inc": {"auth_version": 1},
            },
            session=current_session(),
        )
        if result.matched_count != len(ids):
            raise CustomError(ErrorCodes.CONFLICT, "Users changed concurrently", 409)
        for user in users:
            defer_after_commit(
                lambda user=user: UserRepository._invalidate_user_caches(user)
            )
        defer_after_commit(UserRepository._clear_user_list_caches)
        return result.modified_count

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
    async def _set_cache(key: str, data: Any, ttl: Optional[int] = None) -> bool:
        if not is_redis_available():
            return False
        try:
            redis_client = get_redis()
            import json

            if data is None:
                value_to_store = UserRepository.NULL_CACHE_VALUE
                effective_ttl = UserRepository.NULL_CACHE_TTL
            elif isinstance(data, str):
                value_to_store = data
                effective_ttl = ttl
            else:
                value_to_store = json.dumps(data, default=str)
                effective_ttl = ttl

            await redis_client.setex(
                key, effective_ttl or UserRepository.USER_CACHE_TTL, value_to_store
            )
            return True
        except Exception as e:
            logger.warning(f"Cache set error for key {key}: {e}")
            return False

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
    async def _invalidate_user_caches(user: User) -> None:
        if not is_redis_available():
            return

        try:
            redis_client = get_redis()

            keys_to_delete = [
                UserRepository._get_user_cache_key(str(user.id)),
                UserRepository._get_user_email_cache_key(user.email),
            ]

            if user.username:
                keys_to_delete.append(
                    UserRepository._get_user_username_cache_key(user.username)
                )

            if keys_to_delete:
                await redis_client.delete(*keys_to_delete)
                logger.debug(f"Invalidated caches for user: {user.id}")

        except Exception as e:
            logger.warning(f"Error invalidating user caches for {user.id}: {e}")

    @staticmethod
    async def _clear_user_list_caches() -> None:
        if not is_redis_available():
            return

        try:
            await UserRepository._increment_list_cache_version()

        except Exception as e:
            logger.warning(f"Error clearing user list caches: {e}")

    @staticmethod
    async def _invalidate_user_sessions(user_id: str) -> None:
        if not is_redis_available():
            return

        try:
            pattern = shared_cache.cache_key("session", user_id, "*")
            await shared_cache.delete_pattern(pattern)
            logger.debug(f"Invalidated session keys for user: {user_id}")

        except Exception as e:
            logger.warning(f"Error invalidating user sessions for {user_id}: {e}")

    @staticmethod
    async def clear_all_user_cache() -> None:
        if not is_redis_available():
            return

        try:
            pattern = f"{UserRepository.CACHE_PREFIX}:*"
            await shared_cache.delete_pattern(pattern)
            logger.info("Cleared all user cache")

        except Exception as e:
            logger.warning(f"Error clearing user cache: {e}")
