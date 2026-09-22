import logging
from typing import Any, Optional

from app.core.cache import cache_key
from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


async def enqueue(queue: str, resource_id: str, company_id: str) -> Optional[str]:
    """Enqueue lightweight work metadata in a bounded Redis Stream.

    MongoDB remains the source of truth. If Redis is unavailable, the queued
    database record can be recovered by a low-frequency reconciliation worker.
    """
    redis = get_redis()
    if not redis:
        return None
    try:
        return await redis.xadd(
            cache_key("queue", queue),
            {"resource_id": resource_id, "company_id": company_id},
            maxlen=settings.REDIS_QUEUE_MAX_LENGTH,
            approximate=True,
        )
    except Exception:
        logger.warning("Could not enqueue %s job %s", queue, resource_id, exc_info=True)
        return None


async def ensure_consumer_group(queue: str, group: str) -> None:
    redis = get_redis()
    if not redis:
        return
    try:
        await redis.xgroup_create(cache_key("queue", queue), group, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def read(
    queue: str,
    group: str,
    consumer: str,
    *,
    count: int = 10,
    block_ms: int = 5000,
) -> list[Any]:
    redis = get_redis()
    if not redis:
        return []
    await ensure_consumer_group(queue, group)
    return await redis.xreadgroup(
        group,
        consumer,
        {cache_key("queue", queue): ">"},
        count=min(count, 100),
        block=min(block_ms, 30000),
    )


async def acknowledge(queue: str, group: str, message_id: str) -> None:
    redis = get_redis()
    if redis:
        await redis.xack(cache_key("queue", queue), group, message_id)
