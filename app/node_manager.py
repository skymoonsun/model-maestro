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
        aws_secret_key: Optional[str] = None,
        aws_region: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        health_check_url: Optional[str] = None,
        auto_cookie_refresh: bool = False,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, str]]]:
        """
        Check if a node is healthy.

        Args:
            base_url: Node base URL
            api_key: Optional API key
            timeout: Request timeout
            node_type: 'ollama', 'vllm', 'antigravity', or 'bedrock'
            headers: Optional custom headers
            oauth_tokens: Google OAuth tokens (for antigravity)
            project_id: Google project ID (for antigravity)
            aws_secret_key: AWS Secret Access Key (for bedrock)
            aws_region: AWS Region (for bedrock)
            aws_session_token: AWS Session Token (for bedrock)
            health_check_url: Optional custom health check endpoint URL

        Returns:
            Tuple of (is_healthy, error_message)
        """
        # Antigravity nodes use Google v1internal health check
        if node_type == 'antigravity':
            if not oauth_tokens:
                return False, "Missing OAuth tokens for antigravity node", None
            from app.google_proxy import health_check_antigravity
            is_healthy, error = await health_check_antigravity(oauth_tokens, timeout=timeout)
            return is_healthy, error, None

        # Bedrock nodes use AWS ListFoundationModels health check
        if node_type == 'bedrock':
            if not api_key or not aws_secret_key or not aws_region:
                return False, "Missing AWS credentials or region for bedrock node", None
            from app.bedrock_proxy import health_check_bedrock
            is_healthy, error = await health_check_bedrock(
                access_key=api_key,
                secret_key=aws_secret_key,
                region=aws_region,
                session_token=aws_session_token,
                timeout=timeout
            )
            return is_healthy, error, None

        try:
            client = await self.get_client()
            request_headers = {}
            if headers:
                request_headers.update(headers)
            if api_key and "Authorization" not in request_headers:
                request_headers["Authorization"] = f"Bearer {api_key}"

            # Use custom health check URL if provided, otherwise fall back to node type default
            if health_check_url:
                health_url = health_check_url
            elif node_type == 'vllm':
                health_url = f"{base_url.rstrip('/')}/v1/models"
            else:
                health_url = f"{base_url.rstrip('/')}/api/tags"

            response = await client.get(
                health_url,
                headers=request_headers,
                timeout=timeout
            )

            if response.status_code == 200:
                return True, None, None

            # WAF challenge detection
            if auto_cookie_refresh and response.status_code in (302, 401, 403, 405, 407):
                from app.waf_cookie_handler import refresh_waf_cookie
                refreshed, updated_headers, refresh_error = await refresh_waf_cookie(
                    base_url=base_url,
                    api_key=api_key,
                    node_type=node_type,
                    existing_headers=headers,
                    health_check_url=health_check_url,
                    timeout=timeout,
                )
                if refreshed and updated_headers:
                    # Retry health check with refreshed cookie
                    request_headers_retry = {}
                    request_headers_retry.update(updated_headers)
                    if api_key and "Authorization" not in request_headers_retry:
                        request_headers_retry["Authorization"] = f"Bearer {api_key}"
                    retry_response = await client.get(
                        health_url,
                        headers=request_headers_retry,
                        timeout=timeout,
                    )
                    if retry_response.status_code == 200:
                        return True, None, updated_headers
                    return False, f"HTTP {retry_response.status_code} after cookie refresh", updated_headers
                return False, f"HTTP {response.status_code} (cookie refresh failed: {refresh_error})", None

            return False, f"HTTP {response.status_code}", None

        except httpx.TimeoutException:
            return False, "Request timeout", None
        except httpx.ConnectError as e:
            return False, f"Connection error: {str(e)}", None
        except Exception as e:
            logger.error(f"Health check error for {base_url}: {e}")
            return False, str(e), None

    async def discover_models_from_node(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        node_type: str = 'ollama',
        headers: Optional[Dict[str, str]] = None,
        oauth_tokens: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
        aws_secret_key: Optional[str] = None,
        aws_region: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        auto_cookie_refresh: bool = False,
    ) -> Tuple[bool, List[Dict[str, Any]], Optional[str], Optional[Dict[str, str]]]:
        """
        Discover models from a node.

        Args:
            base_url: Node base URL
            api_key: Optional API key
            timeout: Request timeout
            node_type: 'ollama', 'vllm', 'antigravity', or 'bedrock'
            headers: Optional custom headers
            oauth_tokens: Google OAuth tokens (for antigravity)
            project_id: Google project ID (for antigravity)
            aws_secret_key: AWS Secret Access Key (for bedrock)
            aws_region: AWS Region (for bedrock)
            aws_session_token: AWS Session Token (for bedrock)

        Returns:
            Tuple of (success, models_list, error_message)
        """
        # Antigravity nodes use Google v1internal model discovery
        if node_type == 'antigravity':
            if not oauth_tokens:
                return False, [], "Missing OAuth tokens for antigravity node", None
            from app.google_proxy import discover_antigravity_models
            success, models, error = await discover_antigravity_models(oauth_tokens, project_id)
            return success, models, error, None

        # Bedrock nodes use AWS ListFoundationModels discovery
        if node_type == 'bedrock':
            if not api_key or not aws_secret_key or not aws_region:
                return False, [], "Missing AWS credentials or region for bedrock node", None
            from app.bedrock_proxy import discover_bedrock_models
            success, models, error = await discover_bedrock_models(
                access_key=api_key,
                secret_key=aws_secret_key,
                region=aws_region,
                session_token=aws_session_token
            )
            return success, models, error, None

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
                # WAF challenge detection
                if auto_cookie_refresh and response.status_code in (302, 401, 403, 405, 407):
                    from app.waf_cookie_handler import refresh_waf_cookie
                    refreshed, updated_headers, refresh_error = await refresh_waf_cookie(
                        base_url=base_url,
                        api_key=api_key,
                        node_type=node_type,
                        existing_headers=headers,
                        timeout=timeout,
                    )
                    if refreshed and updated_headers:
                        request_headers_retry = {}
                        request_headers_retry.update(updated_headers)
                        if api_key:
                            request_headers_retry["Authorization"] = f"Bearer {api_key}"
                        retry_response = await client.get(
                            discovery_url,
                            headers=request_headers_retry,
                            timeout=timeout,
                        )
                        if retry_response.status_code == 200:
                            data = retry_response.json()
                            result_models = []
                            if node_type == 'vllm':
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
                            return True, result_models, None, updated_headers
                        return False, [], f"HTTP {retry_response.status_code} after cookie refresh", updated_headers
                    return False, [], f"HTTP {response.status_code} (cookie refresh failed: {refresh_error})", None

                return False, [], f"HTTP {response.status_code}", None

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

            return True, result_models, None, None

        except httpx.TimeoutException:
            return False, [], "Request timeout", None
        except httpx.ConnectError as e:
            return False, [], f"Connection error: {str(e)}", None
        except Exception as e:
            logger.error(f"Discovery error for {base_url}: {e}")
            return False, [], str(e), None

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

        # Discover models (is_available is preserved by upsert;
        # models removed from the node are NOT marked unavailable —
        # the admin may have intentionally disabled a model.)
        success, models, error, updated_headers = await self.discover_models_from_node(
            node.base_url,
            node.api_key,
            node_type=getattr(node, 'node_type', 'ollama'),
            headers=getattr(node, 'headers', None),
            oauth_tokens=getattr(node, 'oauth_tokens', None),
            project_id=getattr(node, 'project_id', None),
            aws_secret_key=getattr(node, 'aws_secret_key', None),
            aws_region=getattr(node, 'aws_region', None),
            aws_session_token=getattr(node, 'aws_session_token', None),
            auto_cookie_refresh=getattr(node, 'auto_cookie_refresh', False),
        )

        # Persist refreshed WAF cookie if captured
        if updated_headers:
            await node_repo.update(node_id, headers=updated_headers)

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

        # Upsert models (metadata only — is_available is never touched)
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

    async def get_all_active_healthy_nodes(self) -> List[Dict[str, Any]]:
        """Get all active, healthy nodes (fallback for unmapped models)."""
        from app.database import async_session_maker
        from app.repositories.node_repository import NodeRepository

        try:
            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                nodes = await node_repo.list_active()
                return [
                    {
                        "node_id": node.id,
                        "node_name": node.name,
                        "base_url": node.base_url,
                        "api_key": node.api_key,
                        "node_type": getattr(node, 'node_type', 'ollama'),
                        "priority": node.priority,
                        "weight": node.weight,
                        "health_status": node.health_status,
                        "headers": node.headers,
                        "oauth_tokens": node.oauth_tokens,
                        "project_id": node.project_id,
                        "scoped_models": node.scoped_models,
                    }
                    for node in nodes
                    if node.health_status in ("healthy", "unknown")
                ]
        except Exception as e:
            logger.error(f"[NodeManager] Error fetching active nodes: {e}")
            return []

    async def invalidate_cache(self):
        """Invalidate model cache in both memory and Redis"""
        self._model_cache.clear()

        from app.redis import redis_manager, CACHE_KEYS
        if redis_manager:
            try:
                # Delete model nodes cache by pattern
                pattern = CACHE_KEYS["MODEL_NODES"].replace("{model_name}", "*")
                deleted = await redis_manager.delete_pattern(pattern)
                logger.info(f"[CacheInvalidate] Deleted {deleted} model_nodes keys via pattern '{pattern}'")
                # Also delete node loads and active nodes
                await redis_manager.delete(CACHE_KEYS["NODE_LOADS"])
                await redis_manager.delete(CACHE_KEYS["ACTIVE_NODES"])
                logger.info("[CacheInvalidate] NODE_LOADS and ACTIVE_NODES deleted")
            except Exception as e:
                logger.warning(f"[CacheInvalidate] Error: {e}")
    
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
        Fetch models from the database (node_models table).

        Uses DB instead of live HTTP discovery so antigravity/bedrock/vllm/ollama
        models all appear consistently. Relies on sync_node_models() / background
        tasks keeping node_models up to date.

        Returns Ollama-compatible format: {"models": [...], "nodes_queried": int, "nodes_failed": int}
        """
        from app.database import async_session_maker
        from app.repositories.node_repository import NodeModelRepository, NodeRepository

        try:
            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                nodes = await node_repo.list_active()
                healthy_nodes = [n for n in nodes if n.health_status in ("healthy", "unknown")]
                healthy_node_ids = {n.id for n in healthy_nodes}

                model_repo = NodeModelRepository(session)
                db_models = await model_repo.get_all_available_models()

                seen_names: set[str] = set()
                merged_models: List[Dict[str, Any]] = []
                for m in db_models:
                    if m.node_id not in healthy_node_ids:
                        continue
                    name = m.model_name
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)
                    merged_models.append({
                        "name": name,
                        "size": m.model_size,
                        "digest": m.digest,
                        "modified_at": m.modified_at.isoformat() if m.modified_at else None,
                        "details": m.model_capabilities or {},
                        "family": m.model_family,
                    })

                logger.info(
                    f"[ModelAggregation] DB scan returned {len(merged_models)} unique models "
                    f"from {len(healthy_nodes)} healthy node(s)"
                )
                return {
                    "models": merged_models,
                    "nodes_queried": len(healthy_nodes),
                    "nodes_failed": 0,
                }

        except Exception as e:
            logger.error(f"[ModelAggregation] Error reading models from DB: {e}", exc_info=True)
            return {"models": [], "nodes_queried": 0, "nodes_failed": 0}

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