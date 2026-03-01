"""Repository for OllamaNode operations"""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models_db import OllamaNode, NodeModel, NodeLoadMetric


class NodeRepository:
    """Repository for OllamaNode CRUD operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(
        self,
        name: str,
        base_url: str,
        api_key: Optional[str] = None,
        priority: int = 0,
        weight: int = 100,
        is_active: bool = True,
        health_check_url: Optional[str] = None
    ) -> OllamaNode:
        """Create a new Ollama node"""
        node = OllamaNode(
            name=name,
            base_url=base_url,
            api_key=api_key,
            priority=priority,
            weight=weight,
            is_active=is_active,
            health_check_url=health_check_url,
            health_status='unknown'
        )
        self.session.add(node)
        await self.session.commit()
        await self.session.refresh(node)
        return node
    
    async def get_by_id(self, node_id: int) -> Optional[OllamaNode]:
        """Get node by ID"""
        result = await self.session.execute(
            select(OllamaNode).where(OllamaNode.id == node_id)
        )
        return result.scalar_one_or_none()
    
    async def get_by_name(self, name: str) -> Optional[OllamaNode]:
        """Get node by name"""
        result = await self.session.execute(
            select(OllamaNode).where(OllamaNode.name == name)
        )
        return result.scalar_one_or_none()
    
    async def list_all(self, active_only: bool = False) -> List[OllamaNode]:
        """List all nodes"""
        query = select(OllamaNode).order_by(OllamaNode.priority.desc())
        if active_only:
            query = query.where(OllamaNode.is_active == True)
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def list_active(self) -> List[OllamaNode]:
        """List all active nodes"""
        return await self.list_all(active_only=True)
    
    async def update(
        self,
        node_id: int,
        **kwargs
    ) -> Optional[OllamaNode]:
        """Update node fields"""
        node = await self.get_by_id(node_id)
        if not node:
            return None
        
        for key, value in kwargs.items():
            if hasattr(node, key):
                setattr(node, key, value)
        
        await self.session.commit()
        await self.session.refresh(node)
        return node
    
    async def delete(self, node_id: int) -> bool:
        """Delete a node"""
        node = await self.get_by_id(node_id)
        if not node:
            return False
        
        await self.session.delete(node)
        await self.session.commit()
        return True
    
    async def update_health_status(
        self,
        node_id: int,
        status: str,
        error_message: Optional[str] = None
    ) -> Optional[OllamaNode]:
        """Update node health status"""
        from datetime import datetime, timezone
        update_kwargs = {"health_status": status}
        if status != "unknown":
            update_kwargs["last_health_check"] = datetime.now(timezone.utc)
        return await self.update(node_id, **update_kwargs)
    
    async def get_nodes_with_models(self) -> List[Dict[str, Any]]:
        """Get all nodes with their models"""
        result = await self.session.execute(
            select(OllamaNode)
            .options(selectinload(OllamaNode.node_models))
            .order_by(OllamaNode.priority.desc())
        )
        nodes = result.scalars().all()
        
        return [
            {
                "id": node.id,
                "name": node.name,
                "base_url": node.base_url,
                "priority": node.priority,
                "weight": node.weight,
                "is_active": node.is_active,
                "health_status": node.health_status,
                "last_health_check": node.last_health_check.isoformat() if node.last_health_check else None,
                "model_count": len(node.node_models),
                "models": [
                    {
                        "model_name": m.model_name,
                        "model_size": m.model_size,
                        "model_family": m.model_family,
                        "is_available": m.is_available
                    }
                    for m in node.node_models
                ]
            }
            for node in nodes
        ]


class NodeModelRepository:
    """Repository for NodeModel operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def upsert(
        self,
        node_id: int,
        model_name: str,
        model_size: Optional[int] = None,
        model_family: Optional[str] = None,
        model_capabilities: Optional[Dict[str, Any]] = None,
        digest: Optional[str] = None,
        modified_at: Optional[str] = None
    ) -> NodeModel:
        """Create or update a node model"""
        # Try to find existing
        result = await self.session.execute(
            select(NodeModel).where(
                and_(
                    NodeModel.node_id == node_id,
                    NodeModel.model_name == model_name
                )
            )
        )
        model = result.scalar_one_or_none()
        
        # Parse modified_at from ISO string if needed (Ollama returns string)
        modified_dt: Optional[datetime] = None
        if modified_at:
            if isinstance(modified_at, str):
                try:
                    s = modified_at.replace('Z', '+00:00')
                    # Python fromisoformat supports max 6 fractional digits
                    if '.' in s and '+' in s:
                        pos = s.index('.') + 1
                        end = s.index('+')
                        frac = s[pos:end]
                        if len(frac) > 6:
                            s = s[:pos + 6] + s[end:]
                    modified_dt = datetime.fromisoformat(s)
                except (ValueError, TypeError):
                    pass
            elif isinstance(modified_at, datetime):
                modified_dt = modified_at

        if model:
            # Update
            model.model_size = model_size
            model.model_family = model_family
            model.model_capabilities = model_capabilities
            model.digest = digest
            model.modified_at = modified_dt
            model.last_seen = datetime.now(timezone.utc)
            model.is_available = True
        else:
            # Create
            model = NodeModel(
                node_id=node_id,
                model_name=model_name,
                model_size=model_size,
                model_family=model_family,
                model_capabilities=model_capabilities,
                digest=digest,
                modified_at=modified_dt,
                is_available=True
            )
            self.session.add(model)
        
        await self.session.commit()
        await self.session.refresh(model)
        return model
    
    async def get_models_for_node(self, node_id: int) -> List[NodeModel]:
        """Get all models for a specific node"""
        result = await self.session.execute(
            select(NodeModel).where(NodeModel.node_id == node_id)
        )
        return list(result.scalars().all())
    
    async def get_nodes_for_model(self, model_name: str) -> List[Dict[str, Any]]:
        """Get all nodes that have a specific model"""
        from app.models_db import OllamaNode
        
        result = await self.session.execute(
            select(NodeModel, OllamaNode)
            .join(OllamaNode, NodeModel.node_id == OllamaNode.id)
            .where(
                and_(
                    NodeModel.model_name == model_name,
                    NodeModel.is_available == True,
                    OllamaNode.is_active == True,
                    OllamaNode.health_status == 'healthy'
                )
            )
            .order_by(OllamaNode.priority.desc())
        )
        
        return [
            {
                "node_id": node.id,
                "node_name": node.name,
                "base_url": node.base_url,
                "priority": node.priority,
                "weight": node.weight,
                "health_status": node.health_status
            }
            for model, node in result.all()
        ]
    
    async def mark_unavailable(self, node_id: int, model_name: str) -> bool:
        """Mark a model as unavailable on a node"""
        result = await self.session.execute(
            select(NodeModel).where(
                and_(
                    NodeModel.node_id == node_id,
                    NodeModel.model_name == model_name
                )
            )
        )
        model = result.scalar_one_or_none()
        
        if model:
            model.is_available = False
            await self.session.commit()
            return True
        return False
    
    async def mark_all_unavailable_for_node(self, node_id: int):
        """Mark all models on a node as unavailable (before fresh sync)"""
        result = await self.session.execute(
            select(NodeModel).where(NodeModel.node_id == node_id)
        )
        models = result.scalars().all()
        
        for model in models:
            model.is_available = False
        
        await self.session.commit()
    
    async def delete_not_seen_since(self, node_id: int, hours: int = 24):
        """Delete models not seen in X hours"""
        from datetime import datetime, timedelta
        from sqlalchemy import delete
        
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        await self.session.execute(
            delete(NodeModel).where(
                and_(
                    NodeModel.node_id == node_id,
                    NodeModel.last_seen < cutoff
                )
            )
        )
        await self.session.commit()
    
    async def get_model_distribution(self) -> List[Dict[str, Any]]:
        """Get distribution of models across nodes - for frontend"""
        from sqlalchemy import func
        from app.models_db import OllamaNode
        
        # Get all unique models with their nodes
        result = await self.session.execute(
            select(
                NodeModel.model_name,
                func.count(NodeModel.node_id).label('node_count'),
                func.array_agg(OllamaNode.name).label('nodes')
            )
            .join(OllamaNode, NodeModel.node_id == OllamaNode.id)
            .where(
                and_(
                    NodeModel.is_available == True,
                    OllamaNode.is_active == True
                )
            )
            .group_by(NodeModel.model_name)
            .order_by(NodeModel.model_name)
        )
        
        return [
            {
                "model_name": row.model_name,
                "node_count": row.node_count,
                "nodes": row.nodes
            }
            for row in result.all()
        ]


