import logging

from app.core.cache import cache_key
from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


class MinerUCircuitBreaker:
    def __init__(self) -> None:
        self.failures_key = cache_key("worker-circuit", "mineru", "failures")
        self.open_key = cache_key("worker-circuit", "mineru", "open")

    async def is_open(self) -> bool:
        redis = get_redis()
        if not redis:
            return False
        try:
            return bool(await redis.exists(self.open_key))
        except Exception:
            logger.warning("MinerU circuit state could not be read")
            return False

    async def record_success(self) -> None:
        redis = get_redis()
        if not redis:
            return
        try:
            await redis.delete(self.failures_key, self.open_key)
        except Exception:
            logger.warning("MinerU circuit state could not be reset")

    async def record_transient_failure(self) -> None:
        redis = get_redis()
        if not redis:
            return
        try:
            failures = await redis.incr(self.failures_key)
            if failures == 1:
                await redis.expire(
                    self.failures_key, settings.MINERU_CIRCUIT_OPEN_SECONDS
                )
            if failures >= settings.MINERU_CIRCUIT_FAILURE_THRESHOLD:
                await redis.setex(
                    self.open_key,
                    settings.MINERU_CIRCUIT_OPEN_SECONDS,
                    "1",
                )
        except Exception:
            logger.warning("MinerU circuit failure could not be recorded")
