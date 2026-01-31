import redis.asyncio as redis
from app.core.config import settings

class RedisClient:
    def __init__(self):
        self.client = None
        
    async def init_redis(self):
        self.client = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        
    async def set_key(self, key: str, value: str, expire: int = 3600):
        await self.client.setex(key, expire, value)
        
    async def get_key(self, key: str):
        return await self.client.get(key)
        
    async def increment(self, key: str):
        return await self.client.incr(key)
        
    async def close(self):
        await self.client.close()

redis_client = RedisClient()