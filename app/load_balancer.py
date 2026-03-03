"""Load Balancer - Node selection strategies"""

import logging
import random
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.repositories.node_repository import NodeLoadMetricRepository

logger = logging.getLogger(__name__)


class LoadBalancer:
    """
    Load balancing strategies for Ollama nodes.
    
    Strategies:
    - least_loaded: Select node with least active requests
    - round_robin: Cycle through nodes
    - weighted: Weighted random selection
    - priority: Use priority order, fallback on failure
    """
    
    def __init__(self):
        self._round_robin_index: Dict[str, int] = {}  # model_name -> index
    
    async def select_node(
        self,
        nodes: List[Dict[str, Any]],
        strategy: str = "least_loaded",
        session = None
    ) -> Optional[Dict[str, Any]]:
        """
        Select the best node based on strategy.
        
        Args:
            nodes: List of node dicts with node_id, name, base_url, priority, weight, health_status
            strategy: Selection strategy
            session: Database session for load metrics
            
        Returns:
            Selected node dict or None if no nodes available
        """
        if not nodes:
            return None
        
        # Filter healthy nodes
        healthy_nodes = [
            n for n in nodes
            if n.get("health_status") in ("healthy", "unknown")
        ]
        
        if not healthy_nodes:
            logger.warning("No healthy nodes available")
            # Fallback to all nodes
            healthy_nodes = nodes
        
        if len(healthy_nodes) == 1:
            return healthy_nodes[0]
        
        if strategy == "least_loaded":
            return await self._select_least_loaded(healthy_nodes, session)
        elif strategy == "round_robin":
            return self._select_round_robin(healthy_nodes)
        elif strategy == "weighted":
            return self._select_weighted(healthy_nodes)
        elif strategy == "priority":
            return self._select_priority(healthy_nodes)
        else:
            logger.warning(f"Unknown strategy: {strategy}, using least_loaded")
            return await self._select_least_loaded(healthy_nodes, session)
    
    async def _select_least_loaded(
        self,
        nodes: List[Dict[str, Any]],
        session
    ) -> Dict[str, Any]:
        """
        Select node with least active requests.
        Falls back to priority if no load data available.
        """
        if session is None:
            # No session, use priority
            return self._select_priority(nodes)
        
        try:
            load_repo = NodeLoadMetricRepository(session)
            all_loads = await load_repo.get_all_loads()
            
            # Calculate load score for each node
            scored_nodes = []
            for node in nodes:
                node_id = node.get("node_id") or node.get("id")
                load = all_loads.get(node_id, {})
                
                # Score = active_requests (lower is better)
                active_requests = load.get("active_requests", 0)
                priority = node.get("priority", 0)
                weight = node.get("weight", 100)
                
                # Normalize: higher priority = better, higher weight = better
                # Lower active_requests = better
                # Score formula: (priority * weight) - active_requests
                score = (priority * weight / 100) - active_requests
                
                scored_nodes.append({
                    **node,
                    "_score": score,
                    "_active_requests": active_requests
                })
            
            # Sort by score (higher is better)
            scored_nodes.sort(key=lambda x: x["_score"], reverse=True)
            
            selected = scored_nodes[0]
            logger.debug(
                f"Selected node {selected['name']} via least_loaded "
                f"(active_req={selected['_active_requests']}, score={selected['_score']:.2f})"
            )
            
            return selected
            
        except Exception as e:
            logger.error(f"Error getting load metrics: {e}")
            # Fallback to priority
            return self._select_priority(nodes)
    
    def _select_round_robin(self, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Cycle through nodes in order.
        Uses a separate counter per model for fairness.
        """
        # Sort nodes by priority first
        sorted_nodes = sorted(nodes, key=lambda x: x.get("priority", 0), reverse=True)
        
        # Use first node's name as key (could be model_name for per-model RR)
        key = "default"
        
        if key not in self._round_robin_index:
            self._round_robin_index[key] = 0
        
        index = self._round_robin_index[key] % len(sorted_nodes)
        self._round_robin_index[key] += 1
        
        selected = sorted_nodes[index]
        logger.debug(f"Selected node {selected['name']} via round_robin (index={index})")
        
        return selected
    
    def _select_weighted(self, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Weighted random selection.
        Higher weight = higher probability of selection.
        """
        # Calculate total weight
        total_weight = sum(n.get("weight", 100) for n in nodes)
        
        # Random selection based on weight
        r = random.uniform(0, total_weight)
        
        cumulative = 0
        for node in nodes:
            cumulative += node.get("weight", 100)
            if r <= cumulative:
                logger.debug(
                    f"Selected node {node['name']} via weighted "
                    f"(weight={node.get('weight', 100)}, random={r:.2f})"
                )
                return node
        
        # Fallback (shouldn't happen)
        return nodes[0]
    
    def _select_priority(self, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Select highest priority node.
        If equal priority, use weight as tiebreaker.
        """
        # Sort by priority (desc), then by weight (desc)
        sorted_nodes = sorted(
            nodes,
            key=lambda x: (x.get("priority", 0), x.get("weight", 100)),
            reverse=True
        )
        
        selected = sorted_nodes[0]
        logger.debug(
            f"Selected node {selected['name']} via priority "
            f"(priority={selected.get('priority', 0)}, weight={selected.get('weight', 100)})"
        )
        
        return selected
    
    async def increment_request_count(self, node_id: int, session):
        """Increment active request count for a node"""
        if session is None:
            return
        
        try:
            load_repo = NodeLoadMetricRepository(session)
            await load_repo.increment_request_count(node_id)
        except Exception as e:
            logger.error(f"Error incrementing request count: {e}")
    
    async def decrement_request_count(self, node_id: int, session):
        """Decrement active request count for a node"""
        if session is None:
            return
        
        try:
            load_repo = NodeLoadMetricRepository(session)
            await load_repo.decrement_request_count(node_id)
        except Exception as e:
            logger.error(f"Error decrementing request count: {e}")
    
    async def record_response_time(
        self,
        node_id: int,
        response_time_ms: int,
        session
    ):
        """Record response time for a node"""
        if session is None:
            return
        
        try:
            load_repo = NodeLoadMetricRepository(session)
            # For now, just store the latest
            # TODO: Implement rolling average
            await load_repo.record_metric(
                node_id=node_id,
                active_requests=0,  # Will be updated separately
                avg_response_time_ms=response_time_ms
            )
        except Exception as e:
            logger.error(f"Error recording response time: {e}")
    
    async def get_load_status(self, session) -> List[Dict[str, Any]]:
        """
        Get load status for all nodes.
        For frontend display.
        """
        from app.repositories.node_repository import NodeRepository, NodeLoadMetricRepository
        
        try:
            node_repo = NodeRepository(session)
            load_repo = NodeLoadMetricRepository(session)
            
            nodes = await node_repo.list_all()
            all_loads = await load_repo.get_all_loads()
            
            result = []
            for node in nodes:
                node_id = node.id
                load = all_loads.get(node_id, {})
                
                result.append({
                    "id": node.id,
                    "name": node.name,
                    "base_url": node.base_url,
                    "priority": node.priority,
                    "weight": node.weight,
                    "is_active": node.is_active,
                    "health_status": node.health_status,
                    "last_health_check": node.last_health_check.isoformat() if node.last_health_check else None,
                    "load": {
                        "active_requests": load.get("active_requests", 0),
                        "total_requests_today": load.get("total_requests_today", 0),
                        "avg_response_time_ms": load.get("avg_response_time_ms")
                    }
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting load status: {e}")
            return []


class RoutingRuleEngine:
    """
    Evaluates routing rules for model selection.
    
    Rules can override default load balancing:
    - Specific node for a model
    - Fallback nodes
    - Custom strategy per model prefix
    """
    
    def __init__(self, session):
        self.session = session
        self._rules_cache: Dict[str, Dict[str, Any]] = {}
    
    async def get_routing_rule(self, model_name: str) -> Optional[Dict[str, Any]]:
        """
        Get routing rule for a model.
        
        Checks:
        1. Exact match: "glm-5:cloud"
        2. Prefix match: "glm-*" or "glm-5*"
        3. Wildcard match: "*"
        
        Returns:
            Routing rule dict or None
        """
        # TODO: Implement rule lookup from database
        # For now, return None (use default routing)
        return None
    
    def match_pattern(self, pattern: str, model_name: str) -> bool:
        """
        Check if model name matches a pattern.
        
        Patterns:
        - Exact: "glm-5:cloud"
        - Prefix: "glm-*" or "glm-5*"
        - Tag wildcard: "glm-5:*"
        - Full wildcard: "*"
        """
        import fnmatch
        
        if pattern == "*":
            return True
        
        # fnmatch supports * and ? wildcards
        return fnmatch.fnmatch(model_name, pattern)


# Global instances
load_balancer = LoadBalancer()


# Helper function for quick access
async def select_best_node(
    nodes: List[Dict[str, Any]],
    strategy: str = "least_loaded",
    session = None
) -> Optional[Dict[str, Any]]:
    """Quick helper to select best node"""
    return await load_balancer.select_node(nodes, strategy, session)