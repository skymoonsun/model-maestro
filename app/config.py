"""Configuration management"""

import json
import os
from pathlib import Path
from typing import Dict, Optional, List
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings"""
    ollama_base_url: str = "http://localhost:11434"
    jwt_secret_key: str
    log_level: str = "INFO"
    database_url: str
    admin_token: str
    admin_username: str = "admin"
    admin_password: str = "admin"
    redis_url: str = "redis://localhost:6379/0"
    docs_username: str = "admin"
    docs_password: str = "changeme"
    
    # Ollama Official Web Search API
    ollama_web_search_url: str = "https://ollama.com/api/web_search"
    ollama_api_key: Optional[str] = None
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings"""
    return Settings()


def filter_tools_for_model(model_name: str, tools: List[dict]) -> List[dict]:
    """
    Model için tanımlı allowed tools config varsa tools listesini filtreler.
    DB tabanlı ToolSet ve ModelConfig mekanizmasını kullanır.

    Args:
        model_name: İstekteki model adı
        tools: Orijinal tools listesi

    Returns:
        Filtrelenmiş tools listesi (kısıtlama yoksa orijinal döner)
    """
    if not tools:
        return tools
    from app.services import config_manager  # Lazy import to avoid circular dependency
    allowed_names = config_manager.get_model_allowed_tools(model_name)
    
    # None means all tools allowed
    if allowed_names is None:
        return tools

    allowed_set = set(allowed_names)
    filtered = []
    for t in tools:
        if t.get("type") == "function":
            name = t.get("function", {}).get("name")
            if name and name in allowed_set:
                filtered.append(t)
        else:
            filtered.append(t)  # Type not recognized, leave as is
    return filtered


def get_allowed_tools_for_model(model_name: str) -> Optional[List[str]]:
    """
    Model için reduced tools config var mı döner.
    None = tüm tool'lar kullanılır.
    DB üzerinden alınır.
    """
    if not model_name:
        return None
    from app.services import config_manager  # Lazy import to avoid circular dependency
    return config_manager.get_model_allowed_tools(model_name)


# =============================================================================
# CONTEXT LENGTH UTILITIES
# =============================================================================
# Ollama'nın varsayılan num_ctx değeri 4096 token - bu çoğu kullanım için
# yetersiz. Context uzunlukları artık DB'de model_mappings tablosunda saklanır
# ve /admin/model-mappings endpoint'i üzerinden yönetilir.
#
# Cursor bu bilgiyi kullanarak:
# 1. Context kullanım yüzdesini gösterir (ör. "40% - 80K/200K")
# 2. Auto-summarize tetikler (context %90'a ulaşınca)
# 3. Daha verimli context yönetimi yapar

# Varsayılan context uzunluğu (DB'de tanımlı olmayan modeller için)
DEFAULT_CONTEXT_LENGTH = 32768


def parse_context_length(value: str) -> int:
    """
    İnsan-dostu context length formatını token sayısına çevirir.
    
    Desteklenen formatlar:
    - "198K" veya "198k" -> 198 * 1024 = 202752
    - "1M" veya "1m" -> 1 * 1024 * 1024 = 1048576
    - "128K" -> 131072
    - "32768" -> 32768 (düz sayı)
    - "32.5K" -> 33280 (ondalıklı)
    """
    if not value:
        raise ValueError("Context length boş olamaz")
    
    value = value.strip().upper()
    
    if value.endswith('K'):
        num = float(value[:-1])
        return int(num * 1024)
    elif value.endswith('M'):
        num = float(value[:-1])
        return int(num * 1024 * 1024)
    else:
        # Düz sayı
        return int(value)


def format_context_length(tokens: int) -> str:
    """
    Token sayısını insan-dostu formata çevirir.
    
    Örnekler:
    - 202752 -> "198K"
    - 131072 -> "128K"
    - 1048576 -> "1M"
    - 32768 -> "32K"
    """
    if not tokens:
        return None
    
    if tokens >= 1024 * 1024 and tokens % (1024 * 1024) == 0:
        return f"{tokens // (1024 * 1024)}M"
    elif tokens >= 1024:
        k_val = tokens / 1024
        if k_val == int(k_val):
            return f"{int(k_val)}K"
        else:
            return f"{k_val:.1f}K"
    else:
        return str(tokens)


