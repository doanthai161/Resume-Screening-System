import redis
from redis.asyncio import Redis
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

redis_client: Redis | None = None

async def init_redis():
    global redis_client
    
    try:
        redis_client = redis.from_url(
            str(settings.REDIS_URL),
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        
        import asyncio
        try:
            await asyncio.wait_for(redis_client.ping(), timeout=2.0)
            logger.info(f" Redis connected successfully to {settings.REDIS_URL}")
        except asyncio.TimeoutError:
            logger.warning(" Redis connection timeout - Redis may not be running")
            redis_client = None
        except Exception as e:
            logger.warning(f" Redis not available: {e}")
            redis_client = None
            
    except Exception as e:
        logger.warning(f" Failed to initialize Redis: {e}")
        redis_client = None

async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None
        logger.info(" Redis connection closed")

def get_redis() -> Redis | None:
    return redis_client

def is_redis_available() -> bool:
    return redis_client is not None