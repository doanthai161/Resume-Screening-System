from typing import List, Optional, Dict, Any, Tuple, Union
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import logging
import bcrypt
import secrets
from app.models.user import User
from app.schemas.user import (
    UserCreate, 
    UserUpdate, 
    UserFilter
)
from app.core.redis import get_redis, is_redis_available
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
from app.utils.time import now_vn
from app.core.config import settings
from app.core.database import get_database_info
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)


class UserRepository:
    CACHE_PREFIX = "user:"
    USER_CACHE_TTL = 1800 
    USER_LIST_CACHE_TTL = 300 
    USER_SEARCH_CACHE_TTL = 300  
    RESET_TOKEN_TTL = 3600 
    
    
    @staticmethod
    def _get_user_cache_key(user_id: str) -> str:
        return f"{UserRepository.CACHE_PREFIX}user:{user_id}"
    
    @staticmethod
    def _get_user_email_cache_key(email: str) -> str:
        return f"{UserRepository.CACHE_PREFIX}user_email:{email}"
    
    @staticmethod
    def _get_user_username_cache_key(username: str) -> str:
        return f"{UserRepository.CACHE_PREFIX}user_username:{username}"
    
    @staticmethod
    def _get_user_list_cache_key(page: int, size: int, filters: dict) -> str:
        filter_str = str(sorted(filters.items()))
        return f"{UserRepository.CACHE_PREFIX}list:{page}:{size}:{filter_str}"
    
    @staticmethod
    def _get_user_search_cache_key(search_term: str, skip: int, limit: int) -> str:
        return f"{UserRepository.CACHE_PREFIX}search:{search_term}:{skip}:{limit}"
    
    @staticmethod
    def _get_reset_token_cache_key(token: str) -> str:
        return f"{UserRepository.CACHE_PREFIX}reset_token:{token}"
    
    
    @staticmethod
    def _hash_password(password: str) -> str:
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    @staticmethod
    def _verify_password(plain_password: str, hashed_password: str) -> bool:
        try:
            return bcrypt.checkpw(
                plain_password.encode('utf-8'),
                hashed_password.encode('utf-8')
            )
        except Exception:
            return False

    @staticmethod
    def _get_db_client():
        return AsyncIOMotorClient(settings.MONGODB_URI)
    
    @staticmethod
    def _get_db():
        client = AsyncIOMotorClient(settings.MONGODB_URI)
        return client[settings.MONGODB_DB_NAME]

    @staticmethod
    def _generate_reset_token() -> str:
        return secrets.token_urlsafe(32)
    
    @staticmethod
    @monitor_db_operation("user_create")
    async def create_user(user_data: Union[UserCreate, dict]) -> User:
        if isinstance(user_data, UserCreate):
            email = user_data.email
            phone_number = user_data.phone_number
            password = user_data.password
            data = user_data.model_dump(exclude={"password"})
        else:
            email = user_data.get("email")
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
            hashed_password=UserRepository._hash_password(password),
            is_active=False,
            is_verified=False,
            is_superuser=False,
            created_at=now_vn(),
            updated_at=now_vn(),
        )

        await user.insert()
        await UserRepository._clear_user_list_caches()

        logger.info(f"User created: {user.id} - {user.email}")
        return user
        
    @staticmethod
    @monitor_db_operation("user_get")
    @monitor_cache_operation("user_get")
    async def get_user(user_id: str) -> Optional[User]:
        cache_key = UserRepository._get_user_cache_key(user_id)
        cached_data = await UserRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user: {user_id}")
            user = User.model_validate(cached_data)
            setattr(user, '_from_cache', True)
            return user
        
        try:
            user = await User.get(ObjectId(user_id))
            if user:
                await UserRepository._set_cache(
                    cache_key, 
                    user.dict(exclude={"hashed_password"}),
                    UserRepository.USER_CACHE_TTL
                )
                logger.debug(f"Cache set for user: {user_id}")
            return user
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_get_by_email")
    @monitor_cache_operation("user_get_by_email")
    async def get_user_by_email(email: str) -> Optional[User]:
        cache_key = UserRepository._get_user_email_cache_key(email)
        cached = await UserRepository._get_from_cache(cache_key)

        if cached:
            user = User.model_validate(cached)
            setattr(user, "_from_cache", True)
            return user

        user = await User.find_one(User.email == email)
        if not user:
            return None

        data = user.model_dump(exclude={"hashed_password"})
        await UserRepository._set_cache(cache_key, data, UserRepository.USER_CACHE_TTL)

        return user
    
    @staticmethod
    @monitor_db_operation("user_get_by_username")
    @monitor_cache_operation("user_get_by_username")
    async def get_user_by_username(username: str) -> Optional[User]:
        cache_key = UserRepository._get_user_username_cache_key(username)
        cached_data = await UserRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user username: {username}")
            user = User.model_validate(cached_data)
            setattr(user, '_from_cache', True)
            return user
        
        try:
            user = await User.find_one({"username": username})
            if user:
                id_cache_key = UserRepository._get_user_cache_key(str(user.id))
                await UserRepository._set_cache(
                    id_cache_key,
                    user.dict(exclude={"hashed_password"}),
                    UserRepository.USER_CACHE_TTL
                )
                
                await UserRepository._set_cache(
                    cache_key,
                    user.dict(exclude={"hashed_password"}),
                    UserRepository.USER_CACHE_TTL
                )
                logger.debug(f"Cache set for user username: {username}")
            return user
        except Exception as e:
            logger.error(f"Error getting user by username {username}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_update")
    async def update_user(user_id: str, update_data: UserUpdate) -> Optional[User]:
        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                return None
            
            update_dict = update_data.model_dump(exclude_unset=True, exclude={"password"})
            
            if update_data.password:
                update_dict["hashed_password"] = UserRepository._hash_password(update_data.password)
            
            for field, value in update_dict.items():
                setattr(user, field, value)
            
            user.updated_at = now_vn()
            await user.save()
            
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
                superuser_count = await User.find({"is_superuser": True}).count()
                if superuser_count <= 1:
                    raise ValueError("Cannot delete the last superuser")
            
            user.is_active = False
            user.deleted_at = now_vn()
            user.deleted_by = ObjectId(deleted_by) if deleted_by else None
            await user.save()
            
            await UserRepository._invalidate_user_caches(user)
            await UserRepository._clear_user_list_caches()
            
            logger.info(f"User soft deleted: {user_id}")
            return True
            
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
            
            await user.delete()
            
            await UserRepository._invalidate_user_caches(user)
            
            logger.warning(f"User hard deleted: {user_id}")
            return True
            
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
        sort_desc: bool = True
    ) -> Tuple[List[User], int]:
        filter_dict = filters.model_dump(exclude_unset=True) if filters else {}
        cache_key = UserRepository._get_user_list_cache_key(page, size, filter_dict)
        cached_data = await UserRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user list: page={page}, size={size}")
            users = [User.model_validate(item) for item in cached_data.get("users", [])]
            total = cached_data.get("total", 0)
            for user in users:
                setattr(user, '_from_cache', True)
            return users, total
        
        try:
            query = {"is_active": True}
            
            if filters:
                if filters.email:
                    query["email"] = {"$regex": filters.email, "$options": "i"}
                if filters.full_name:
                    query["full_name"] = {"$regex": filters.full_name, "$options": "i"}
                if filters.full_name:
                    query["full_name"] = {"$regex": filters.full_name, "$options": "i"}
                if filters.phone:
                    query["phone"] = {"$regex": filters.phone, "$options": "i"}
                if filters.is_verified is not None:
                    query["is_verified"] = filters.is_verified
                if filters.role:
                    query["role"] = filters.role
            
            total = await User.find(query).count()
            
            sort_direction = -1 if sort_desc else 1
            cursor = User.find(query).sort([(sort_by, sort_direction)])
            
            skip = (page - 1) * size
            users = await cursor.skip(skip).limit(size).to_list()
            
            cache_data = {
                "users": [user.dict(exclude={"hashed_password"}) for user in users],
                "total": total
            }
            await UserRepository._set_cache(
                cache_key, 
                cache_data, 
                UserRepository.USER_LIST_CACHE_TTL
            )
            logger.debug(f"Cache set for user list: page={page}, size={size}")
            
            return users, total
            
        except Exception as e:
            logger.error(f"Error listing users: {e}", exc_info=True)
            return [], 0
    
    @staticmethod
    @monitor_db_operation("user_search")
    @monitor_cache_operation("user_search")
    async def search_users(
        search_term: str,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[User], int]:
        cache_key = UserRepository._get_user_search_cache_key(search_term, skip, limit)
        cached_data = await UserRepository._get_from_cache(cache_key)
        
        if cached_data:
            logger.debug(f"Cache hit for user search: {search_term}")
            users = [User.model_validate(item) for item in cached_data.get("users", [])]
            total = cached_data.get("total", 0)
            for user in users:
                setattr(user, '_from_cache', True)
            return users, total
        
        try:
            if not search_term or len(search_term.strip()) < 2:
                return [], 0
            
            search_term = search_term.strip()
            
            query = {
                "is_active": True,
                "$or": [
                    {"email": {"$regex": search_term, "$options": "i"}},
                    {"username": {"$regex": search_term, "$options": "i"}},
                    {"full_name": {"$regex": search_term, "$options": "i"}},
                    {"phone": {"$regex": search_term, "$options": "i"}},
                ]
            }
            
            total = await User.find(query).count()
            
            cursor = User.find(query).sort([("created_at", -1)])
            users = await cursor.skip(skip).limit(limit).to_list()
            
            cache_data = {
                "users": [user.dict(exclude={"hashed_password"}) for user in users],
                "total": total
            }
            await UserRepository._set_cache(
                cache_key, 
                cache_data, 
                UserRepository.USER_SEARCH_CACHE_TTL
            )
            logger.debug(f"Cache set for user search: {search_term}")
            
            return users, total
            
        except Exception as e:
            logger.error(f"Error searching users: {e}", exc_info=True)
            return [], 0
    
    
    @staticmethod
    @monitor_db_operation("user_authenticate")
    async def authenticate_user(email: str, password: str) -> Optional[User]:
        try:
            user = await UserRepository.get_user_by_email(email)
            
            if not user or not user.is_active:
                return None
            
            if not UserRepository._verify_password(password, user.hashed_password):
                logger.warning(f"Failed authentication attempt for email: {email}")
                return None
            
            user.last_login = now_vn()
            await user.save()
            
            # Don't cache authenticated user with updated timestamp
            await UserRepository._delete_cache(UserRepository._get_user_cache_key(str(user.id)))
            
            logger.info(f"User authenticated: {email}")
            return user
            
        except Exception as e:
            logger.error(f"Error authenticating user {email}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_verify")
    async def verify_user(user_id: str) -> bool:
        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                return False
            
            user.is_verified = True
            user.verified_at = now_vn()
            await user.save()
            
            await UserRepository._invalidate_user_caches(user)
            
            logger.info(f"User verified: {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error verifying user {user_id}: {e}")
            return False
    
    @staticmethod
    @monitor_db_operation("user_generate_reset_token")
    async def generate_password_reset_token(email: str) -> Optional[str]:
        try:
            user = await UserRepository.get_user_by_email(email)
            if not user or not user.is_active:
                return None
            
            token = UserRepository._generate_reset_token()
            
            token_cache_key = UserRepository._get_reset_token_cache_key(token)
            token_data = {
                "user_id": str(user.id),
                "email": user.email,
                "created_at": datetime.now().isoformat()
            }
            await UserRepository._set_cache(
                token_cache_key, 
                token_data, 
                UserRepository.RESET_TOKEN_TTL
            )
            
            logger.info(f"Password reset token generated for user: {email}")
            return token
            
        except Exception as e:
            logger.error(f"Error generating reset token for {email}: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_validate_reset_token")
    async def validate_password_reset_token(token: str) -> Optional[str]:
        try:
            token_cache_key = UserRepository._get_reset_token_cache_key(token)
            token_data = await UserRepository._get_from_cache(token_cache_key)
            
            if not token_data:
                return None
            
            user_id = token_data.get("user_id")
            if not user_id:
                return None
            
            user = await UserRepository.get_user(user_id)
            if not user or not user.is_active:
                await UserRepository._delete_cache(token_cache_key)
                return None
            
            return user_id
            
        except Exception as e:
            logger.error(f"Error validating reset token: {e}")
            return None
    
    @staticmethod
    @monitor_db_operation("user_reset_password")
    async def reset_password(token: str, new_password: str) -> bool:
        try:
            user_id = await UserRepository.validate_password_reset_token(token)
            if not user_id:
                return False
            
            user = await User.get(ObjectId(user_id))
            if not user:
                return False
            
            user.hashed_password = UserRepository._hash_password(new_password)
            user.updated_at = now_vn()
            await user.save()
            
            token_cache_key = UserRepository._get_reset_token_cache_key(token)
            await UserRepository._delete_cache(token_cache_key)
            await UserRepository._invalidate_user_caches(user)
            await UserRepository._invalidate_user_sessions(user_id)
            
            logger.info(f"Password reset for user: {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error resetting password: {e}")
            return False
    
    @staticmethod
    @monitor_db_operation("user_change_password")
    async def change_password(
        user_id: str, 
        current_password: str, 
        new_password: str
    ) -> bool:
        try:
            user = await User.get(ObjectId(user_id))
            if not user or not user.is_active:
                return False
            
            if not UserRepository._verify_password(current_password, user.hashed_password):
                logger.warning(f"Password change failed for user: {user_id}")
                return False
            
            user.hashed_password = UserRepository._hash_password(new_password)
            user.updated_at = now_vn()
            await user.save()
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
            recent_signups = await User.find({
                "created_at": {"$gte": seven_days_ago}
            }).count()
            
            roles = {}
            if hasattr(User, 'role'):
                pipeline = [
                    {"$match": {"is_active": True}},
                    {"$group": {"_id": "$role", "count": {"$sum": 1}}}
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
                "calculated_at": datetime.now().isoformat()
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting user statistics: {e}")
            return {
                "error": str(e),
                "calculated_at": datetime.now().isoformat()
            }
    
    @staticmethod
    @monitor_db_operation("user_activity_stats")
    async def get_user_activity_statistics(user_id: str) -> Dict[str, Any]:
        try:
            user = await User.get(ObjectId(user_id))
            if not user:
                return {"error": "User not found"}
            
            account_age_days = (datetime.now() - user.created_at).days if user.created_at else 0
            
            last_activity = max(
                user.last_login or user.created_at,
                user.updated_at or user.created_at
            )
            
            stats = {
                "user_id": user_id,
                "email": user.email,
                "username": user.username,
                "full_name": user.full_name,
                "is_active": user.is_active,
                "is_verified": user.is_verified,
                "is_superuser": user.is_superuser,
                "account_created": user.created_at.isoformat() if user.created_at else None,
                "account_age_days": account_age_days,
                "last_login": user.last_login.isoformat() if user.last_login else None,
                "last_activity": last_activity.isoformat(),
                "email_verified_at": user.verified_at.isoformat() if user.verified_at else None,
                "phone_verified": bool(user.phone_verified_at),
                "calculated_at": datetime.now().isoformat()
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting user activity stats for {user_id}: {e}")
            return {
                "user_id": user_id,
                "error": str(e),
                "calculated_at": datetime.now().isoformat()
            }
    
    @staticmethod
    @monitor_db_operation("user_bulk_update")
    async def bulk_update_users(
        user_ids: List[str],
        update_data: Dict[str, Any]
    ) -> Tuple[int, int]:
        try:
            if not user_ids:
                return 0, 0
            
            update_data = {k: v for k, v in update_data.items() 
                          if k not in ["hashed_password", "password"]}
            
            if not update_data:
                return 0, len(user_ids)
            
            update_data["updated_at"] = now_vn()
            
            result = await User.find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}}) \
                              .update_many({"$set": update_data})
            
            for user_id in user_ids:
                await UserRepository._delete_cache(UserRepository._get_user_cache_key(user_id))
            
            await UserRepository._clear_user_list_caches()
            
            logger.info(f"Bulk updated {result.modified_count} users")
            return result.modified_count, len(user_ids)
            
        except Exception as e:
            logger.error(f"Error bulk updating users: {e}")
            return 0, 0
    
    @staticmethod
    @monitor_db_operation("user_bulk_deactivate")
    async def bulk_deactivate_users(user_ids: List[str], deactivated_by: str) -> int:
        try:
            if not user_ids:
                return 0
            
            user_ids = [uid for uid in user_ids if uid != deactivated_by]
            
            superuser_ids = []
            superusers = await User.find({
                "_id": {"$in": [ObjectId(uid) for uid in user_ids]},
                "is_superuser": True
            }).to_list()
            
            superuser_ids = [str(user.id) for user in superusers]
            
            if len(superuser_ids) > 0:
                total_superusers = await User.find({"is_superuser": True}).count()
                if total_superusers <= len(superuser_ids):
                    user_ids = [uid for uid in user_ids if uid not in superuser_ids]
            
            update_data = {
                "is_active": False,
                "deactivated_at": now_vn(),
                "deactivated_by": ObjectId(deactivated_by),
                "updated_at": now_vn()
            }
            
            result = await User.find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}}) \
                              .update_many({"$set": update_data})
            
            for user_id in user_ids:
                await UserRepository._delete_cache(UserRepository._get_user_cache_key(user_id))
            
            await UserRepository._clear_user_list_caches()
            
            logger.info(f"Bulk deactivated {result.modified_count} users")
            return result.modified_count
            
        except Exception as e:
            logger.error(f"Error bulk deactivating users: {e}")
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
                ttl or UserRepository.USER_CACHE_TTL, 
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
                keys_to_delete.append(UserRepository._get_user_username_cache_key(user.username))
            
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
            redis_client = get_redis()
            pattern = f"{UserRepository.CACHE_PREFIX}list:*"
            keys = await redis_client.keys(pattern)
            
            if keys:
                await redis_client.delete(*keys)
                logger.debug(f"Cleared {len(keys)} user list cache keys")
            
        except Exception as e:
            logger.warning(f"Error clearing user list caches: {e}")
    
    @staticmethod
    async def _invalidate_user_sessions(user_id: str) -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            pattern = f"session:{user_id}:*"
            keys = await redis_client.keys(pattern)
            
            if keys:
                await redis_client.delete(*keys)
                logger.debug(f"Invalidated {len(keys)} session keys for user: {user_id}")
            
        except Exception as e:
            logger.warning(f"Error invalidating user sessions for {user_id}: {e}")
    
    @staticmethod
    async def clear_all_user_cache() -> None:
        if not is_redis_available():
            return
        
        try:
            redis_client = get_redis()
            pattern = f"{UserRepository.CACHE_PREFIX}*"
            keys = await redis_client.keys(pattern)
            
            if keys:
                await redis_client.delete(*keys)
                logger.info(f"Cleared all user cache ({len(keys)} keys)")
            
        except Exception as e:
            logger.warning(f"Error clearing user cache: {e}")