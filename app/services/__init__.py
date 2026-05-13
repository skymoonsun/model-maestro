"""
Configuration Manager - DB-backed configuration with Redis caching and hot reload.

Replaces hardcoded values in config.py, main.py, background_tasks.py, and proxy.py
with database-driven configuration.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Optional, List, Any

from app.database import async_session_maker
from app.repositories.system_config_repository import SystemConfigRepository
from app.repositories.model_config_repository import ModelConfigRepository
from app.repositories.tool_set_repository import ToolSetRepository

logger = logging.getLogger(__name__)


# Default system config values (fallback if DB is empty)
DEFAULT_SYSTEM_CONFIG = {
    "background_tasks.batch_size": "50",
    "background_tasks.poll_interval": "2.0",
    "background_tasks.queue_key": "activity_log_queue",
    "http_client.max_connections": "100",
    "http_client.max_keepalive_connections": "40",
    "http_client.keepalive_expiry": "300",
    "http_client.timeout": "1200",
    "defaults.context_length": "32768",
    "defaults.log_level": "INFO",
    "search.web_search_url": "https://ollama.com/api/web_search",
    "search.web_search_api_key": "",
}

# Default ollama unsupported params
DEFAULT_OLLAMA_UNSUPPORTED_PARAMS = [
    "logit_bias", "logprobs", "top_logprobs", "top_k",
    "response_format", "user", "service_tier",
    "parallel_tool_calls", "store", "metadata",
    "prediction", "modalities", "audio",
]


class ConfigManager:
    """
    Centralized configuration manager.
    
    Loads config from DB, caches in Redis, and provides hot-reload capability.
    Falls back to defaults if DB is empty.
    """
    
    _instance = None
    _config_cache: Dict[str, str] = {}
    _model_config_cache: Dict[str, Dict] = {}
    _tool_set_cache: Dict[str, List[str]] = {}
    _last_reload: Optional[datetime] = None
    _reload_interval: int = 60  # seconds
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def load_all(self):
        """Load all configuration from database"""
        try:
            await self._load_system_config()
            await self._load_model_configs()
            await self._load_tool_sets()
            self._last_reload = datetime.now()
            logger.info("Configuration loaded from database")
        except Exception as e:
            logger.warning(f"Failed to load config from DB, using defaults: {e}")

    async def ensure_defaults(self):
        """Ensure all default config keys exist in DB (auto-seed missing ones)"""
        try:
            added = 0
            async with async_session_maker() as session:
                repo = SystemConfigRepository(session)
                for key, value in DEFAULT_SYSTEM_CONFIG.items():
                    existing = await repo.get_by_key(key)
                    if not existing:
                        await repo.upsert(key, value)
                        added += 1
                await session.commit()
            if added:
                logger.info(f"Auto-seeded {added} missing config keys")
                await self.load_all()
        except Exception as e:
            logger.warning(f"Failed to auto-seed defaults: {e}")
    
    async def _load_system_config(self):
        """Load system config from DB into cache"""
        async with async_session_maker() as session:
            repo = SystemConfigRepository(session)
            configs = await repo.get_all()
            
            # Start with defaults
            self._config_cache = dict(DEFAULT_SYSTEM_CONFIG)
            
            # Override with DB values
            for config in configs:
                self._config_cache[config.key] = config.value
    
    async def _load_model_configs(self):
        """Load model configs from DB into cache"""
        async with async_session_maker() as session:
            repo = ModelConfigRepository(session)
            configs = await repo.get_active()
            
            self._model_config_cache = {}
            for config in configs:
                self._model_config_cache[config.model_prefix.lower()] = {
                    "id": config.id,
                    "model_prefix": config.model_prefix,
                    "allowed_tools": config.allowed_tools,
                    "unsupported_params": config.unsupported_params,
                    "default_context_length": config.default_context_length,
                    "max_context_length": config.max_context_length,
                    "requests_per_minute": config.requests_per_minute,
                    "tokens_per_minute": config.tokens_per_minute,
                    "is_active": config.is_active,
                    "maintenance_mode": config.maintenance_mode,
                    "cost_multiplier": float(config.cost_multiplier) if config.cost_multiplier else 1.0,
                }
    
    async def _load_tool_sets(self):
        """Load tool sets from DB into cache"""
        async with async_session_maker() as session:
            repo = ToolSetRepository(session)
            tool_sets = await repo.get_all()
            
            self._tool_set_cache = {}
            for ts in tool_sets:
                self._tool_set_cache[ts.name] = ts.tools
    
    def _should_reload(self) -> bool:
        """Check if config should be reloaded"""
        if self._last_reload is None:
            return True
        return (datetime.now() - self._last_reload).seconds > self._reload_interval
    
    async def ensure_loaded(self):
        """Ensure config is loaded, reload if stale"""
        if self._should_reload():
            await self.load_all()
    
    async def invalidate(self):
        """Force reload on next access"""
        self._last_reload = None
        
        # Also clear Redis cache
        from app.redis import redis_manager
        if redis_manager:
            await redis_manager.delete("config:system")
            await redis_manager.delete("config:model_configs")
            await redis_manager.delete("config:tool_sets")
        
        logger.info("Configuration cache invalidated")
    
    # =========================================================================
    # System Config Accessors
    # =========================================================================
    
    def get(self, key: str, default: str = "") -> str:
        """Get a system config value"""
        return self._config_cache.get(key, default)
    
    def get_int(self, key: str, default: int = 0) -> int:
        """Get a system config value as integer"""
        try:
            return int(self._config_cache.get(key, str(default)))
        except (ValueError, TypeError):
            return default
    
    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get a system config value as float"""
        try:
            return float(self._config_cache.get(key, str(default)))
        except (ValueError, TypeError):
            return default
    
    def get_list(self, key: str, default: Optional[List[str]] = None) -> List[str]:
        """Get a system config value as list (stored as JSON)"""
        try:
            value = self._config_cache.get(key)
            if value:
                return json.loads(value)
            return default or []
        except (ValueError, TypeError, json.JSONDecodeError):
            return default or []
    
    def get_grouped_config(self) -> Dict[str, Any]:
        """Get all system config grouped by category"""
        result = {
            "background_tasks": {},
            "http_client": {},
            "defaults": {},
            "search": {},
            "tunnel": {},
            "ollama_unsupported_params": self.get_ollama_unsupported_params(),
        }

        for key, value in self._config_cache.items():
            parts = key.split(".", 1)
            if len(parts) == 2 and parts[0] in result:
                # Try to convert to number
                try:
                    converted = int(value)
                except ValueError:
                    try:
                        converted = float(value)
                    except ValueError:
                        converted = value
                result[parts[0]][parts[1]] = converted

        return result
    
    def get_ollama_unsupported_params(self) -> List[str]:
        """Get Ollama unsupported parameters"""
        params = self._config_cache.get("ollama_unsupported_params")
        if params:
            try:
                return json.loads(params)
            except json.JSONDecodeError:
                pass
        return list(DEFAULT_OLLAMA_UNSUPPORTED_PARAMS)
    
    # =========================================================================
    # Model Config Accessors
    # =========================================================================
    
    def get_model_config(self, model_name: str) -> Optional[Dict]:
        """Get model config by prefix matching"""
        model_name_lower = model_name.lower()
        
        best_match = None
        best_len = 0
        
        for prefix, config in self._model_config_cache.items():
            if model_name_lower.startswith(prefix) and len(prefix) > best_len:
                best_match = config
                best_len = len(prefix)
        
        return best_match
    
    def get_all_model_configs(self) -> Dict[str, Dict]:
        """Get all model configs"""
        return dict(self._model_config_cache)
    
    def get_model_unsupported_params(self, model_name: str) -> List[str]:
        """Get unsupported params for a model"""
        config = self.get_model_config(model_name)
        if config and config.get("unsupported_params"):
            return config["unsupported_params"]
        return []
    
    def get_model_allowed_tools(self, model_name: str) -> Optional[List[str]]:
        """Get allowed tools for a model (None = all tools allowed)"""
        config = self.get_model_config(model_name)
        if config:
            return config.get("allowed_tools")
        return None
    
    def is_model_in_maintenance(self, model_name: str) -> bool:
        """Check if model is in maintenance mode"""
        config = self.get_model_config(model_name)
        if config:
            return config.get("maintenance_mode", False)
        return False
    
    # =========================================================================
    # Tool Set Accessors
    # =========================================================================
    
    def get_tool_set(self, name: str) -> Optional[List[str]]:
        """Get tools in a tool set"""
        return self._tool_set_cache.get(name)
    
    def get_all_tool_sets(self) -> Dict[str, List[str]]:
        """Get all tool sets"""
        return dict(self._tool_set_cache)
    
    # =========================================================================
    # Update Operations (with cache invalidation)
    # =========================================================================
    
    async def update_system_config(self, updates: Dict[str, Any]):
        """Update system config values in DB"""
        async with async_session_maker() as session:
            repo = SystemConfigRepository(session)
            
            for key, value in updates.items():
                if isinstance(value, (list, dict)):
                    value = json.dumps(value)
                else:
                    value = str(value)
                await repo.upsert(key, value)
            
            await session.commit()
        
        await self.invalidate()
        await self.load_all()


# Global config manager instance
config_manager = ConfigManager()
