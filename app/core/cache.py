import hashlib
import json
import logging
from datetime import date, datetime
from enum import Enum
from typing import Any, AsyncIterator, Optional

from bson import ObjectId

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def cache_key(namespace: str, *parts: object) -> str:
    safe_parts = [str(part).replace(":", "_") for part in parts]
    return ":".join([settings.REDIS_KEY_PREFIX, namespace, *safe_parts])


def idempotency_key(scope: str, tenant_id: str, raw_key: str) -> str:
    # Hash user input so it cannot create unbounded or confusing Redis key names.
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return cache_key("idempotency", scope, tenant_id, digest)


async def get_json(key: str) -> Optional[Any]:
    redis = get_redis()
    if not redis:
        return None
    try:
        raw = await redis.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("Redis cache read failed for %s", key, exc_info=True)
        return None


async def set_json(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    redis = get_redis()
    if not redis:
        return False
    try:
        payload = json.dumps(value, default=_json_default, separators=(",", ":"))
        await redis.setex(key, ttl or settings.REDIS_CACHE_TTL, payload)
        return True
    except Exception:
        logger.warning("Redis cache write failed for %s", key, exc_info=True)
        return False


async def delete_keys(*keys: str) -> None:
    redis = get_redis()
    if not redis or not keys:
        return
    try:
        await redis.delete(*keys)
    except Exception:
        logger.warning("Redis cache deletion failed", exc_info=True)


async def iter_keys(pattern: str, count: int = 100) -> AsyncIterator[str]:
    redis = get_redis()
    if not redis:
        return
    async for key in redis.scan_iter(match=pattern, count=count):
        yield key


async def delete_pattern(pattern: str) -> None:
    # SCAN is intentionally used instead of KEYS to avoid blocking Redis.
    batch: list[str] = []
    async for key in iter_keys(pattern):
        batch.append(key)
        if len(batch) >= 100:
            await delete_keys(*batch)
            batch.clear()
    if batch:
        await delete_keys(*batch)


async def reserve_idempotency(
    scope: str,
    tenant_id: str,
    raw_key: str,
    owner_token: str,
) -> bool:
    redis = get_redis()
    if not redis:
        return True
    key = idempotency_key(scope, tenant_id, raw_key)
    try:
        return bool(
            await redis.set(
                key,
                f"processing:{owner_token}",
                ex=settings.IDEMPOTENCY_TTL,
                nx=True,
            )
        )
    except Exception:
        logger.warning("Redis idempotency reservation failed", exc_info=True)
        # Database unique indexes remain the fallback protection.
        return True


async def complete_idempotency(
    scope: str,
    tenant_id: str,
    raw_key: str,
    resource_id: str,
    owner_token: str,
) -> None:
    redis = get_redis()
    if not redis:
        return
    key = idempotency_key(scope, tenant_id, raw_key)
    script = """
    local current = redis.call('get', KEYS[1])
    if not current or current == ARGV[1] or current == ARGV[2] then
        redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3])
        return 1
    end
    return 0
    """
    try:
        await redis.eval(
            script,
            1,
            key,
            f"processing:{owner_token}",
            f"completed:{resource_id}",
            settings.IDEMPOTENCY_TTL,
        )
    except Exception:
        logger.warning("Redis idempotency completion failed", exc_info=True)


async def get_idempotency_result(scope: str, tenant_id: str, raw_key: str) -> Optional[str]:
    redis = get_redis()
    if not redis:
        return None
    try:
        value = await redis.get(idempotency_key(scope, tenant_id, raw_key))
        if value and value.startswith("completed:"):
            return value.split(":", 1)[1]
        return None
    except Exception:
        logger.warning("Redis idempotency lookup failed", exc_info=True)
        return None


async def release_idempotency(
    scope: str,
    tenant_id: str,
    raw_key: str,
    owner_token: str,
) -> None:
    redis = get_redis()
    if not redis:
        return
    key = idempotency_key(scope, tenant_id, raw_key)
    script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """
    try:
        await redis.eval(script, 1, key, f"processing:{owner_token}")
    except Exception:
        logger.warning("Redis idempotency release failed", exc_info=True)


def authorization_key(user_id: str) -> str:
    return cache_key("authz", user_id)


async def invalidate_user_authorization(user_id: str) -> None:
    await delete_keys(authorization_key(user_id))


async def invalidate_actor_authorization(actor_id: str) -> None:
    from bson import ObjectId
    from app.models.user_actor import UserActor

    if not ObjectId.is_valid(actor_id):
        return
    links = await UserActor.find({"actor_id": ObjectId(actor_id)}).to_list()
    await delete_keys(*(authorization_key(str(link.user_id)) for link in links))


async def invalidate_permission_authorization(permission_id: str) -> None:
    from bson import ObjectId
    from app.models.actor_permission import ActorPermission

    if not ObjectId.is_valid(permission_id):
        return
    links = await ActorPermission.find({"permission_id": ObjectId(permission_id)}).to_list()
    for link in links:
        await invalidate_actor_authorization(str(link.actor_id))
