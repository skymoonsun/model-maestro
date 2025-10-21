"""Configuration management"""

import json
import os
from pathlib import Path
from typing import Dict, Optional
from pydantic_settings import BaseSettings
from functools import lru_cache
from app.redis import RedisManager, CACHE_KEYS, CACHE_TTL


class Settings(BaseSettings):
    """Application settings"""
    ollama_base_url: str = "http://localhost:11434"
    jwt_secret_key: str
    log_level: str = "INFO"
    database_url: str
    admin_token: str
    redis_url: str = "redis://localhost:6379/0"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings"""
    return Settings()


class ModelMappingManager:
    """
    Manage model name mappings
    
    Uses PostgreSQL for storage with Redis cache for performance.
    Falls back to JSON file if DB is unavailable.
    """
    
    def __init__(self, config_path: str = "/app/config/model_mappings.json"):
        self.config_path = config_path
        self._mappings: Dict[str, str] = {}
        self._reverse_mappings: Dict[str, str] = {}
        self._cache_loaded = False
    
    async def _load_from_redis(self) -> bool:
        """Load model mappings from Redis cache"""
        try:
            # Use global redis_manager
            if redis_manager is None:
                return False
            # Try to get from Redis first
            mappings_data = await redis_manager.get(CACHE_KEYS["MODEL_MAPPINGS"])
            reverse_mappings_data = await redis_manager.get(CACHE_KEYS["MODEL_MAPPINGS_REVERSE"])
            
            if mappings_data and reverse_mappings_data:
                self._mappings = mappings_data
                self._reverse_mappings = reverse_mappings_data
                self._cache_loaded = True
                return True
            return False
        except Exception as e:
            print(f"Error loading model mappings from Redis: {e}")
            return False
    
    async def _save_to_redis(self):
        """Save model mappings to Redis cache"""
        try:
            # Use global redis_manager
            if redis_manager is None:
                return
            await redis_manager.set(
                CACHE_KEYS["MODEL_MAPPINGS"], 
                self._mappings, 
                CACHE_TTL["MODEL_MAPPINGS"]
            )
            await redis_manager.set(
                CACHE_KEYS["MODEL_MAPPINGS_REVERSE"], 
                self._reverse_mappings, 
                CACHE_TTL["MODEL_MAPPINGS"]
            )
        except Exception as e:
            print(f"Error saving model mappings to Redis: {e}")
    
    async def _load_from_db(self):
        """Load model mappings from PostgreSQL"""
        try:
            from app.repositories.model_mapping_repository import ModelMappingRepository
            from app.database import async_session_maker
            
            async with async_session_maker() as session:
                repo = ModelMappingRepository(session)
                mappings = await repo.list_all()
                
                self._mappings = {m.display_name: m.real_name for m in mappings}
                self._reverse_mappings = {m.real_name: m.display_name for m in mappings}
                self._cache_loaded = True
                
                # Save to Redis cache
                await self._save_to_redis()
                
        except Exception as e:
            print(f"Error loading model mappings from DB: {e}")
            # Fallback to JSON file
            self._load_from_json()
    
    def _load_from_json(self):
        """Load model mappings from JSON file (fallback)"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._mappings = data.get("mappings", {})
                    self._reverse_mappings = {v: k for k, v in self._mappings.items()}
                    self._cache_loaded = True
        except Exception as e:
            print(f"Error loading model mappings from JSON: {e}")
            self._mappings = {}
            self._reverse_mappings = {}
    
    async def ensure_loaded(self):
        """Ensure mappings are loaded (always fresh from Redis)"""
        # Always try Redis first for fresh data
        if not await self._load_from_redis():
            # If Redis fails, load from DB and save to Redis
            await self._load_from_db()
    
    def get_real_model_name(self, display_name: str) -> str:
        """
        Convert display name to real Ollama model name
        (client -> ollama)
        
        Args:
            display_name: Model name from client (e.g., "gpt-oss:120b")
        
        Returns:
            Real model name for Ollama (e.g., "gpt-oss:120b-cloud")
        """
        return self._mappings.get(display_name, display_name)
    
    def get_display_model_name(self, real_name: str) -> str:
        """
        Convert real Ollama model name to display name
        (ollama -> client)
        
        Args:
            real_name: Model name from Ollama (e.g., "gpt-oss:120b-cloud")
        
        Returns:
            Display model name for client (e.g., "gpt-oss:120b")
        """
        return self._reverse_mappings.get(real_name, real_name)
    
    def get_all_mappings(self) -> Dict[str, str]:
        """Get all model mappings"""
        return self._mappings.copy()
    
    async def reload(self):
        """Reload mappings from database and update Redis cache"""
        self._cache_loaded = False
        await self._load_from_db()
    
    async def invalidate_cache(self):
        """Invalidate Redis cache"""
        try:
            # Use global redis_manager
            if redis_manager is None:
                return
            await redis_manager.delete(CACHE_KEYS["MODEL_MAPPINGS"])
            await redis_manager.delete(CACHE_KEYS["MODEL_MAPPINGS_REVERSE"])
            self._cache_loaded = False
        except Exception as e:
            print(f"Error invalidating Redis cache: {e}")
    
    async def create_mapping(self, display_name: str, real_name: str) -> Dict[str, str]:
        """Create a new model mapping in database"""
        from app.repositories.model_mapping_repository import ModelMappingRepository
        from app.database import async_session_maker
        
        async with async_session_maker() as session:
            repo = ModelMappingRepository(session)
            
            # Check if already exists
            if await repo.exists(display_name):
                raise ValueError(f"Model mapping already exists: {display_name}")
            
            mapping = await repo.create(display_name, real_name)
            await session.commit()
            
            # Update local cache
            self._mappings[display_name] = real_name
            self._reverse_mappings[real_name] = display_name
            
            # Update Redis cache
            await self._save_to_redis()
            
            return {
                "display_name": mapping.display_name,
                "real_name": mapping.real_name,
                "created_at": mapping.created_at.isoformat() if mapping.created_at else None
            }
    
    async def delete_mapping(self, display_name: str):
        """Delete a model mapping from database"""
        from app.repositories.model_mapping_repository import ModelMappingRepository
        from app.database import async_session_maker
        
        async with async_session_maker() as session:
            repo = ModelMappingRepository(session)
            
            mapping = await repo.get_by_display_name(display_name)
            if not mapping:
                raise ValueError(f"Model mapping not found: {display_name}")
            
            await repo.delete(display_name)
            await session.commit()
            
            # Update local cache
            real_name = self._mappings.get(display_name)
            if real_name:
                del self._mappings[display_name]
                if real_name in self._reverse_mappings:
                    del self._reverse_mappings[real_name]
            
            # Update Redis cache
            await self._save_to_redis()
    
    async def list_mappings(self):
        """List all model mappings from database"""
        from app.repositories.model_mapping_repository import ModelMappingRepository
        from app.database import async_session_maker
        
        async with async_session_maker() as session:
            repo = ModelMappingRepository(session)
            mappings = await repo.list_all()
            
            return [
                {
                    "display_name": m.display_name,
                    "real_name": m.real_name,
                    "created_at": m.created_at.isoformat() if m.created_at else None
                }
                for m in mappings
            ]


# Global model mapping manager instance
model_mapper = ModelMappingManager()

# Global Redis manager instance (will be set by main.py)
redis_manager = None

