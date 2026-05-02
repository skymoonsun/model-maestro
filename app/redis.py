"""Redis connection manager for caching"""

import redis.asyncio as redis
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class RedisManager:
    """Redis connection manager with caching utilities"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self.redis_client: Optional[redis.Redis] = None
        self._connected = False
    
    async def connect(self):
        """Connect to Redis"""
        if self._connected:
            return
        
        try:
            self.redis_client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            # Test connection
            await self.redis_client.ping()
            self._connected = True
            logger.info("Connected to Redis successfully")
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            self.redis_client = None
            self._connected = False
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis_client:
            await self.redis_client.close()
            self._connected = False
            logger.info("Disconnected from Redis")
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from Redis"""
        if not self._connected or not self.redis_client:
            return None
        
        try:
            value = await self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Redis GET error for key {key}: {e}")
            return None
    
    async def set(self, key: str, value: Any, expire: Optional[int] = None) -> bool:
        """Set value in Redis"""
        if not self._connected or not self.redis_client:
            return False
        
        try:
            json_value = json.dumps(value)
            if expire is None:
                # No expiration (permanent)
                await self.redis_client.set(key, json_value)
            else:
                # With expiration
                await self.redis_client.set(key, json_value, ex=expire)
            return True
        except Exception as e:
            logger.error(f"Redis SET error for key {key}: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete key from Redis"""
        if not self._connected or not self.redis_client:
            return False
        
        try:
            result = await self.redis_client.delete(key)
            return result > 0
        except Exception as e:
            logger.error(f"Redis DELETE error for key {key}: {e}")
            return False
    
    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern"""
        if not self._connected or not self.redis_client:
            return 0
        
        try:
            keys = await self.redis_client.keys(pattern)
            if keys:
                return await self.redis_client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Redis DELETE PATTERN error for pattern {pattern}: {e}")
            return 0
    
    async def exists(self, key: str) -> bool:
        """Check if key exists in Redis"""
        if not self._connected or not self.redis_client:
            return False
        
        try:
            result = await self.redis_client.exists(key)
            return result > 0
        except Exception as e:
            logger.error(f"Redis EXISTS error for key {key}: {e}")
            return False

# Global Redis manager instance (initialized in main.py)
redis_manager: Optional[RedisManager] = None

# Cache key constants
CACHE_KEYS = {
    "MODEL_MAPPINGS": "model_mappings",
    "MODEL_MAPPINGS_REVERSE": "model_mappings_reverse",
    "USER_MODELS": "user_models:{username}",
    "USER_HAS_ALL_MODELS": "user_has_all_models:{username}",
    "USER_ACCESS": "user_access:{username}",
    "USER_LIMIT": "user_limit:{username}",
    "USER_DAILY_USAGE": "user_daily_usage:{username}:{date}",
    "TOKEN_USERNAME": "token:{token}",
    "MODEL_NODES": "model_nodes:{model_name}",
    "NODE_LOADS": "node_loads",
    "ACTIVE_NODES": "active_nodes",
}

# Cache TTL (Time To Live) in seconds
CACHE_TTL = {
    "MODEL_MAPPINGS": 3600,   # 1 hour — model mappings change rarely
    "USER_MODELS": 300,       # 5 minutes — user model lists
    "USER_HAS_ALL_MODELS": 300,  # 5 minutes
    "USER_ACCESS": 300,       # 5 minutes — user access info
    "USER_LIMIT": 60,         # 1 minute — limits can change frequently
    "USER_DAILY_USAGE": 300,  # 5 minutes — daily usage (key includes date)
    "TOKEN_USERNAME": 3600,   # 1 hour — token validation
    "MODEL_NODES": 120,     # 2 minutes — node list per model
    "NODE_LOADS": 30,       # 30 seconds — load metrics change fast
    "ACTIVE_NODES": 120,    # 2 minutes — active node list
}
