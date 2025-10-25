#!/usr/bin/env python3
"""Clear Redis cache for user daily usage (fix WRONGTYPE errors)"""

import asyncio
import redis.asyncio as redis
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def clear_cache():
    """Clear all user_daily_usage cache keys"""
    # Connect to Redis
    client = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    
    try:
        # Get all keys matching the pattern
        keys = await client.keys("user_daily_usage:*")
        
        logger.info(f"Found {len(keys)} cache keys")
        
        # Delete all keys
        if keys:
            deleted = await client.delete(*keys)
            logger.info(f"Deleted {deleted} cache keys")
        else:
            logger.info("No keys to delete")
        
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(clear_cache())
