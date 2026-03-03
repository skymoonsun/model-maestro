"""Admin API endpoints for Ollama node management"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
import logging

from app.database import async_session_maker
from app.models import (
    OllamaNodeCreate,
    OllamaNodeUpdate,
    OllamaNodeResponse,
    OllamaNodeDetailResponse,
    ModelDistributionRow,
    NodeSyncResponse,
    PullModelRequest,
    PullModelResponse
)
from app.models_db import OllamaNode, AuditLog
from app.repositories.node_repository import (
    NodeRepository,
    NodeModelRepository,
    NodeLoadMetricRepository
)
from app.node_manager import node_manager
from app.load_balancer import load_balancer
from app.auth import verify_admin
from app.repositories.audit_log_repository import AuditLogRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/nodes", tags=["Admin - Nodes"])


# ==================== NODE MANAGEMENT ====================

@router.post("", response_model=OllamaNodeResponse)
async def create_node(
    request: OllamaNodeCreate,
    admin: str = Depends(verify_admin)
):
    """
    Create a new Ollama node.
    
    - **name**: Unique name for the node (e.g., "main-server", "backup-1")
    - **base_url**: Ollama server URL (e.g., "http://194.87.188.8:11434")
    - **api_key**: Optional API key for authentication
    - **priority**: Higher = preferred (default: 0)
    - **weight**: Load balancing weight (default: 100)
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        
        # Check if name already exists
        existing = await repo.get_by_name(request.name)
        if existing:
            raise HTTPException(status_code=400, detail=f"Node '{request.name}' already exists")
        
        # Create node
        node = await repo.create(
            name=request.name,
            base_url=request.base_url,
            api_key=request.api_key,
            priority=request.priority or 0,
            weight=request.weight or 100,
            is_active=request.is_active if request.is_active is not None else True,
            health_check_url=request.health_check_url
        )
        
        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="create_node",
            entity_type="node",
            entity_id=str(node.id),
            details={"name": node.name, "base_url": node.base_url},
            admin_ip=None
        )
        
        return OllamaNodeResponse(
            id=node.id,
            name=node.name,
            base_url=node.base_url,
            api_key_set=bool(node.api_key),
            priority=node.priority,
            weight=node.weight,
            is_active=node.is_active,
            health_status=node.health_status,
            last_health_check=node.last_health_check.isoformat() if node.last_health_check else None,
            created_at=node.created_at.isoformat() if node.created_at else None,
            updated_at=node.updated_at.isoformat() if node.updated_at else None
        )


@router.get("", response_model=List[OllamaNodeDetailResponse])
async def list_nodes(
    active_only: bool = Query(False, description="Only return active nodes"),
    admin: str = Depends(verify_admin)
):
    """
    List all Ollama nodes with their models.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        nodes = await repo.get_nodes_with_models()
        
        if active_only:
            nodes = [n for n in nodes if n["is_active"]]
        
        return nodes


@router.get("/{node_id}", response_model=OllamaNodeDetailResponse)
async def get_node(
    node_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Get detailed information about a specific node.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        model_repo = NodeModelRepository(session)
        
        node = await repo.get_by_id(node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        
        models = await model_repo.get_models_for_node(node_id)
        
        return OllamaNodeDetailResponse(
            id=node.id,
            name=node.name,
            base_url=node.base_url,
            api_key_set=bool(node.api_key),
            priority=node.priority,
            weight=node.weight,
            is_active=node.is_active,
            health_status=node.health_status,
            last_health_check=node.last_health_check.isoformat() if node.last_health_check else None,
            created_at=node.created_at.isoformat() if node.created_at else None,
            updated_at=node.updated_at.isoformat() if node.updated_at else None,
            model_count=len(models),
            models=[
                {
                    "model_name": m.model_name,
                    "model_size": m.model_size,
                    "model_family": m.model_family,
                    "is_available": m.is_available
                }
                for m in models
            ]
        )


@router.patch("/{node_id}", response_model=OllamaNodeResponse)
async def update_node(
    node_id: int,
    request: OllamaNodeUpdate,
    admin: str = Depends(verify_admin)
):
    """
    Update node configuration.
    
    All fields are optional - only provided fields will be updated.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        
        # Build update dict
        update_data = {}
        if request.name is not None:
            # Check if name already exists
            existing = await repo.get_by_name(request.name)
            if existing and existing.id != node_id:
                raise HTTPException(status_code=400, detail=f"Node '{request.name}' already exists")
            update_data["name"] = request.name
        
        if request.base_url is not None:
            update_data["base_url"] = request.base_url
        
        if request.api_key is not None:
            update_data["api_key"] = request.api_key
        
        if request.priority is not None:
            update_data["priority"] = request.priority
        
        if request.weight is not None:
            update_data["weight"] = request.weight
        
        if request.is_active is not None:
            update_data["is_active"] = request.is_active
        
        if request.health_check_url is not None:
            update_data["health_check_url"] = request.health_check_url
        
        node = await repo.update(node_id, **update_data)
        
        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        
        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="update_node",
            entity_type="node",
            entity_id=str(node.id),
            details=update_data,
            admin_ip=None
        )
        
        return OllamaNodeResponse(
            id=node.id,
            name=node.name,
            base_url=node.base_url,
            api_key_set=bool(node.api_key),
            priority=node.priority,
            weight=node.weight,
            is_active=node.is_active,
            health_status=node.health_status,
            last_health_check=node.last_health_check.isoformat() if node.last_health_check else None,
            created_at=node.created_at.isoformat() if node.created_at else None,
            updated_at=node.updated_at.isoformat() if node.updated_at else None
        )


