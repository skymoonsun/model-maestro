"""Admin API endpoints for Ollama node management"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional
import logging
import re
from pydantic import BaseModel

from app.database import async_session_maker
from app.models import (
    OllamaNodeCreate,
    OllamaNodeUpdate,
    OllamaNodeResponse,
    OllamaNodeDetailResponse,
    ModelDistributionRow,
    NodeSyncResponse,
    PullModelRequest,
    PullModelResponse,
    NodePriorityBatchRequest,
    NodePriorityBatchResponse,
    AntigravityQuotaResponse,
    AntigravityQuotaData,
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


def _validate_bedrock_node_fields(
    *,
    api_key: Optional[str],
    aws_secret_key: Optional[str],
    aws_region: Optional[str],
    bedrock_auth_mode: Optional[str],
) -> str:
    """Normalize auth mode and raise if credentials are incomplete."""
    from app.bedrock_proxy import bedrock_credentials_configured, resolve_bedrock_auth_mode

    mode = resolve_bedrock_auth_mode(bedrock_auth_mode, aws_secret_key)
    if not bedrock_credentials_configured(
        api_key=api_key,
        secret_key=aws_secret_key,
        region=aws_region,
        bedrock_auth_mode=mode,
    ):
        raise HTTPException(
            status_code=400,
            detail="Bedrock node requires AWS region and either IAM access key + secret, or a Bedrock API key",
        )
    return mode


def _validate_cursor_node_fields(
    *,
    api_key: Optional[str],
) -> None:
    """Raise if Cursor API key is missing or invalid."""
    from app.cursor_proxy import cursor_credentials_configured

    if not cursor_credentials_configured(api_key):
        raise HTTPException(
            status_code=400,
            detail="Cursor node requires a valid API key (crsr_... prefix)",
        )


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

        # Validate code format
        if request.code is not None:
            if not re.match(r'^[a-z0-9_-]{1,30}$', request.code):
                raise HTTPException(status_code=400, detail="Node code must be 1-30 chars, lowercase alphanumeric with hyphens/underscores only")
            existing_code = await repo.get_by_code(request.code)
            if existing_code:
                raise HTTPException(status_code=400, detail=f"Node code '{request.code}' already exists")

        # Auto-generate base_url for Bedrock if not provided
        base_url = request.base_url
        if request.node_type == 'bedrock' and (not base_url or not base_url.strip()):
            region = request.aws_region or 'us-east-1'
            base_url = f"https://bedrock-runtime.{region}.amazonaws.com"

        bedrock_mode: Optional[str] = request.bedrock_auth_mode
        aws_secret = request.aws_secret_key
        aws_session = request.aws_session_token
        if request.node_type == 'bedrock':
            bedrock_mode = _validate_bedrock_node_fields(
                api_key=request.api_key,
                aws_secret_key=request.aws_secret_key,
                aws_region=request.aws_region,
                bedrock_auth_mode=request.bedrock_auth_mode,
            )
            if bedrock_mode == 'api_key':
                aws_secret = None
                aws_session = None

        # Validate Cursor API key when creating a Cursor node
        if request.node_type == 'cursor':
            _validate_cursor_node_fields(api_key=request.api_key)
            # Use default Cursor proxy if not provided
            if not base_url or not base_url.strip():
                base_url = 'https://cursor-api.standardagents.ai/v1'

        # Create node
        node = await repo.create(
            name=request.name,
            base_url=base_url,
            api_key=request.api_key,
            priority=request.priority or 0,
            weight=request.weight or 100,
            is_active=request.is_active if request.is_active is not None else True,
            node_type=request.node_type or 'ollama',
            warmup_enabled=request.warmup_enabled if request.warmup_enabled is not None else True,
            auto_sync_enabled=request.auto_sync_enabled if request.auto_sync_enabled is not None else True,
            health_check_url=request.health_check_url,
            code=request.code,
            headers=request.headers,
            oauth_tokens=request.oauth_tokens.model_dump() if request.oauth_tokens else None,
            project_id=request.project_id,
            aws_secret_key=aws_secret,
            aws_region=request.aws_region,
            aws_session_token=aws_session,
            bedrock_auth_mode=bedrock_mode,
            scoped_models=request.scoped_models if request.scoped_models is not None else False,
            auto_cookie_refresh=request.auto_cookie_refresh if request.auto_cookie_refresh is not None else False
        )

        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="create_node",
            entity_type="node",
            entity_id=str(node.id),
            details={"name": node.name, "base_url": node.base_url, "code": node.code},
            admin_ip=None
        )

        # Invalidate cache so proxy picks up the new node immediately
        await node_manager.invalidate_cache()
        await load_balancer.invalidate_cache()

        return OllamaNodeResponse(
            id=node.id,
            name=node.name,
            base_url=node.base_url,
            api_key=node.api_key,
            api_key_set=bool(node.api_key),
            priority=node.priority,
            weight=node.weight,
            is_active=node.is_active,
            node_type=node.node_type,
            warmup_enabled=node.warmup_enabled,
            auto_sync_enabled=node.auto_sync_enabled,
            code=node.code,
            health_status=node.health_status,
            last_health_check=node.last_health_check.isoformat() if node.last_health_check else None,
            created_at=node.created_at.isoformat() if node.created_at else None,
            updated_at=node.updated_at.isoformat() if node.updated_at else None,
            headers=node.headers,
            oauth_tokens=node.oauth_tokens,
            project_id=node.project_id,
            aws_secret_key=node.aws_secret_key,
            aws_region=node.aws_region,
            aws_session_token=node.aws_session_token,
            bedrock_auth_mode=getattr(node, "bedrock_auth_mode", None),
            scoped_models=node.scoped_models if node.scoped_models is not None else False,
            auto_cookie_refresh=node.auto_cookie_refresh if node.auto_cookie_refresh is not None else False
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
            api_key=node.api_key,
            api_key_set=bool(node.api_key),
            priority=node.priority,
            weight=node.weight,
            is_active=node.is_active,
            node_type=node.node_type,
            warmup_enabled=node.warmup_enabled,
            auto_sync_enabled=node.auto_sync_enabled,
            code=node.code,
            health_status=node.health_status,
            last_health_check=node.last_health_check.isoformat() if node.last_health_check else None,
            created_at=node.created_at.isoformat() if node.created_at else None,
            updated_at=node.updated_at.isoformat() if node.updated_at else None,
            headers=node.headers,
            oauth_tokens=node.oauth_tokens,
            project_id=node.project_id,
            aws_secret_key=node.aws_secret_key,
            aws_region=node.aws_region,
            aws_session_token=node.aws_session_token,
            bedrock_auth_mode=getattr(node, "bedrock_auth_mode", None),
            auto_cookie_refresh=node.auto_cookie_refresh if node.auto_cookie_refresh is not None else False,
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
        existing_node = await repo.get_by_id(node_id)
        if not existing_node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

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

        if request.node_type is not None:
            update_data["node_type"] = request.node_type

        if request.warmup_enabled is not None:
            update_data["warmup_enabled"] = request.warmup_enabled

        if request.auto_sync_enabled is not None:
            update_data["auto_sync_enabled"] = request.auto_sync_enabled

        if request.health_check_url is not None:
            update_data["health_check_url"] = request.health_check_url

        if request.code is not None:
            if request.code == '':
                update_data["code"] = None
            elif not re.match(r'^[a-z0-9_-]{1,30}$', request.code):
                raise HTTPException(status_code=400, detail="Node code must be 1-30 chars, lowercase alphanumeric with hyphens/underscores only")
            else:
                existing_code = await repo.get_by_code(request.code)
                if existing_code and existing_code.id != node_id:
                    raise HTTPException(status_code=400, detail=f"Node code '{request.code}' already exists")
                update_data["code"] = request.code

        if request.headers is not None:
            update_data["headers"] = request.headers if request.headers else None

        if request.oauth_tokens is not None:
            update_data["oauth_tokens"] = request.oauth_tokens.model_dump() if request.oauth_tokens else None

        if request.project_id is not None:
            update_data["project_id"] = request.project_id

        if request.aws_secret_key is not None:
            update_data["aws_secret_key"] = request.aws_secret_key

        if request.aws_region is not None:
            update_data["aws_region"] = request.aws_region

        if request.aws_session_token is not None:
            update_data["aws_session_token"] = request.aws_session_token

        if request.bedrock_auth_mode is not None:
            update_data["bedrock_auth_mode"] = request.bedrock_auth_mode

        effective_type = update_data.get("node_type", existing_node.node_type)
        if effective_type == "bedrock":
            mode = _validate_bedrock_node_fields(
                api_key=update_data.get("api_key", existing_node.api_key),
                aws_secret_key=update_data.get("aws_secret_key", existing_node.aws_secret_key),
                aws_region=update_data.get("aws_region", existing_node.aws_region),
                bedrock_auth_mode=update_data.get(
                    "bedrock_auth_mode", getattr(existing_node, "bedrock_auth_mode", None)
                ),
            )
            update_data["bedrock_auth_mode"] = mode
            if mode == "api_key":
                update_data["aws_secret_key"] = None
                update_data["aws_session_token"] = None

        if effective_type == "cursor":
            _validate_cursor_node_fields(
                api_key=update_data.get("api_key", existing_node.api_key),
            )
            if not update_data.get("base_url", existing_node.base_url):
                update_data["base_url"] = "https://api2.cursor.sh/v1"

        if request.scoped_models is not None:
            update_data["scoped_models"] = request.scoped_models

        if request.auto_cookie_refresh is not None:
            update_data["auto_cookie_refresh"] = request.auto_cookie_refresh

        node = await repo.update(node_id, **update_data)

        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        # Invalidate Redis cache so proxy picks up new api_key / headers immediately
        await node_manager.invalidate_cache()
        await load_balancer.invalidate_cache()

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
            api_key=node.api_key,
            api_key_set=bool(node.api_key),
            priority=node.priority,
            weight=node.weight,
            is_active=node.is_active,
            node_type=node.node_type,
            warmup_enabled=node.warmup_enabled,
            auto_sync_enabled=node.auto_sync_enabled,
            code=node.code,
            health_status=node.health_status,
            last_health_check=node.last_health_check.isoformat() if node.last_health_check else None,
            created_at=node.created_at.isoformat() if node.created_at else None,
            updated_at=node.updated_at.isoformat() if node.updated_at else None,
            headers=node.headers,
            oauth_tokens=node.oauth_tokens,
            project_id=node.project_id,
            aws_secret_key=node.aws_secret_key,
            aws_region=node.aws_region,
            aws_session_token=node.aws_session_token,
            bedrock_auth_mode=getattr(node, "bedrock_auth_mode", None),
            scoped_models=node.scoped_models if node.scoped_models is not None else False,
            auto_cookie_refresh=node.auto_cookie_refresh if node.auto_cookie_refresh is not None else False
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

        # Invalidate cache so proxy stops routing to the deleted node
        await node_manager.invalidate_cache()
        await load_balancer.invalidate_cache()

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
        is_healthy, error, updated_headers = await node_manager.health_check_node(
            node.base_url,
            node.api_key,
            node_type=getattr(node, 'node_type', 'ollama'),
            headers=getattr(node, 'headers', None),
            oauth_tokens=getattr(node, 'oauth_tokens', None),
            project_id=getattr(node, 'project_id', None),
            aws_secret_key=getattr(node, 'aws_secret_key', None),
            aws_region=getattr(node, 'aws_region', None),
            aws_session_token=getattr(node, 'aws_session_token', None),
            bedrock_auth_mode=getattr(node, 'bedrock_auth_mode', None),
            health_check_url=getattr(node, 'health_check_url', None),
            auto_cookie_refresh=getattr(node, 'auto_cookie_refresh', False),
        )

        # Persist refreshed WAF cookie if captured
        if updated_headers:
            await repo.update(node_id, headers=updated_headers)

        # Update node status
        status = "healthy" if is_healthy else "unhealthy"
        await repo.update_health_status(node_id, status, error)
        await session.commit()

        # Invalidate cache so proxy picks up the new health status immediately
        await node_manager.invalidate_cache()
        await load_balancer.invalidate_cache()

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

        # Invalidate cache so proxy picks up the new model catalog immediately
        await node_manager.invalidate_cache()
        await load_balancer.invalidate_cache()

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

        # Invalidate cache so proxy picks up the refreshed catalogs immediately
        await node_manager.invalidate_cache()
        await load_balancer.invalidate_cache()

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


@router.patch("/batch/priority", response_model=NodePriorityBatchResponse)
async def update_node_priorities(
    request: NodePriorityBatchRequest,
    admin: str = Depends(verify_admin)
):
    """
    Batch update node priorities via drag-and-drop reordering.

    Expects a list of {node_id, priority} objects.
    Higher priority = preferred in fallback order.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        updated_nodes = []

        # Single transaction: collect all nodes first, then commit once
        for item in request.priorities:
            node = await repo.get_by_id(item.node_id)
            if node:
                node.priority = item.priority
                updated_nodes.append(node)

        await session.commit()

        # Refresh all updated nodes
        for node in updated_nodes:
            await session.refresh(node)

        # Build response
        response_nodes = []
        for node in updated_nodes:
            response_nodes.append(OllamaNodeResponse(
                id=node.id,
                name=node.name,
                base_url=node.base_url,
                api_key=node.api_key,
                api_key_set=bool(node.api_key),
                priority=node.priority,
                weight=node.weight,
                is_active=node.is_active,
                node_type=node.node_type,
                warmup_enabled=node.warmup_enabled,
                auto_sync_enabled=node.auto_sync_enabled,
                health_status=node.health_status,
                last_health_check=node.last_health_check.isoformat() if node.last_health_check else None,
                created_at=node.created_at.isoformat() if node.created_at else None,
                updated_at=node.updated_at.isoformat() if node.updated_at else None,
                auto_cookie_refresh=node.auto_cookie_refresh if node.auto_cookie_refresh is not None else False
            ))

        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="batch_update_priorities",
            entity_type="node",
            entity_id="batch",
            details={"updated_count": len(response_nodes)},
            admin_ip=None
        )

        # Invalidate cache so proxy picks up the new priority order immediately
        await node_manager.invalidate_cache()
        await load_balancer.invalidate_cache()

        return NodePriorityBatchResponse(
            updated=len(response_nodes),
            nodes=response_nodes
        )


