"""Configuration management"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional, List, Any, Tuple
from pydantic_settings import BaseSettings
from functools import lru_cache

logger = logging.getLogger(__name__)


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

    # Google OAuth for Antigravity nodes
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_redirect_uri: str = "http://localhost:3000/admin/oauth/callback"

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
        self._mapping_node_ids: Dict[str, List[int]] = {}  # display_name -> restricted node ids (empty entry = global)
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
                self._mapping_node_ids = data.get("mapping_node_ids", {})
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
                "capabilities": self._capabilities,
                "mapping_node_ids": self._mapping_node_ids,
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

                self._mapping_node_ids = {}
                for m in mappings:
                    if m.nodes:
                        self._mapping_node_ids[m.display_name] = sorted({n.id for n in m.nodes})

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
            self._mapping_node_ids = {}
    
    async def ensure_loaded(self):
        """
        Load model mappings once per process.

        PostgreSQL is the source of truth. The JSON file under cache_dir is a
        write-through snapshot (see _load_from_db / create_or_update_mapping);

        loading from file first caused stale mappings when DB was updated but the
        on-disk file still held an older snapshot (e.g. Docker volume).
        """
        if self._cache_loaded:
            return
        await self._load_from_db()
        if not self._cache_loaded:
            # DB unreachable or failed — last resort: snapshot from previous successful sync
            if self._load_from_cache_file():
                print(
                    "Model mappings: loaded from JSON cache file (database unavailable); "
                    "mappings may be stale until DB is reachable."
                )
            else:
                print("Model mappings: database load failed and no cache file; mappings empty")

    def get_restricted_node_ids(self, display_name: str) -> Optional[List[int]]:
        """
        If this display name has node restrictions in DB, return those node ids.
        Returns None when the mapping is global (any node).
        """
        key = self._mapping_lookup_key(display_name)
        ids = self._mapping_node_restrictions(display_name, key)
        if not ids:
            return None
        return list(ids)

    def _mapping_lookup_key(self, display_name: str) -> Optional[str]:
        """Internal key used in `_mappings` / `_mapping_node_ids` for this client-visible name."""
        if display_name in self._mappings:
            return display_name
        if ':' not in display_name:
            latest_name = f"{display_name}:latest"
            if latest_name in self._mappings:
                return latest_name
        return None

    def _mapping_node_restrictions(
        self, incoming_display_name: str, mapping_key: Optional[str]
    ) -> Optional[List[int]]:
        """
        Junction table is keyed by ORM ``ModelMapping.display_name``. Client/group names may differ
        only by ``:latest`` (e.g. row stored as ``foo:latest`` vs member ``foo``). Align keys so a
        configured restriction is never mistaken as "missing" (which would apply mapping globally).

        Returns:
            None if no junction row matched any alias (mapping applies on every node).
            Non-empty list: mapping applies only on these node ids.
        """
        candidates: List[str] = []
        seen: set[str] = set()

        def add(name: Optional[str]) -> None:
            if not name or name in seen:
                return
            seen.add(name)
            candidates.append(name)

        add(mapping_key)
        add(incoming_display_name)

        def add_latest_variant(name: Optional[str]) -> None:
            if not name:
                return
            if name.endswith(":latest"):
                add(name[: -len(":latest")])
            elif ":" not in name:
                add(f"{name}:latest")

        add_latest_variant(mapping_key)
        add_latest_variant(incoming_display_name)

        for c in candidates:
            if c in self._mapping_node_ids:
                return self._mapping_node_ids[c]
        return None

    def get_real_model_name_for_node(
        self, display_name: str, selected_node_id: Optional[int]
    ) -> str:
        """
        Map display -> real only when appropriate for the outbound node.

        If the mapping row limits nodes (junction non-empty), apply ``real_name`` only when
        ``selected_node_id`` is in that set. Otherwise forward ``display_name`` so catalogs
        that use the client-visible tag stay aligned.

        Missing junction entry or empty junction list means global mapping (same as
        ``get_real_model_name``).
        """
        key = self._mapping_lookup_key(display_name)
        if key is None:
            return display_name
        real = self._mappings[key]
        restricted = self._mapping_node_restrictions(display_name, key)
        if restricted is None or not restricted:
            return real
        if selected_node_id is not None and selected_node_id in restricted:
            return real
        return display_name

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
        key = self._mapping_lookup_key(display_name)
        if key is None:
            return display_name
        return self._mappings[key]
    
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
    
    async def create_or_update_mapping(
        self,
        display_name: str,
        real_name: str,
        context_length: Optional[int] = None,
        capabilities: Optional[List[str]] = None,
        node_ids: Optional[List[int]] = None,
    ) -> Dict[str, any]:
        """
        Create or update a model mapping in database (upsert).
        Var olan mapping'i günceller, yoksa yeni oluşturur.
        """
        from app.repositories.model_mapping_repository import ModelMappingRepository
        from app.database import async_session_maker

        async with async_session_maker() as session:
            repo = ModelMappingRepository(session)

            mapping, is_new = await repo.upsert(display_name, real_name, node_ids, context_length, capabilities)

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

            if node_ids is not None:
                if node_ids:
                    self._mapping_node_ids[display_name] = sorted(set(node_ids))
                else:
                    self._mapping_node_ids.pop(display_name, None)

            # Save to cache file
            self._save_to_cache_file()

            action = "Created" if is_new else "Updated"
            ctx_info = f" (ctx={context_length})" if context_length else ""
            node_info = f" (nodes={node_ids})" if node_ids else ""
            print(f"{action} mapping: {display_name} -> {real_name}{node_info}{ctx_info}")

            nids = self._mapping_node_ids.get(display_name, [])
            return {
                "display_name": mapping.display_name,
                "real_name": mapping.real_name,
                "node_ids": nids,
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
            if display_name in self._mapping_node_ids:
                del self._mapping_node_ids[display_name]

            # Save to cache file
            self._save_to_cache_file()

            print(f"Deleted mapping: {display_name}")

    async def update_mapping(
        self,
        old_display_name: str,
        new_display_name: Optional[str] = None,
        real_name: Optional[str] = None,
        context_length: Optional[int] = None,
        capabilities: Optional[List[str]] = None,
        node_ids: Optional[List[int]] = None,
    ) -> Dict[str, any]:
        """
        Mevcut bir mapping'i güncelle. display_name de dahil tüm alanlar değiştirilebilir.
        Cache'i de senkronize eder.
        """
        from app.repositories.model_mapping_repository import ModelMappingRepository
        from app.database import async_session_maker

        async with async_session_maker() as session:
            repo = ModelMappingRepository(session)
            mapping = await repo.get_by_display_name(old_display_name)
            if not mapping:
                raise ValueError(f"Model mapping not found: {old_display_name}")

            # Eğer yeni display_name farklıysa ve zaten varsa hata at
            if new_display_name and new_display_name != old_display_name:
                existing = await repo.get_by_display_name(new_display_name)
                if existing:
                    raise ValueError(f"Display name already exists: {new_display_name}")
                mapping.display_name = new_display_name

            if real_name is not None:
                mapping.real_name = real_name
            if context_length is not None:
                mapping.context_length = context_length
            if capabilities is not None:
                mapping.capabilities = capabilities
            if node_ids is not None:
                await repo.sync_mapping_nodes(mapping, node_ids)

            await session.commit()
            await session.refresh(mapping)

            # Cache güncelle — önce eski entry'leri temizle
            old_real_name = self._mappings.get(old_display_name)
            if old_real_name:
                del self._mappings[old_display_name]
                if old_real_name in self._reverse_mappings:
                    if old_display_name in self._reverse_mappings[old_real_name]:
                        self._reverse_mappings[old_real_name].remove(old_display_name)
                    if not self._reverse_mappings[old_real_name]:
                        del self._reverse_mappings[old_real_name]

            if old_display_name in self._context_lengths:
                del self._context_lengths[old_display_name]
            if old_display_name in self._capabilities:
                del self._capabilities[old_display_name]
            self._mapping_node_ids.pop(old_display_name, None)

            # Yeni değerleri cache'e ekle
            final_display_name = new_display_name or old_display_name
            self._mappings[final_display_name] = mapping.real_name

            if mapping.real_name not in self._reverse_mappings:
                self._reverse_mappings[mapping.real_name] = []
            if final_display_name not in self._reverse_mappings[mapping.real_name]:
                self._reverse_mappings[mapping.real_name].append(final_display_name)

            if mapping.context_length:
                self._context_lengths[final_display_name] = mapping.context_length
            if mapping.capabilities:
                self._capabilities[final_display_name] = mapping.capabilities

            if node_ids is not None:
                if node_ids:
                    self._mapping_node_ids[final_display_name] = sorted(set(node_ids))
                else:
                    self._mapping_node_ids.pop(final_display_name, None)

            self._save_to_cache_file()

            print(f"Updated mapping: {old_display_name} -> {final_display_name}")

            nids = sorted(set(node_ids)) if node_ids is not None else self._mapping_node_ids.get(final_display_name, [])
            return {
                "display_name": mapping.display_name,
                "real_name": mapping.real_name,
                "node_ids": nids,
                "context_length": mapping.context_length,
                "capabilities": mapping.capabilities,
                "created_at": mapping.created_at.isoformat() if mapping.created_at else None,
            }

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
                    "node_ids": sorted({n.id for n in m.nodes}) if m.nodes else [],
                    "context_length": m.context_length,
                    "capabilities": m.capabilities,
                    "created_at": m.created_at.isoformat() if m.created_at else None
                }
                for m in mappings
            ]


class ModelGroupManager:
    """
    Manage model groups for dynamic model selection with fallback chains.

    Model groups allow routing requests to different models based on:
    - Capabilities (vision support, code generation, etc.)
    - Strategy (round_robin, weighted, priority)
    - Fallback chains (if one model fails, try the next)

    Groups are stored in DB and cached in memory for fast lookups.
    """

    def __init__(self):
        # Cache: group_name -> {group: ModelGroup, members: List[ModelGroupMember]}
        self._groups: Dict[str, Dict[str, Any]] = {}
        # Cache: group_name -> round_robin_index
        self._round_robin_indices: Dict[str, int] = {}
        # Cache: member_id -> {node_id: priority} (per-member node priority override)
        self._member_node_prio: Dict[int, Dict[int, int]] = {}
        # Track loaded state
        self._cache_loaded = False

    async def ensure_loaded(self):
        """Load groups from database (once on startup or after cache invalidation)"""
        if self._cache_loaded:
            return

        try:
            from app.repositories.model_group_repository import ModelGroupRepository
            from app.database import async_session_maker
            from app.models_db import model_group_member_nodes
            from sqlalchemy import select as _select

            async with async_session_maker() as session:
                repo = ModelGroupRepository(session)
                groups = await repo.get_all_groups(active_only=True)

                self._groups = {}
                all_member_ids: List[int] = []
                for group in groups:
                    members = await repo.get_members_by_group_name(group.name)
                    active_members = [m for m in members if m.is_active]
                    self._groups[group.name] = {
                        "group": group,
                        "members": active_members,
                    }
                    all_member_ids.extend(m.id for m in active_members)

                # Cache per-(member, node) priorities so routing can override the global
                # node priority for a member without lazy-loading the association.
                self._member_node_prio = {}
                if all_member_ids:
                    rows = await session.execute(
                        _select(
                            model_group_member_nodes.c.member_id,
                            model_group_member_nodes.c.node_id,
                            model_group_member_nodes.c.priority,
                        ).where(model_group_member_nodes.c.member_id.in_(all_member_ids))
                    )
                    for mid, nid, prio in rows.all():
                        self._member_node_prio.setdefault(mid, {})[int(nid)] = int(prio or 0)

                self._cache_loaded = True
                print(f"[ModelGroupManager] Loaded {len(self._groups)} model groups from database")

        except Exception as e:
            print(f"[ModelGroupManager] Error loading groups from DB: {e}")
            self._groups = {}
            self._member_node_prio = {}

    def is_group(self, model_name: str) -> bool:
        """Check if a model name is a group"""
        return model_name in self._groups

    def _detect_vision_request(self, messages: List[Dict[str, Any]]) -> bool:
        """
        Detect if request contains images (vision capability needed).

        Checks for:
        - image_url content in messages
        - base64 encoded images in content

        Args:
            messages: List of chat messages

        Returns:
            True if vision capability is needed
        """
        if not messages:
            return False

        for message in messages:
            content = message.get("content")
            if not content:
                continue

            # Handle string content (might contain base64 image)
            if isinstance(content, str):
                # Check for base64 image data URL pattern
                if "data:image/" in content:
                    return True
                continue

            # Handle list content (OpenAI format with image_url)
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue

                    # Check for image_url type
                    if part.get("type") == "image_url":
                        return True

                    # Check for image base64 in text
                    if part.get("type") == "text" and isinstance(part.get("text"), str):
                        if "data:image/" in part.get("text", ""):
                            return True

                    # Check for image field
                    if "image" in part:
                        return True

        return False

    def _select_by_capability(
        self, members: List[Any], needs_vision: bool
    ) -> Optional[Any]:
        """
        Select a member only when vision is required.

        Non-vision requests use ``_select_by_strategy`` so priority / round_robin /
        weighted behave as configured.
        """
        if not members or not needs_vision:
            return None

        vision_members = [
            m
            for m in members
            if m.capability_tags and "vision" in m.capability_tags
        ]
        if vision_members:
            return min(vision_members, key=lambda m: m.priority)

        logger.warning(
            "[ModelGroupManager] Vision-capable content detected but no active member "
            "has capability_tags including 'vision'; falling back to group strategy selection."
        )
        return None

    def _select_by_strategy(
        self, members: List[Any], strategy: str, group_name: str
    ) -> Optional[Any]:
        """
        Select a member based on the group's strategy.

        Strategies:
        - round_robin: Cycle through members in order
        - weighted: Random selection based on weights
        - priority: Always select highest priority (lowest number)

        Args:
            members: List of ModelGroupMember objects
            strategy: Selection strategy
            group_name: Group name (for round_robin state tracking)

        Returns:
            Selected ModelGroupMember or None
        """
        if not members:
            return None

        if strategy == "priority":
            # Always select lowest priority number
            return min(members, key=lambda m: m.priority)

        elif strategy == "weighted":
            # Weighted random selection
            import random

            total_weight = sum(m.weight for m in members)
            if total_weight == 0:
                return members[0]

            r = random.uniform(0, total_weight)
            cumulative = 0
            for member in members:
                cumulative += member.weight
                if r <= cumulative:
                    return member
            return members[-1]

        else:  # round_robin (default)
            # Cycle through members
            current_index = self._round_robin_indices.get(group_name, 0)
            sorted_members = sorted(members, key=lambda m: m.priority)
            selected = sorted_members[current_index % len(sorted_members)]
            self._round_robin_indices[group_name] = current_index + 1
            return selected

    async def resolve_model_with_metadata(
        self, model_name: str, request_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Optional[List[int]], Optional[Dict[int, int]]]:
        """
        Resolve a group slug to a member ``model_display_name`` and that member's preferred nodes.

        **Member selection** follows ``group.strategy``:

        - ``priority``: always the active member with the lowest ``priority`` number.
        - ``round_robin``: cycles through members ordered by ``priority``.
        - ``weighted``: weighted random choice among active members.

        If the request needs vision (images), a vision-tagged member is chosen first
        (among those, lowest ``priority``); if none qualify, falls back to strategy as above.

        **Load balancing**: regardless of strategy, ``preferred_node_ids`` are only those
        nodes linked to the **selected** member—not a union across all group members.

        Non-group ``model_name`` is returned unchanged with no preferred nodes.

        Returns:
            Tuple of (actual_model_name, preferred_node_ids, node_priority_overrides)
        """
        await self.ensure_loaded()

        # Not a group - return as-is (backward compatible)
        if model_name not in self._groups:
            return model_name, None, None

        group_data = self._groups[model_name]
        group = group_data["group"]
        members = group_data["members"]

        if not members:
            logger.warning(f"[ModelGroupManager] Group '{model_name}' has no active members")
            return model_name, None, None

        # Detect if vision capability is needed
        needs_vision = False
        if request_data:
            messages = request_data.get("messages", [])
            needs_vision = self._detect_vision_request(messages)

        # Vision: narrow to vision-capable members (then lowest priority among them).
        # Otherwise member choice is entirely driven by group.strategy (priority / round_robin / weighted).
        selected = self._select_by_capability(members, needs_vision)

        if not selected:
            selected = self._select_by_strategy(members, group.strategy, model_name)

        if selected:
            pids = self.preferred_node_ids_for_member(selected)
            overrides = self.node_priority_overrides_for_member(selected)
            logger.info(
                f"[ModelGroupManager] Group '{model_name}' -> '{selected.model_display_name}' "
                f"(strategy={group.strategy}, vision={needs_vision}, "
                f"preferred_node_ids={pids}, node_priority_overrides={overrides})"
            )
            return selected.model_display_name, pids, overrides

        # Fallback: return group name (will be handled by model_mapper)
        return model_name, None, None

    async def resolve_model(
        self, model_name: str, request_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Resolve a model name to an actual model name.

        Backward-compatible wrapper around resolve_model_with_metadata().

        Args:
            model_name: Model name from client request
            request_data: Optional request body (for capability detection)

        Returns:
            Actual model name to use (display_name from member)
        """
        resolved, _, _ = await self.resolve_model_with_metadata(model_name, request_data)
        return resolved

    def get_fallback(
        self, group_name: str, failed_model: str, tried_models: Optional[set] = None
    ) -> Optional[str]:
        """
        Get the next model from a group by priority order after a failure.

        Iterates through all active members in priority order (lowest number first),
        skipping any model already in tried_models.

        Args:
            group_name: Name of the group
            failed_model: The model that failed
            tried_models: Set of model names already tried (including the failed one)

        Returns:
            Next model name or None if all members exhausted
        """
        if group_name not in self._groups:
            return None

        group_data = self._groups[group_name]
        members = group_data["members"]

        # Build the set of models to skip
        skip = set()
        if tried_models:
            skip = set(tried_models)
        skip.add(failed_model)

        # Try members in priority order, skipping already-tried models
        sorted_members = sorted(members, key=lambda m: m.priority)
        for member in sorted_members:
            if member.model_display_name not in skip:
                return member.model_display_name

        return None

    def get_fallback_413(self, group_name: str, failed_model: str) -> Optional[str]:
        """Return the 413 fallback model for a group (any member flagged is_fallback_413)."""
        if group_name not in self._groups:
            return None
        skip = {failed_model}
        sorted_members = sorted(self._groups[group_name]["members"], key=lambda m: m.priority)
        for member in sorted_members:
            if member.model_display_name not in skip and getattr(member, "is_fallback_413", False):
                return member.model_display_name
        return None

    def preferred_node_ids_for_member(self, member: Any) -> Optional[List[int]]:
        """
        Preferred node ids for one group member (LB runs only within this pool).

        Ordered by per-member priority (highest first) when priorities are set;
        otherwise insertion/relationship order. De-duplicated, order preserved.
        """
        pref = getattr(member, "preferred_nodes", None) or []
        ids: List[int] = []
        seen: set = set()
        for node in pref:
            nid = getattr(node, "id", None)
            if nid is None:
                continue
            nid = int(nid)
            if nid not in seen:
                seen.add(nid)
                ids.append(nid)
        # `preferred_nodes` is ordered by priority desc at the relationship level, but
        # fall back to the cached priority map in case the relationship wasn't ordered.
        prio = self._member_node_prio.get(getattr(member, "id", None) or -1)
        if prio and any(prio.get(n, 0) for n in ids):
            ids.sort(key=lambda n: prio.get(n, 0), reverse=True)
        return ids if ids else None

    def node_priority_overrides_for_member(self, member: Any) -> Optional[Dict[int, int]]:
        """
        Per-member node priority override map (node_id -> priority, higher = preferred).

        Returns None when the member has no explicit priorities (all 0) so routing
        falls back to the node's own global priority — preserving legacy behavior.
        """
        prio = self._member_node_prio.get(getattr(member, "id", None) or -1)
        if not prio:
            return None
        active = {nid: p for nid, p in prio.items() if p}
        return active or None

    def get_member_catalog_names(self, group_name: str) -> List[str]:
        """Member ``model_display_name`` values used to match node catalogs during LB."""
        info = self._groups.get(group_name)
        if not info:
            return []
        out: List[str] = []
        for m in info["members"]:
            name = getattr(m, "model_display_name", None)
            if name and name not in out:
                out.append(str(name))
        return out

    def get_group_info(self, group_name: str) -> Optional[Dict[str, Any]]:
        """Get cached group info (group + members)"""
        return self._groups.get(group_name)

    def invalidate_cache(self, group_name: Optional[str] = None):
        """
        Invalidate cache for a specific group or all groups.

        Args:
            group_name: Specific group to invalidate, or None for all
        """
        if group_name:
            self._groups.pop(group_name, None)
            self._round_robin_indices.pop(group_name, None)
        else:
            self._groups.clear()
            self._round_robin_indices.clear()

        self._cache_loaded = False

    async def reload(self):
        """Force reload all groups from database"""
        self.invalidate_cache()
        await self.ensure_loaded()


# Global model mapping manager instance
model_mapper = ModelMappingManager()

# Global model group manager instance
model_group_manager = ModelGroupManager()

# Global Redis manager instance (will be set by main.py)
from app.redis import RedisManager
redis_manager = None