@router.delete("/{node_id}")
async def delete_node(
    node_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Delete a node (also deletes all model associations).
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        node = await repo.get_by_id(node_id)
        
        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        
        node_name = node.name
        
        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="delete_node",
            entity_type="node",
            entity_id=str(node_id),
            details={"name": node_name},
            admin_ip=None
        )
        
        deleted = await repo.delete(node_id)
        
        return {
            "success": True,
            "message": f"Node '{node_name}' deleted",
            "node_id": node_id
        }


# ==================== HEALTH CHECK ====================

@router.post("/{node_id}/health-check")
async def check_node_health(
    node_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Perform health check on a node.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        node = await repo.get_by_id(node_id)
        
        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
        
        # Perform health check
        is_healthy, error = await node_manager.health_check_node(
            node.base_url,
            node.api_key
        )
        
        # Update node status
        status = "healthy" if is_healthy else "unhealthy"
        await repo.update_health_status(node_id, status, error)
        await session.commit()
        
        return {
            "node_id": node_id,
            "node_name": node.name,
            "health_status": status,
            "error": error,
            "checked_at": datetime.now(timezone.utc).isoformat()
        }


# ==================== MODEL SYNC ====================

@router.post("/{node_id}/sync-models", response_model=NodeSyncResponse)
async def sync_node_models(
    node_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Sync models from a specific node by calling /api/tags.
    
    This will:
    1. Connect to the node
    2. Get all models via /api/tags
    3. Update database with available models
    """
    async with async_session_maker() as session:
        result = await node_manager.sync_node_models(node_id, session)
        
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error", "Sync failed"))
        
        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="sync_node_models",
            entity_type="node",
            entity_id=str(node_id),
            details={"synced_count": result["synced_count"]},
            admin_ip=None
        )
        
        return NodeSyncResponse(**result)


@router.post("/sync-all", response_model=List[NodeSyncResponse])
async def sync_all_nodes(
    admin: str = Depends(verify_admin)
):
    """
    Sync models from all active nodes.
    """
    async with async_session_maker() as session:
        results = await node_manager.sync_all_nodes(session)
        
        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="sync_all_nodes",
            entity_type="node",
            entity_id="all",
            details={
                "total_nodes": results["total_nodes"],
                "successful": results["successful_nodes"],
                "failed": results["failed_nodes"]
            },
            admin_ip=None
        )
        
        return results["results"]


# ==================== MODEL DISTRIBUTION ====================

@router.get("/models/distribution", response_model=List[ModelDistributionRow])
async def get_model_distribution(
    admin: str = Depends(verify_admin)
):
    """
    Get distribution of models across nodes.
    
    Shows which models are available on which nodes.
    Useful for seeing the overall model availability picture.
    """
    async with async_session_maker() as session:
        distribution = await node_manager.get_model_distribution(session)
        return distribution


@router.get("/load-balancer/status")
async def get_load_balancer_status(
    admin: str = Depends(verify_admin)
):
    """
    Get current load balancer status and metrics.
    """
    async with async_session_maker() as session:
        status = await load_balancer.get_load_status(session)
        return status


# ==================== MODEL PULL ====================

@router.post("/{node_id}/pull-model")
async def pull_model_to_node(
    node_id: int,
    request: PullModelRequest,
    admin: str = Depends(verify_admin)
):
    """
    Pull a model to a specific node.
    
    Streams progress updates.
    """
    async with async_session_maker() as session:
        
        async def stream_progress():
            try:
                async for chunk in node_manager.pull_model_to_node(
                    node_id,
                    request.name,
                    request.stream,
                    session
                ):
                    yield f"{chunk}\n"
            except Exception as e:
                yield f'{{"error": "{str(e)}"}}\n'
        
        return StreamingResponse(
            stream_progress(),
            media_type="application/x-ndjson"
        )


@router.post("/pull-model-all")
async def pull_model_to_all_nodes(
    request: PullModelRequest,
    admin: str = Depends(verify_admin)
):
    """
    Pull a model to all active nodes.
    
    Streams progress from each node.
    """
    async with async_session_maker() as session:
        
        async def stream_progress():
            try:
                async for chunk in node_manager.pull_model_to_all_nodes(
                    request.name,
                    request.stream,
                    session
                ):
                    yield f"{chunk}\n"
            except Exception as e:
                yield f'{{"error": "{str(e)}"}}\n'
        
        return StreamingResponse(
            stream_progress(),
            media_type="application/x-ndjson"
        )


# ==================== NODE LOAD METRICS ====================

@router.get("/{node_id}/metrics")
async def get_node_metrics(
    node_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Get load metrics for a specific node.
    """
    async with async_session_maker() as session:
        metric_repo = NodeLoadMetricRepository(session)
        metrics = await metric_repo.get_current_load(node_id)
        
        if not metrics:
            return {
                "node_id": node_id,
                "active_requests": 0,
                "total_requests_today": 0,
                "avg_response_time_ms": None
            }
        
        return metrics