# ==================== GOOGLE OAUTH (Antigravity) ====================

@router.get("/{node_id}/google-auth-url")
async def get_google_auth_url(
    node_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Generate Google OAuth consent screen URL for an antigravity node.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        node = await repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        from app.config import get_settings
        settings = get_settings()

        client_id = settings.google_client_id
        client_secret = settings.google_client_secret
        redirect_uri = settings.google_redirect_uri

        if not client_id or not client_secret:
            raise HTTPException(
                status_code=500,
                detail="Google OAuth credentials not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in environment."
            )

        from app.google_auth import GoogleOAuthManager
        manager = GoogleOAuthManager(client_id, client_secret, redirect_uri)

        # Generate a state token that encodes the node_id
        import secrets
        state = f"{node_id}:{secrets.token_urlsafe(16)}"

        auth_url = manager.get_auth_url(state=state)

        return {
            "auth_url": auth_url,
            "state": state,
            "node_id": node_id,
            "node_name": node.name,
        }


class GoogleOAuthCallbackRequest(BaseModel):
    code: str
    state: str


async def _process_google_oauth_callback(node_id: int, code: str, state: str) -> dict:
    """Shared logic for processing Google OAuth callback."""
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        node = await repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        from app.config import get_settings
        settings = get_settings()

        client_id = settings.google_client_id
        client_secret = settings.google_client_secret
        redirect_uri = settings.google_redirect_uri

        if not client_id or not client_secret:
            raise HTTPException(
                status_code=500,
                detail="Google OAuth credentials not configured."
            )

        from app.google_auth import GoogleOAuthManager, load_code_assist

        manager = GoogleOAuthManager(client_id, client_secret, redirect_uri)

        # Exchange code for tokens
        try:
            tokens = await manager.exchange_code(code)
        except Exception as e:
            logger.error(f"Token exchange failed: {e}")
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")

        # Get user info
        try:
            user_info = await manager.get_user_info(tokens["access_token"])
            user_email = user_info.get("email", "unknown")
        except Exception as e:
            logger.warning(f"User info fetch failed: {e}")
            user_email = "unknown"

        # Call loadCodeAssist to get project_id (optional — may fail for accounts
        # without Cloud Code Private API access; proxy works without it)
        project_id = None
        try:
            project_id = await load_code_assist(tokens["access_token"])
            logger.info(f"loadCodeAssist returned project_id: {project_id}")
        except Exception as e:
            logger.warning(f"loadCodeAssist failed (account may lack Cloud Code API access): {e}")

        # Update node with OAuth tokens and project_id (if any)
        update_data: dict = {
            "oauth_tokens": tokens,
        }
        if project_id:
            update_data["project_id"] = project_id

        updated_node = await repo.update(node_id, **update_data)

        if not updated_node:
            raise HTTPException(status_code=500, detail="Failed to update node with OAuth tokens")

        # Audit log
        audit_repo = AuditLogRepository(session)
        await audit_repo.create(
            action="google_oauth_connected",
            entity_type="node",
            entity_id=str(node_id),
            details={"email": user_email, "project_id": project_id},
            admin_ip=None
        )

        return {
            "success": True,
            "node_id": node_id,
            "node_name": node.name,
            "email": user_email,
            "project_id": project_id,
            "token_type": tokens.get("token_type", "Bearer"),
            "expires_in": tokens.get("expires_in"),
        }


@router.post("/{node_id}/google-auth-callback")
async def google_auth_callback_post(
    node_id: int,
    request: GoogleOAuthCallbackRequest,
    admin: str = Depends(verify_admin)
):
    """
    Handle Google OAuth callback manually (POST from admin panel).

    Exchanges the authorization code for tokens, fetches user info,
    calls loadCodeAssist to get project_id, and updates the node.
    """
    return await _process_google_oauth_callback(node_id, request.code, request.state)


@router.post("/{node_id}/google-refresh-token")
async def google_refresh_token(
    node_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Manually refresh Google OAuth access token for an antigravity node.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        node = await repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        if not node.oauth_tokens or not node.oauth_tokens.get("refresh_token"):
            raise HTTPException(status_code=400, detail="Node has no refresh_token")

        from app.config import get_settings
        settings = get_settings()

        client_id = settings.google_client_id
        client_secret = settings.google_client_secret

        if not client_id or not client_secret:
            raise HTTPException(
                status_code=500,
                detail="Google OAuth credentials not configured."
            )

        from app.google_auth import GoogleOAuthManager

        manager = GoogleOAuthManager(client_id, client_secret, "")

        try:
            new_tokens = await manager.refresh_access_token(node.oauth_tokens["refresh_token"])
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            raise HTTPException(status_code=400, detail=f"Token refresh failed: {e}")

        # Merge new tokens into existing token dict
        updated_tokens = dict(node.oauth_tokens)
        updated_tokens["access_token"] = new_tokens["access_token"]
        updated_tokens["expires_in"] = new_tokens["expires_in"]
        updated_tokens["obtained_at"] = new_tokens["obtained_at"]
        if new_tokens.get("scope"):
            updated_tokens["scope"] = new_tokens["scope"]

        await repo.update(node_id, oauth_tokens=updated_tokens)

        return {
            "success": True,
            "node_id": node_id,
            "node_name": node.name,
            "expires_in": new_tokens.get("expires_in"),
        }


@router.get("/{node_id}/quota", response_model=AntigravityQuotaResponse)
async def get_antigravity_quota(
    node_id: int,
    admin: str = Depends(verify_admin),
):
    """
    Fetch Google account quota for an Antigravity node (fetchAvailableModels).

    Uses the same APIs as Antigravity Manager: loadCodeAssist + fetchAvailableModels
    with Sandbox → Daily → Prod endpoint fallback.
    """
    async with async_session_maker() as session:
        repo = NodeRepository(session)
        node = await repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        if node.node_type != "antigravity":
            raise HTTPException(
                status_code=400,
                detail="Quota is only available for antigravity nodes",
            )

        if not node.oauth_tokens or not node.oauth_tokens.get("access_token"):
            raise HTTPException(
                status_code=400,
                detail="Node has no Google OAuth tokens. Connect Google Auth first.",
            )

        from app.google_quota import fetch_antigravity_quota

        tokens = dict(node.oauth_tokens)
        try:
            quota_raw, resolved_project_id = await fetch_antigravity_quota(
                tokens,
                node.project_id,
            )
        except Exception as exc:
            logger.error("[Quota] fetch failed for node %s: %s", node_id, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        update_data: dict = {}
        if tokens != node.oauth_tokens:
            update_data["oauth_tokens"] = tokens
        if resolved_project_id and resolved_project_id != node.project_id:
            update_data["project_id"] = resolved_project_id
        if update_data:
            await repo.update(node_id, **update_data)

        quota = AntigravityQuotaData.model_validate(quota_raw)

        return AntigravityQuotaResponse(
            success=True,
            node_id=node_id,
            node_name=node.name,
            project_id=resolved_project_id or node.project_id,
            quota=quota,
        )