class NodeLoadMetricRepository:
    """Repository for node load metrics"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def record_metric(
        self,
        node_id: int,
        active_requests: int,
        avg_response_time_ms: Optional[int] = None
    ) -> NodeLoadMetric:
        """Record a load metric for a node"""
        metric = NodeLoadMetric(
            node_id=node_id,
            active_requests=active_requests,
            avg_response_time_ms=avg_response_time_ms
        )
        self.session.add(metric)
        await self.session.commit()
        await self.session.refresh(metric)
        return metric
    
    async def increment_request_count(self, node_id: int):
        """Increment active request count for a node"""
        # Get current metric
        result = await self.session.execute(
            select(NodeLoadMetric)
            .where(NodeLoadMetric.node_id == node_id)
            .order_by(NodeLoadMetric.recorded_at.desc())
            .limit(1)
        )
        metric = result.scalar_one_or_none()
        
        if metric:
            metric.active_requests += 1
            metric.total_requests_today += 1
            metric.last_5_min_requests += 1
        else:
            # Create new metric
            metric = NodeLoadMetric(
                node_id=node_id,
                active_requests=1,
                total_requests_today=1,
                last_5_min_requests=1
            )
            self.session.add(metric)
        
        await self.session.commit()
    
    async def decrement_request_count(self, node_id: int):
        """Decrement active request count for a node"""
        result = await self.session.execute(
            select(NodeLoadMetric)
            .where(NodeLoadMetric.node_id == node_id)
            .order_by(NodeLoadMetric.recorded_at.desc())
            .limit(1)
        )
        metric = result.scalar_one_or_none()
        
        if metric and metric.active_requests > 0:
            metric.active_requests -= 1
            await self.session.commit()
    
    async def get_current_load(self, node_id: int) -> Optional[Dict[str, Any]]:
        """Get current load metric for a node"""
        result = await self.session.execute(
            select(NodeLoadMetric)
            .where(NodeLoadMetric.node_id == node_id)
            .order_by(NodeLoadMetric.recorded_at.desc())
            .limit(1)
        )
        metric = result.scalar_one_or_none()
        
        if metric:
            return {
                "node_id": metric.node_id,
                "active_requests": metric.active_requests,
                "total_requests_today": metric.total_requests_today,
                "avg_response_time_ms": metric.avg_response_time_ms,
                "recorded_at": metric.recorded_at.isoformat() if metric.recorded_at else None
            }
        return None
    
    async def get_all_loads(self) -> Dict[int, Dict[str, Any]]:
        """Get current load for all nodes"""
        from sqlalchemy import func
        
        # Get the latest metric for each node
        result = await self.session.execute(
            select(NodeLoadMetric)
            .distinct(NodeLoadMetric.node_id)
            .order_by(NodeLoadMetric.node_id, NodeLoadMetric.recorded_at.desc())
        )
        metrics = result.scalars().all()
        
        return {
            m.node_id: {
                "active_requests": m.active_requests,
                "total_requests_today": m.total_requests_today,
                "avg_response_time_ms": m.avg_response_time_ms
            }
            for m in metrics
        }
    
    async def reset_daily_counters(self):
        """Reset daily counters (run at midnight)"""
        from sqlalchemy import update
        
        await self.session.execute(
            update(NodeLoadMetric).values(total_requests_today=0)
        )
        await self.session.commit()
    
    async def reset_5min_counters(self):
        """Reset 5-minute counters (run every 5 minutes)"""
        from sqlalchemy import update
        
        await self.session.execute(
            update(NodeLoadMetric).values(last_5_min_requests=0)
        )
        await self.session.commit()