def get_context_length_for_model(model_name: str) -> int:
    """
    Model için yapılandırılmış context uzunluğunu döner.
    Önce DB'deki (cache'deki) tam eşleşmeye bakar,
    bulamazsa DEFAULT_CONTEXT_LENGTH döner.

    Args:
        model_name: Model adı (display name veya real name)

    Returns:
        Context uzunluğu (token cinsinden)
    """
    if not model_name:
        return DEFAULT_CONTEXT_LENGTH
    
    # ModelMappingManager'daki context_lengths cache'ine bak
    ctx = model_mapper.get_context_length(model_name)
    if ctx:
        return ctx
    
    return DEFAULT_CONTEXT_LENGTH


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
        self._reverse_mappings: Dict[str, List[str]] = {}  # One real_name can have multiple display_names
        self._context_lengths: Dict[str, int] = {}  # display_name -> context_length (token)
        self._capabilities: Dict[str, List[str]] = {}  # display_name -> capabilities
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
                
                # Handle both old format (string) and new format (list) for reverse_mappings
                reverse_mappings_raw = data.get("reverse_mappings", {})
                self._reverse_mappings = {}
                
                for real_name, display_names in reverse_mappings_raw.items():
                    if isinstance(display_names, str):
                        # Old format: convert string to list
                        self._reverse_mappings[real_name] = [display_names]
                    elif isinstance(display_names, list):
                        # New format: use as-is
                        self._reverse_mappings[real_name] = display_names
                    else:
                        # Invalid format: skip
                        continue
                
                self._cache_loaded = True
                print(f"Loaded {len(self._mappings)} model mappings from cache file")
                
                # Load context lengths and capabilities
                self._context_lengths = data.get("context_lengths", {})
                self._capabilities = data.get("capabilities", {})
                if self._context_lengths:
                    print(f"Loaded {len(self._context_lengths)} context length configs from cache file")
                if self._capabilities:
                    print(f"Loaded {len(self._capabilities)} capabilities configs from cache file")
                
                return True
        except Exception as e:
            print(f"Error loading model mappings from cache file: {e}")
            return False
    
    def _save_to_cache_file(self):
        """Save model mappings to JSON cache file"""
        try:
            data = {
                "mappings": self._mappings,
                "reverse_mappings": self._reverse_mappings,
                "context_lengths": self._context_lengths,
                "capabilities": self._capabilities
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
                
                # Build reverse mapping: one real_name can have multiple display_names
                self._reverse_mappings = {}
                for m in mappings:
                    if m.real_name not in self._reverse_mappings:
                        self._reverse_mappings[m.real_name] = []
                    self._reverse_mappings[m.real_name].append(m.display_name)
                
                # Build context lengths dict
                self._context_lengths = {
                    m.display_name: m.context_length 
                    for m in mappings 
                    if m.context_length
                }

                # Build capabilities dict
                self._capabilities = {
                    m.display_name: m.capabilities
                    for m in mappings
                    if m.capabilities
                }
                
                self._cache_loaded = True
                
                # Save to cache file
                self._save_to_cache_file()
                
                print(f"Loaded {len(self._mappings)} model mappings from database")
                if self._context_lengths:
                    print(f"Loaded {len(self._context_lengths)} context length configs from database")
                
        except Exception as e:
            print(f"Error loading model mappings from DB: {e}")
            # No fallback - mappings will be empty
            self._mappings = {}
            self._reverse_mappings = {}
            self._context_lengths = {}
            self._capabilities = {}
    
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
        
        If multiple display names map to the same real name, returns the first one.
        
        Args:
            real_name: Model name from Ollama (e.g., "gpt-oss:120b-cloud")
        
        Returns:
            Display model name for client (e.g., "gpt-oss:120b")
        """
        display_names = self._reverse_mappings.get(real_name, [])
        if display_names:
            return display_names[0]  # Return first display name
        return real_name  # No mapping found, return as-is
    
    def get_all_display_names_for_real_name(self, real_name: str) -> List[str]:
        """
        Get all display names that map to a given real name
        
        Args:
            real_name: Real Ollama model name (e.g., "qwen2.5-coder:latest")
        
        Returns:
            List of display names (e.g., ["qwen3-coder:480b", "qwen3-coder:780b"])
            If no mappings exist, returns [real_name]
        """
        display_names = self._reverse_mappings.get(real_name, [])
        if display_names:
            return display_names
        return [real_name]  # No mapping found, return real_name as single item
    
    def get_all_mappings(self) -> Dict[str, str]:
        """Get all model mappings"""
        return self._mappings.copy()
    
    def get_context_length(self, model_name: str) -> Optional[int]:
        """
        Model için context uzunluğunu döner.
        Önce display_name ile, sonra real_name ile arar.
        
        Args:
            model_name: Model adı (display name veya real name)
        
        Returns:
            Context uzunluğu (token cinsinden) veya None
        """
        # Direkt display_name ile ara
        if model_name in self._context_lengths:
            return self._context_lengths[model_name]
        
        # real_name ile ara (reverse mapping ile display_name bul)
        display_names = self._reverse_mappings.get(model_name, [])
        for dn in display_names:
            if dn in self._context_lengths:
                return self._context_lengths[dn]
        
        return None
    
    def get_capabilities(self, model_name: str) -> Optional[List[str]]:
        """
        Model için capabilities listesini döner ("completion", "tools", vb.).
        Önce display_name ile, sonra real_name ile arar.
        """
        # Direkt display_name ile ara
        if model_name in self._capabilities:
            return self._capabilities[model_name]
        
        # real_name ile ara (reverse mapping ile display_name bul)
        display_names = self._reverse_mappings.get(model_name, [])
        for dn in display_names:
            if dn in self._capabilities:
                return self._capabilities[dn]
        
        return None
    
    def get_all_context_lengths(self) -> Dict[str, int]:
        """Get all context lengths"""
        return self._context_lengths.copy()
    
    async def reload(self):
        """Reload mappings from database"""
        self._cache_loaded = False
        await self._load_from_db()
    
    async def invalidate_cache(self):
        """Invalidate cache (force reload from DB)"""
        self._cache_loaded = False
    
    async def create_or_update_mapping(self, display_name: str, real_name: str, context_length: Optional[int] = None, capabilities: Optional[List[str]] = None) -> Dict[str, any]:
        """
        Create or update a model mapping in database (upsert).
        Var olan mapping'i günceller, yoksa yeni oluşturur.
        """
        from app.repositories.model_mapping_repository import ModelMappingRepository
        from app.database import async_session_maker
        
        async with async_session_maker() as session:
            repo = ModelMappingRepository(session)
            
            mapping, is_new = await repo.upsert(display_name, real_name, context_length, capabilities)
            
            # Update local cache - önce eski reverse mapping'i temizle
            old_real_name = self._mappings.get(display_name)
            if old_real_name and old_real_name != real_name:
                # Real name değiştiyse eski reverse mapping'den kaldır
                if old_real_name in self._reverse_mappings:
                    if display_name in self._reverse_mappings[old_real_name]:
                        self._reverse_mappings[old_real_name].remove(display_name)
                    if not self._reverse_mappings[old_real_name]:
                        del self._reverse_mappings[old_real_name]
            
            # Set new mapping
            self._mappings[display_name] = real_name
            
            # Update reverse mapping
            if real_name not in self._reverse_mappings:
                self._reverse_mappings[real_name] = []
            if display_name not in self._reverse_mappings[real_name]:
                self._reverse_mappings[real_name].append(display_name)
            
            # Update context length cache
            if context_length:
                self._context_lengths[display_name] = context_length
            elif display_name in self._context_lengths and context_length is None:
                # context_length gönderilmediyse mevcut değeri koru (update'de)
                pass
            
            # Save to cache file
            self._save_to_cache_file()
            
            action = "Created" if is_new else "Updated"
            ctx_info = f" (ctx={context_length})" if context_length else ""
            print(f"{action} mapping: {display_name} -> {real_name}{ctx_info}")
            
            return {
                "display_name": mapping.display_name,
                "real_name": mapping.real_name,
                "context_length": mapping.context_length,
                "capabilities": mapping.capabilities,
                "created_at": mapping.created_at.isoformat() if mapping.created_at else None,
                "is_new": is_new
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
                
                # Remove display_name from reverse mapping list
                if real_name in self._reverse_mappings:
                    if display_name in self._reverse_mappings[real_name]:
                        self._reverse_mappings[real_name].remove(display_name)
                    # If list becomes empty, remove the key
                    if not self._reverse_mappings[real_name]:
                        del self._reverse_mappings[real_name]
            
            # Remove context length
            if display_name in self._context_lengths:
                del self._context_lengths[display_name]
            
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
                    "context_length": m.context_length,
                    "capabilities": m.capabilities,
                    "created_at": m.created_at.isoformat() if m.created_at else None
                }
                for m in mappings
            ]


# Global model mapping manager instance
model_mapper = ModelMappingManager()

# Global Redis manager instance (will be set by main.py)
from app.redis import RedisManager
redis_manager = None
