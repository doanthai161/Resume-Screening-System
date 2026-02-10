# test_clear_cache.py
import asyncio
import sys
sys.path.append('.')
from app.core.cache import cache

async def main():
    email = "danieldoan16103@gmail.com"
    
    # Clear cache
    await cache.delete(f"user:email:{email}")
    print(f"✅ Cleared cache for {email}")
    
    # Check if still exists
    cached = await cache.get(f"user:email:{email}")
    if cached:
        print(f"❌ Cache still exists: {cached}")
    else:
        print(f"✅ Cache cleared successfully")
    
    # Close connections
    await cache.close()

if __name__ == "__main__":
    asyncio.run(main())