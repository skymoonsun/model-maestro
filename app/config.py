"""Configuration management"""

import json
import os
from pathlib import Path
from typing import Dict, Optional
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings"""
    ollama_base_url: str = "http://localhost:11434"
    jwt_secret_key: str
    log_level: str = "INFO"
    database_url: str
    admin_token: str
    redis_url: str = "redis://localhost:6379/0"
    docs_username: str = "admin"
    docs_password: str = "changeme"
    
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
    
    Uses PostgreSQL for storage with JSON file cache for performance.
    """
    
    def __init__(self, cache_dir: Optional[str] = None):
        # Auto-detect cache directory: /app/cache for Docker, ./cache for local
        if cache_dir is None:
            if os.path.exists("/app"):
                cache_dir = "/app/cache"
            else:
                # Local development
                cache_dir = os.path.join(os.getcwd(), "cache")
        
        self.cache_dir = cache_dir
        self.cache_file = os.path.join(cache_dir, "model_mappings.json")
        self._mappings: Dict[str, str] = {}
        self._reverse_mappings: Dict[str, str] = {}
        self._cache_loaded = False
        
        # Ensure cache directory exists
        os.makedirs(cache_dir, exist_ok=True)
        print(f"Using cache directory: {cache_dir}")
    
    def _load_from_cache_file(self) -> bool:
        """Load model mappings from JSON cache file"""
        try:
            if not os.path.exists(self.cache_file):
                return False
            
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._mappings = data.get("mappings", {})
                self._reverse_mappings = data.get("reverse_mappings", {})
                self._cache_loaded = True
                print(f"Loaded {len(self._mappings)} model mappings from cache file")
                return True
        except Exception as e:
            print(f"Error loading model mappings from cache file: {e}")
            return False
    
    def _save_to_cache_file(self):
        """Save model mappings to JSON cache file"""
        try:
            data = {
                "mappings": self._mappings,
                "reverse_mappings": self._reverse_mappings
            }
            
            # Write to temporary file first, then rename (atomic operation)
            temp_file = f"{self.cache_file}.tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            # Atomic rename
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
            os.rename(temp_file, self.cache_file)
            
            print(f"Saved {len(self._mappings)} model mappings to cache file")
        except Exception as e:
            print(f"Error saving model mappings to cache file: {e}")
            # Try to clean up temp file if it exists
            temp_file = f"{self.cache_file}.tmp"
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
    
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
                
                # Save to cache file
                self._save_to_cache_file()
                
                print(f"Loaded {len(self._mappings)} model mappings from database")
                
        except Exception as e:
            print(f"Error loading model mappings from DB: {e}")
            # No fallback - mappings will be empty
            self._mappings = {}
            self._reverse_mappings = {}
    
    async def ensure_loaded(self):
        """Ensure mappings are loaded (from cache file or DB on first load)"""
        # Only load once
        if not self._cache_loaded:
            # Try cache file first (fastest)
            if not self._load_from_cache_file():
                # Cache file empty/failed, load from DB and populate cache
                await self._load_from_db()
    
    def get_real_model_name(self, display_name: str) -> str:
        """
        Convert display name to real Ollama model name
        (client -> ollama)
        
        Args:
            display_name: Model name from client (e.g., "gpt-oss:120b" or "glm-4.6")
        
        Returns:
            Real model name for Ollama (e.g., "gpt-oss:120b-cloud")
        
        Note:
            If display_name has no tag (no ':'), try with ':latest' suffix as well.
            Ollama automatically appends ':latest' to tagless model names.
        """
        # Try exact match first
        if display_name in self._mappings:
            return self._mappings[display_name]
        
        # If no ':' in name, try with ':latest' suffix
        # (Ollama treats "glm-4.6" as "glm-4.6:latest")
        if ':' not in display_name:
            latest_name = f"{display_name}:latest"
            if latest_name in self._mappings:
                return self._mappings[latest_name]
        
        # No mapping found, return as-is
        return display_name
    
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
        """Reload mappings from database"""
        self._cache_loaded = False
        await self._load_from_db()
    
    async def invalidate_cache(self):
        """Invalidate cache (force reload from DB)"""
        self._cache_loaded = False
    
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
            
            # Save to cache file
            self._save_to_cache_file()
            
            print(f"Created mapping: {display_name} -> {real_name}")
            
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
            
            # Save to cache file
            self._save_to_cache_file()
            
            print(f"Deleted mapping: {display_name}")
    
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
from app.redis import RedisManager
redis_manager = None
