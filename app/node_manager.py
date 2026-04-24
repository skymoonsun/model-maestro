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
        """Get or create HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client
    
    async def close(self):
        """Close HTTP client"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
    
    async def health_check_node(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 5.0
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a node is healthy.
        
        Returns:
            Tuple of (is_healthy, error_message)
        """
        try:
            client = await self.get_client()
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            
            response = await client.get(
                f"{base_url.rstrip('/')}/api/tags",
                headers=headers,
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
        timeout: float = 30.0
    ) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        Discover models from a node by calling /api/tags.
        
        Returns:
            Tuple of (success, models_list, error_message)
        """
        try:
            client = await self.get_client()
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            
            response = await client.get(
                f"{base_url.rstrip('/')}/api/tags",
                headers=headers,
                timeout=timeout
            )
            
            if response.status_code != 200:
                return False, [], f"HTTP {response.status_code}"
            
            data = response.json()
            models = data.get("models", [])
            
            result_models = []
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
            node.api_key
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
        
        # Invalidate cache
        self._cache_valid = False
        
        return {
            "success": True,
            "node_id": node_id,
            "node_name": node.name,
            "synced_count": len(synced_models),
            "models": synced_models[:10] if synced_models else [],  # First 10 for preview
            "total_models": len(synced_models)
        }
    
    async def sync_all_nodes(self, session) -> Dict[str, Any]:
        """
        Sync models from all active nodes.
        
        Returns:
            Dict with overall sync statistics
        """
        node_repo = NodeRepository(session)
        nodes = await node_repo.list_active()
        
        results = []
        total_synced = 0
        failed_nodes = []
        
        for node in nodes:
            result = await self.sync_node_models(node.id, session)
            results.append(result)
            
            if result["success"]:
                total_synced += result["total_models"]
            else:
                failed_nodes.append(node.name)
        
        # Invalidate cache
        self._cache_valid = False
        
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
        session
    ) -> List[Dict[str, Any]]:
        """
        Get all nodes that have a specific model.
        
        Returns:
            List of node dicts with load info
        """
        # Use cache if valid
        if self._cache_valid and model_name in self._model_cache:
            return self._model_cache[model_name]
        
        model_repo = NodeModelRepository(session)
        nodes = await model_repo.get_nodes_for_model(model_name)
        
        # Update cache
        self._model_cache[model_name] = nodes
        
        return nodes
    
    async def get_model_location(
        self,
        display_name: str,
        real_name: str,
        session
    ) -> Optional[Dict[str, Any]]:
        """
        Find the best node for a model.
        
        Args:
            display_name: User-facing model name
            real_name: Actual model name in Ollama
            
        Returns:
            Node dict or None if not found
        """
        from app.config import model_mapper
        
        # Try real_name first
        nodes = await self.get_nodes_for_model(real_name, session)
        
        if not nodes:
            # Try display_name as fallback
            nodes = await self.get_nodes_for_model(display_name, session)
        
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
    
    def invalidate_cache(self):
        """Invalidate model cache"""
        self._cache_valid = False
        self._model_cache.clear()
    
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
        headers = {}
        if node.api_key:
            headers["Authorization"] = f"Bearer {node.api_key}"
        
        url = f"{node.base_url.rstrip('/')}/api/pull"
        
        async with client.stream(
            "POST",
            url,
            json={"name": model_name, "stream": stream},
            headers=headers,
            timeout=600.0  # 10 minutes
        ) as response:
            if response.status_code != 200:
                error = await response.aread()
                raise Exception(f"Failed to pull model: {error.decode()}")
            
            buffer = b""
            async for chunk in response.aiter_bytes():
                buffer += chunk
                
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

                response = await client.get(
                    f"{node_info['base_url'].rstrip('/')}/api/tags",
                    headers=headers,
                    timeout=15.0,
                )

                if response.status_code != 200:
                    logger.warning(
                        f"[ModelAggregation] Node {node_info['name']} returned {response.status_code}"
                    )
                    return node_info["name"], [], False

                data = response.json()
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