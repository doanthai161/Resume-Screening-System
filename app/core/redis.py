import logging
from typing import Any

try:
    from redis.asyncio import Redis
    from redis.asyncio.connection import ConnectionPool
except ImportError:  # Allows database-only/test deployments to start without cache.
    Redis = Any
    ConnectionPool = None
from app.core.config import settings

logger = logging.getLogger(__name__)

redis_client: Redis | None = None

async def init_redis():
    global redis_client
    
    try:
        if ConnectionPool is None:
            logger.warning("redis package is not installed; cache and distributed idempotency are disabled")
            redis_client = None
            return
        pool = ConnectionPool.from_url(
            str(settings.REDIS_URL),
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_keepalive=True,
            retry_on_timeout=True,
            health_check_interval=30
        )
        redis_client = Redis(connection_pool=pool)
        
        import asyncio
        try:
            await asyncio.wait_for(redis_client.ping(), timeout=2.0)
            logger.info("Redis connected successfully")
        except asyncio.TimeoutError:
            logger.warning("Redis connection timeout - Redis may not be running")
            await redis_client.aclose()
            redis_client = None
        except Exception as e:
            logger.warning(f"Redis not available: {e}")
            await redis_client.aclose()
            redis_client = None
            
    except Exception as e:
        logger.warning(f"Failed to initialize Redis: {e}")
        redis_client = None

async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.aclose()
        redis_client = None
        logger.info("Redis connection closed")

def get_redis() -> Redis | None:
    return redis_client

def is_redis_available() -> bool:
    return redis_client is not None
