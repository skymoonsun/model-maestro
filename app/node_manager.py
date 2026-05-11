"""Node Manager - Ollama node discovery and health management"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import httpx

from app.repositories.node_repository import (
    NodeRepository,
    NodeModelRepository,
    NodeLoadMetricRepository
)

logger = logging.getLogger(__name__)


class NodeManager:
    """
    Manages Ollama nodes and model discovery.
    
    Responsibilities:
    - Node health checks
    - Model discovery from nodes
    - Model location tracking
    - Cache management
    """
    
    def __init__(self):
        self._model_cache: Dict[str, List[Dict[str, Any]]] = {}  # model_name -> nodes
        self._cache_valid = False
        self._http_client: Optional[httpx.AsyncClient] = None
    
    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with conservative connection limits to avoid starving streaming."""
        if self._http_client is None or self._http_client.is_closed:
            limits = httpx.Limits(
                max_keepalive_connections=20,
                max_connections=40,
                keepalive_expiry=120,
            )
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=limits,
            )
        return self._http_client
    
    async def close(self):
        """Close HTTP client"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
    
    async def health_check_node(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
        node_type: str = 'ollama',
        headers: Optional[Dict[str, str]] = None,
        oauth_tokens: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a node is healthy.

        Args:
            base_url: Node base URL
            api_key: Optional API key
            timeout: Request timeout
            node_type: 'ollama', 'vllm', or 'antigravity'
            headers: Optional custom headers
            oauth_tokens: Google OAuth tokens (for antigravity)
            project_id: Google project ID (for antigravity)

        Returns:
            Tuple of (is_healthy, error_message)
        """
        # Antigravity nodes use Google v1internal health check
        if node_type == 'antigravity':
            if not oauth_tokens:
                return False, "Missing OAuth tokens for antigravity node"
            from app.google_proxy import health_check_antigravity
            return await health_check_antigravity(oauth_tokens, timeout=timeout)

        try:
            client = await self.get_client()
            request_headers = {}
            if headers:
                request_headers.update(headers)
            if api_key:
                request_headers["Authorization"] = f"Bearer {api_key}"

            # Use different health check endpoint based on node type
            if node_type == 'vllm':
                health_url = f"{base_url.rstrip('/')}/v1/models"
            else:
                health_url = f"{base_url.rstrip('/')}/api/tags"

            response = await client.get(
                health_url,
                headers=request_headers,
                timeout=timeout
            )

            if response.status_code == 200:
                return True, None
            else:
                return False, f"HTTP {response.status_code}"

        except httpx.TimeoutException:
            return False, "Request timeout"
        except httpx.ConnectError as e:
            return False, f"Connection error: {str(e)}"
        except Exception as e:
            logger.error(f"Health check error for {base_url}: {e}")
            return False, str(e)

    async def discover_models_from_node(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        node_type: str = 'ollama',
        headers: Optional[Dict[str, str]] = None,
        oauth_tokens: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        Discover models from a node.

        Args:
            base_url: Node base URL
            api_key: Optional API key
            timeout: Request timeout
            node_type: 'ollama', 'vllm', or 'antigravity'
            headers: Optional custom headers
            oauth_tokens: Google OAuth tokens (for antigravity)
            project_id: Google project ID (for antigravity)

        Returns:
            Tuple of (success, models_list, error_message)
        """
        # Antigravity nodes use Google v1internal model discovery
        if node_type == 'antigravity':
            if not oauth_tokens:
                return False, [], "Missing OAuth tokens for antigravity node"
            from app.google_proxy import discover_antigravity_models
            return await discover_antigravity_models(oauth_tokens, project_id)

        try:
            client = await self.get_client()
            request_headers = {}
            if headers:
                request_headers.update(headers)
            if api_key:
                request_headers["Authorization"] = f"Bearer {api_key}"

            # Use different discovery endpoint based on node type
            if node_type == 'vllm':
                discovery_url = f"{base_url.rstrip('/')}/v1/models"
            else:
                discovery_url = f"{base_url.rstrip('/')}/api/tags"

            response = await client.get(
                discovery_url,
                headers=request_headers,
                timeout=timeout
            )

            if response.status_code != 200:
                return False, [], f"HTTP {response.status_code}"

            data = response.json()
            result_models = []

            if node_type == 'vllm':
                # vLLM returns {"object": "list", "data": [{"id": "model-name", ...}]}
                models = data.get("data", [])
                for model in models:
                    result_models.append({
                        "name": model.get("id"),
                        "size": None,
                        "digest": None,
                        "modified_at": None,
                        "details": {"max_model_len": model.get("max_model_len")},
                        "family": None
                    })
            else:
                # Ollama returns {"models": [{"name": "...", ...}]}
                models = data.get("models", [])
                for model in models:
                    result_models.append({
                        "name": model.get("name"),
                        "size": model.get("size"),
                        "digest": model.get("digest"),
                        "modified_at": model.get("modified_at"),
                        "details": model.get("details", {}),
                        "family": model.get("details", {}).get("family") if isinstance(model.get("details"), dict) else None
                    })

            return True, result_models, None

        except httpx.TimeoutException:
            return False, [], "Request timeout"
        except httpx.ConnectError as e:
            return False, [], f"Connection error: {str(e)}"
        except Exception as e:
            logger.error(f"Discovery error for {base_url}: {e}")
            return False, [], str(e)
        except Exception as e:
            logger.error(f"Model discovery error for {base_url}: {e}")
            return False, [], str(e)
    
    async def sync_node_models(
        self,
        node_id: int,
        session
    ) -> Dict[str, Any]:
        """
        Sync models from a specific node to database.
        
        Returns:
            Dict with sync statistics
        """
        node_repo = NodeRepository(session)
        model_repo = NodeModelRepository(session)
        
        # Get node info
        node = await node_repo.get_by_id(node_id)
        if not node:
            return {"success": False, "error": "Node not found"}
        
        # Mark all existing models as unavailable (will be re-activated if still present)
        await model_repo.mark_all_unavailable_for_node(node_id)
        
        # Discover models
        success, models, error = await self.discover_models_from_node(
            node.base_url,
            node.api_key,
            node_type=getattr(node, 'node_type', 'ollama'),
            headers=getattr(node, 'headers', None),
            oauth_tokens=getattr(node, 'oauth_tokens', None),
            project_id=getattr(node, 'project_id', None)
        )
        
        if not success:
            # Update node health status
            await node_repo.update_health_status(node_id, "unhealthy", error)
            return {
                "success": False,
                "node_id": node_id,
                "node_name": node.name,
                "error": error
            }
        
        # Update node health status
        await node_repo.update_health_status(node_id, "healthy")
        
        # Upsert models
        synced_models = []
        for model_data in models:
            model = await model_repo.upsert(
                node_id=node_id,
                model_name=model_data["name"],
                model_size=model_data.get("size"),
                model_family=model_data.get("family"),
                model_capabilities=model_data.get("details"),
                digest=model_data.get("digest"),
                modified_at=model_data.get("modified_at")
            )
            synced_models.append(model_data["name"])
        
        # Invalidate cache (Redis + memory)
        await self.invalidate_cache()

        return {
            "success": True,
            "node_id": node_id,
            "node_name": node.name,
            "synced_count": len(synced_models),
            "models": synced_models[:10] if synced_models else [],  # First 10 for preview
            "total_models": len(synced_models)
        }
    
    async def _sync_node_with_own_session(self, node_id: int) -> Dict[str, Any]:
        """Sync a single node using its own DB session (safe for concurrent use)."""
        from app.database import async_session_maker

        async with async_session_maker() as session:
            result = await self.sync_node_models(node_id, session)
            await session.commit()
            return result

    async def sync_all_nodes(self, session) -> Dict[str, Any]:
        """
        Sync models from all active nodes concurrently.
        Each sync gets its own session to avoid SQLAlchemy concurrency issues.

        Returns:
            Dict with overall sync statistics
        """
        import asyncio as _asyncio

        node_repo = NodeRepository(session)
        nodes = await node_repo.list_active()

        # Sync all nodes concurrently, each with its own session
        results = await _asyncio.gather(
            *[self._sync_node_with_own_session(node.id) for node in nodes],
            return_exceptions=True
        )

        total_synced = 0
        failed_nodes = []

        for result in results:
            if isinstance(result, Exception):
                failed_nodes.append("unknown")
                logger.error(f"Node sync error: {result}")
                continue
            if result["success"]:
                total_synced += result["total_models"]
            else:
                failed_nodes.append(result.get("node_name", "unknown"))

        # Invalidate cache (Redis + memory)
        await self.invalidate_cache()

        return {
            "total_nodes": len(nodes),
            "successful_nodes": len(nodes) - len(failed_nodes),
            "failed_nodes": failed_nodes,
            "total_models": total_synced,
            "results": results
        }
    
    async def get_nodes_for_model(
        self,
        model_name: str,
        session=None
    ) -> List[Dict[str, Any]]:
        """
        Get all nodes that have a specific model.
        Uses Redis as primary cache, falls back to DB on miss.

        Args:
            model_name: The model name to look up
            session: Optional DB session (only used on cache miss)

        Returns:
            List of node dicts with load info
        """
        from app.redis import redis_manager, CACHE_KEYS, CACHE_TTL
        cache_key = CACHE_KEYS["MODEL_NODES"].format(model_name=model_name)

        # Try Redis first
        if redis_manager:
            try:
                cached = await redis_manager.get(cache_key)
                if cached:
                    import json
                    return json.loads(cached)
            except Exception:
                pass

        # Fallback to DB — open our own session if caller didn't provide one
        if session is None:
            from app.database import async_session_maker
            async with async_session_maker() as db_session:
                return await self.get_nodes_for_model(model_name, db_session)

        model_repo = NodeModelRepository(session)
        nodes = await model_repo.get_nodes_for_model(model_name)

        # Store in Redis for next time
        if redis_manager:
            try:
                import json
                await redis_manager.set(cache_key, json.dumps(nodes), expire=CACHE_TTL["MODEL_NODES"])
            except Exception:
                pass

        return nodes
    
    async def get_model_location(
        self,
        display_name: str,
        real_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Find the best node for a model.

        Args:
            display_name: User-facing model name
            real_name: Actual model name in Ollama

        Returns:
            Node dict or None if not found
        """
        # Try real_name first
        nodes = await self.get_nodes_for_model(real_name)

        if not nodes:
            # Try display_name as fallback
            nodes = await self.get_nodes_for_model(display_name)

        if not nodes:
            return None

        # Return first available node (will be load-balanced later)
        return nodes[0]
    
    async def get_model_distribution(self, session) -> List[Dict[str, Any]]:
        """
        Get distribution of all models across nodes.
        For frontend display.
        """
        model_repo = NodeModelRepository(session)
        return await model_repo.get_model_distribution()
    
    async def invalidate_cache(self):
        """Invalidate model cache in both memory and Redis"""
        self._model_cache.clear()

        from app.redis import redis_manager, CACHE_KEYS
        if redis_manager:
            try:
                # Delete model nodes cache (pattern scan)
                keys = await redis_manager.keys(CACHE_KEYS["MODEL_NODES"].replace("{model_name}", "*"))
                if keys:
                    await redis_manager.delete(*keys)
                # Also delete node loads and active nodes
                await redis_manager.delete(CACHE_KEYS["NODE_LOADS"])
                await redis_manager.delete(CACHE_KEYS["ACTIVE_NODES"])
            except Exception:
                pass
    
    async def pull_model_to_node(
        self,
        node_id: int,
        model_name: str,
        stream: bool = True,
        session = None
    ):
        """
        Pull a model to a specific node.
        
        Streams progress updates.
        """
        node_repo = NodeRepository(session)
        node = await node_repo.get_by_id(node_id)
        
        if not node:
            raise ValueError(f"Node {node_id} not found")
        
        client = await self.get_client()
        request_headers = {}
        if getattr(node, 'headers', None):
            request_headers.update(node.headers)
        if node.api_key:
            request_headers["Authorization"] = f"Bearer {node.api_key}"

        url = f"{node.base_url.rstrip('/')}/api/pull"

        async with client.stream(
            "POST",
            url,
            json={"name": model_name, "stream": stream},
            headers=request_headers,
            timeout=600.0  # 10 minutes
        ) as response:
            if response.status_code != 200:
                error = await response.aread()
                raise Exception(f"Failed to pull model: {error.decode()}")
            
            buffer = b""
            async for chunk in response.aiter_bytes():
                buffer += chunk

                # Guard against unbounded buffer growth
                if len(buffer) > 10 * 1024 * 1024:
                    logger.warning(f"[PULL] Buffer exceeded 10MB, discarding")
                    buffer = b""
                    continue

                # Yield complete lines
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if line:
                        try:
                            data = line.decode('utf-8')
                            yield data
                        except UnicodeDecodeError:
                            pass
            
            # Yield remaining buffer
            if buffer:
                try:
                    yield buffer.decode('utf-8')
                except UnicodeDecodeError:
                    pass
        
        # Sync models after pull
        await self.sync_node_models(node_id, session)
    
    async def pull_model_to_all_nodes(
        self,
        model_name: str,
        stream: bool = True,
        session = None
    ):
        """
        Pull a model to all active nodes.
        
        Yields progress from each node.
        """
        node_repo = NodeRepository(session)
        nodes = await node_repo.list_active()
        
        for node in nodes:
            yield f'{{"status": "pulling_to_node", "node": "{node.name}"}}\n'
            
            try:
                async for chunk in self.pull_model_to_node(
                    node.id,
                    model_name,
                    stream,
                    session
                ):
                    yield chunk
            except Exception as e:
                yield f'{{"error": "Failed to pull to {node.name}: {str(e)}"}}\n'
    
    async def get_all_models_from_nodes(self) -> Dict[str, Any]:
        """
        Fetch models from all healthy, active nodes concurrently and merge results.

        Deduplicates by model name, keeping the first occurrence's details.
        Returns Ollama-compatible format: {"models": [...], "nodes_queried": int, "nodes_failed": int}

        Falls back to an empty model list if no nodes are available.
        """
        from app.database import async_session_maker

        nodes_to_query = []

        try:
            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                active_nodes = await node_repo.list_active()

                for node in active_nodes:
                    if node.health_status in ("healthy", "unknown"):
                        nodes_to_query.append({
                            "id": node.id,
                            "name": node.name,
                            "base_url": node.base_url,
                            "api_key": node.api_key,
                            "node_type": getattr(node, 'node_type', 'ollama'),
                        })
        except Exception as e:
            logger.error(f"[ModelAggregation] Error fetching nodes from DB: {e}")
            return {"models": [], "nodes_queried": 0, "nodes_failed": 0}

        if not nodes_to_query:
            logger.warning("[ModelAggregation] No healthy nodes found for model listing")
            return {"models": [], "nodes_queried": 0, "nodes_failed": 0}

        async def _fetch_node_models(node_info: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], bool]:
            """Fetch models from a single node. Returns (node_name, models, success)."""
            try:
                client = await self.get_client()
                headers = {}
                if node_info["api_key"]:
                    headers["Authorization"] = f"Bearer {node_info['api_key']}"

                node_type = node_info.get("node_type", "ollama")
                if node_type == "vllm":
                    discovery_url = f"{node_info['base_url'].rstrip('/')}/v1/models"
                else:
                    discovery_url = f"{node_info['base_url'].rstrip('/')}/api/tags"

                response = await client.get(
                    discovery_url,
                    headers=headers,
                    timeout=15.0,
                )

                if response.status_code != 200:
                    logger.warning(
                        f"[ModelAggregation] Node {node_info['name']} returned {response.status_code}"
                    )
                    return node_info["name"], [], False

                data = response.json()

                if node_type == "vllm":
                    # vLLM returns {"object": "list", "data": [{"id": "...", ...}]}
                    # Convert to Ollama-compatible format {"models": [{"name": "...", ...}]}
                    raw_models = data.get("data", [])
                    models = []
                    for m in raw_models:
                        models.append({
                            "name": m.get("id"),
                            "size": None,
                            "digest": None,
                            "modified_at": None,
                            "details": None,
                        })
                else:
                    models = data.get("models", [])

                return node_info["name"], models, True

            except Exception as e:
                logger.warning(f"[ModelAggregation] Error fetching from node {node_info['name']}: {e}")
                return node_info["name"], [], False

        # Fetch from all nodes concurrently
        results = await asyncio.gather(
            *[_fetch_node_models(n) for n in nodes_to_query],
            return_exceptions=False,
        )

        # Merge models, deduplicating by name
        seen_names = set()
        merged_models = []
        nodes_succeeded = 0
        nodes_failed = 0

        for node_name, models, success in results:
            if success:
                nodes_succeeded += 1
            else:
                nodes_failed += 1

            for model in models:
                model_name = model.get("name")
                if model_name and model_name not in seen_names:
                    seen_names.add(model_name)
                    merged_models.append(model)

        logger.info(
            f"[ModelAggregation] Fetched {len(merged_models)} unique models "
            f"from {nodes_succeeded}/{len(nodes_to_query)} nodes "
            f"({nodes_failed} failed)"
        )

        return {
            "models": merged_models,
            "nodes_queried": nodes_succeeded,
            "nodes_failed": nodes_failed,
        }

    async def get_node_models(self, node_id: int, session) -> List[Dict[str, Any]]:
        """Get all models for a specific node"""
        model_repo = NodeModelRepository(session)
        models = await model_repo.get_models_for_node(node_id)
        
        return [
            {
                "model_name": m.model_name,
                "model_size": m.model_size,
                "model_family": m.model_family,
                "model_capabilities": m.model_capabilities,
                "digest": m.digest,
                "is_available": m.is_available,
                "last_seen": m.last_seen.isoformat() if m.last_seen else None
            }
            for m in models
        ]


# Global instance
node_manager = NodeManager()