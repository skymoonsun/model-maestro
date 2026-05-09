"""
Admin Configuration endpoints.
Manages system config, model config, tool sets, and format patterns.
"""

import json
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.auth import verify_admin
from app.database import async_session_maker
from app.models import (
    SystemConfigItem,
    SystemConfigResponse,
    UpdateSystemConfigRequest,
    ModelConfigRequest,
    ModelConfigResponse,
    ToolSetRequest,
    ToolSetResponse,
    ModelFormatPatternRequest,
    ModelFormatPatternResponse,
    AuditLogResponse,
)
from app.repositories.system_config_repository import SystemConfigRepository
from app.repositories.model_config_repository import ModelConfigRepository
from app.repositories.tool_set_repository import ToolSetRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.services import config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


# =============================================================================
# Helper: Create audit log
# =============================================================================

async def _audit_log(action: str, entity_type: str, entity_id: str = None,
                     details: dict = None, request: Request = None):
    """Create an audit log entry"""
    try:
        admin_ip = None
        if request:
            admin_ip = request.client.host if request.client else None
        
        async with async_session_maker() as session:
            repo = AuditLogRepository(session)
            await repo.create(
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
                admin_ip=admin_ip
            )
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to create audit log: {e}")


# =============================================================================
# System Configuration Endpoints
# =============================================================================

@router.get("/config", response_model=SystemConfigResponse, tags=["Admin - System Config"])
async def get_system_config(admin: str = Depends(verify_admin)):
    """
    Get all system configuration (grouped by category).
    
    Returns background_tasks, http_client, defaults, and ollama_unsupported_params.
    """
    await config_manager.ensure_loaded()
    grouped = config_manager.get_grouped_config()
    return SystemConfigResponse(**grouped)


@router.get("/config/raw", tags=["Admin - System Config"])
async def get_system_config_raw(admin: str = Depends(verify_admin)):
    """
    Get all system configuration as raw key-value pairs.
    """
    async with async_session_maker() as session:
        repo = SystemConfigRepository(session)
        configs = await repo.get_all()
        
        return {
            "configs": [
                {
                    "key": c.key,
                    "value": c.value,
                    "description": c.description,
                    "updated_at": str(c.updated_at) if c.updated_at else None,
                }
                for c in configs
            ]
        }


@router.put("/config", response_model=SystemConfigResponse, tags=["Admin - System Config"])
async def update_system_config(
    update: UpdateSystemConfigRequest,
    request: Request,
    admin: str = Depends(verify_admin)
):
    """
    Update system configuration (partial update).
    
    Only provided fields will be updated.
    """
    updates = {}
    
    if update.background_tasks:
        for key, value in update.background_tasks.items():
            updates[f"background_tasks.{key}"] = str(value)
    
    if update.http_client:
        for key, value in update.http_client.items():
            updates[f"http_client.{key}"] = str(value)
    
    if update.defaults:
        for key, value in update.defaults.items():
            updates[f"defaults.{key}"] = str(value)

    if update.search:
        for key, value in update.search.items():
            updates[f"search.{key}"] = str(value)

    if update.ollama_unsupported_params is not None:
        updates["ollama_unsupported_params"] = json.dumps(update.ollama_unsupported_params)

    if updates:
        async with async_session_maker() as session:
            repo = SystemConfigRepository(session)
            for key, value in updates.items():
                await repo.upsert(key, value)
            await session.commit()
        
        await _audit_log("update_system_config", "system_config", details=updates, request=request)
        await config_manager.invalidate()
        await config_manager.load_all()
    
    grouped = config_manager.get_grouped_config()
    return SystemConfigResponse(**grouped)


# =============================================================================
# Model Configuration Endpoints
# =============================================================================

@router.get("/model-config", response_model=List[ModelConfigResponse], tags=["Admin - Model Config"])
async def list_model_configs(admin: str = Depends(verify_admin)):
    """
    List all model configurations.
    """
    async with async_session_maker() as session:
        repo = ModelConfigRepository(session)
        configs = await repo.get_all()
        
        return [
            ModelConfigResponse(
                id=c.id,
                model_prefix=c.model_prefix,
                allowed_tools=c.allowed_tools,
                unsupported_params=c.unsupported_params,
                default_context_length=c.default_context_length,
                max_context_length=c.max_context_length,
                requests_per_minute=c.requests_per_minute,
                tokens_per_minute=c.tokens_per_minute,
                is_active=c.is_active,
                maintenance_mode=c.maintenance_mode,
                description=c.description,
                cost_multiplier=float(c.cost_multiplier) if c.cost_multiplier else 1.0,
                created_at=str(c.created_at) if c.created_at else None,
                updated_at=str(c.updated_at) if c.updated_at else None,
            )
            for c in configs
        ]


@router.post("/model-config", response_model=ModelConfigResponse, status_code=201, tags=["Admin - Model Config"])
async def create_model_config(
    config_req: ModelConfigRequest,
    request: Request,
    admin: str = Depends(verify_admin)
):
    """
    Create a new model configuration.
    
    The model_prefix is used for prefix matching: e.g., "deepseek" will match
    "deepseek-v3.1:671b", "deepseek-r1:latest", etc.
    """
    async with async_session_maker() as session:
        repo = ModelConfigRepository(session)
        
        # Check if prefix already exists
        existing = await repo.get_by_prefix(config_req.model_prefix)
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Model config with prefix '{config_req.model_prefix}' already exists (id={existing.id})"
            )
        
        config = await repo.create(**config_req.model_dump())
        await session.commit()
        await session.refresh(config)
        
        await _audit_log(
            "create_model_config", "model_config",
            entity_id=str(config.id),
            details=config_req.model_dump(),
            request=request
        )
        await config_manager.invalidate()
        
        return ModelConfigResponse(
            id=config.id,
            model_prefix=config.model_prefix,
            allowed_tools=config.allowed_tools,
            unsupported_params=config.unsupported_params,
            default_context_length=config.default_context_length,
            max_context_length=config.max_context_length,
            requests_per_minute=config.requests_per_minute,
            tokens_per_minute=config.tokens_per_minute,
            is_active=config.is_active,
            maintenance_mode=config.maintenance_mode,
            description=config.description,
            cost_multiplier=float(config.cost_multiplier) if config.cost_multiplier else 1.0,
            created_at=str(config.created_at) if config.created_at else None,
            updated_at=str(config.updated_at) if config.updated_at else None,
        )


@router.put("/model-config/{config_id}", response_model=ModelConfigResponse, tags=["Admin - Model Config"])
async def update_model_config(
    config_id: int,
    config_req: ModelConfigRequest,
    request: Request,
    admin: str = Depends(verify_admin)
):
    """
    Update an existing model configuration.
    """
    async with async_session_maker() as session:
        repo = ModelConfigRepository(session)
        
        config = await repo.update(config_id, **config_req.model_dump())
        if not config:
            raise HTTPException(status_code=404, detail=f"Model config with id {config_id} not found")
        
        await session.commit()
        await session.refresh(config)
        
        await _audit_log(
            "update_model_config", "model_config",
            entity_id=str(config_id),
            details=config_req.model_dump(),
            request=request
        )
        await config_manager.invalidate()
        
        return ModelConfigResponse(
            id=config.id,
            model_prefix=config.model_prefix,
            allowed_tools=config.allowed_tools,
            unsupported_params=config.unsupported_params,
            default_context_length=config.default_context_length,
            max_context_length=config.max_context_length,
            requests_per_minute=config.requests_per_minute,
            tokens_per_minute=config.tokens_per_minute,
            is_active=config.is_active,
            maintenance_mode=config.maintenance_mode,
            description=config.description,
            cost_multiplier=float(config.cost_multiplier) if config.cost_multiplier else 1.0,
            created_at=str(config.created_at) if config.created_at else None,
            updated_at=str(config.updated_at) if config.updated_at else None,
        )


@router.delete("/model-config/{config_id}", status_code=204, tags=["Admin - Model Config"])
async def delete_model_config(
    config_id: int,
    request: Request,
    admin: str = Depends(verify_admin)
):
    """
    Delete a model configuration.
    """
    async with async_session_maker() as session:
        repo = ModelConfigRepository(session)
        success = await repo.delete_by_id(config_id)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Model config with id {config_id} not found")
        
        await session.commit()
        
        await _audit_log(
            "delete_model_config", "model_config",
            entity_id=str(config_id),
            request=request
        )
        await config_manager.invalidate()
    
    return None


# =============================================================================
# Tool Set Endpoints
# =============================================================================

@router.get("/tool-sets", response_model=List[ToolSetResponse], tags=["Admin - Tool Sets"])
async def list_tool_sets(admin: str = Depends(verify_admin)):
    """
    List all tool sets.
    """
    async with async_session_maker() as session:
        repo = ToolSetRepository(session)
        tool_sets = await repo.get_all()
        
        return [
            ToolSetResponse(
                id=ts.id,
                name=ts.name,
                tools=ts.tools,
                description=ts.description,
                created_at=str(ts.created_at) if ts.created_at else None,
                updated_at=str(ts.updated_at) if ts.updated_at else None,
            )
            for ts in tool_sets
        ]


@router.post("/tool-sets", response_model=ToolSetResponse, status_code=201, tags=["Admin - Tool Sets"])
async def create_tool_set(
    ts_req: ToolSetRequest,
    request: Request,
    admin: str = Depends(verify_admin)
):
    """
    Create a new tool set.
    
    Set tools to null for "full" (all tools allowed).
    """
    async with async_session_maker() as session:
        repo = ToolSetRepository(session)
        
        # Check if name already exists
        existing = await repo.get_by_name(ts_req.name)
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Tool set with name '{ts_req.name}' already exists"
            )
        
        ts = await repo.create(
            name=ts_req.name,
            tools=ts_req.tools,
            description=ts_req.description
        )
        await session.commit()
        await session.refresh(ts)
        
        await _audit_log(
            "create_tool_set", "tool_set",
            entity_id=str(ts.id),
            details=ts_req.model_dump(),
            request=request
        )
        await config_manager.invalidate()
        
        return ToolSetResponse(
            id=ts.id,
            name=ts.name,
            tools=ts.tools,
            description=ts.description,
            created_at=str(ts.created_at) if ts.created_at else None,
            updated_at=str(ts.updated_at) if ts.updated_at else None,
        )


@router.put("/tool-sets/{tool_set_id}", response_model=ToolSetResponse, tags=["Admin - Tool Sets"])
async def update_tool_set(
    tool_set_id: int,
    ts_req: ToolSetRequest,
    request: Request,
    admin: str = Depends(verify_admin)
):
    """
    Update an existing tool set.
    """
    async with async_session_maker() as session:
        repo = ToolSetRepository(session)
        
        ts = await repo.update(
            tool_set_id,
            name=ts_req.name,
            tools=ts_req.tools,
            description=ts_req.description
        )
        if not ts:
            raise HTTPException(status_code=404, detail=f"Tool set with id {tool_set_id} not found")
        
        await session.commit()
        await session.refresh(ts)
        
        await _audit_log(
            "update_tool_set", "tool_set",
            entity_id=str(tool_set_id),
            details=ts_req.model_dump(),
            request=request
        )
        await config_manager.invalidate()
        
        return ToolSetResponse(
            id=ts.id,
            name=ts.name,
            tools=ts.tools,
            description=ts.description,
            created_at=str(ts.created_at) if ts.created_at else None,
            updated_at=str(ts.updated_at) if ts.updated_at else None,
        )


@router.delete("/tool-sets/{tool_set_id}", status_code=204, tags=["Admin - Tool Sets"])
async def delete_tool_set(
    tool_set_id: int,
    request: Request,
    admin: str = Depends(verify_admin)
):
    """
    Delete a tool set.
    """
    async with async_session_maker() as session:
        repo = ToolSetRepository(session)
        success = await repo.delete_by_id(tool_set_id)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Tool set with id {tool_set_id} not found")
        
        await session.commit()
        
        await _audit_log(
            "delete_tool_set", "tool_set",
            entity_id=str(tool_set_id),
            request=request
        )
        await config_manager.invalidate()
    
    return None


# =============================================================================
# Audit Log Endpoints
# =============================================================================

@router.get("/audit-logs", tags=["Admin - Audit Logs"])
async def get_audit_logs(
    limit: int = 50,
    offset: int = 0,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    admin: str = Depends(verify_admin)
):
    """
    Get admin audit logs.
    
    Optional filters:
    - action: Filter by action type (e.g., "create_user", "update_config")
    - entity_type: Filter by entity type (e.g., "user", "model_config")
    """
    async with async_session_maker() as session:
        repo = AuditLogRepository(session)
        
        if action:
            logs = await repo.get_by_action(action, limit=limit)
        elif entity_type:
            logs = await repo.get_by_entity(entity_type, limit=limit)
        else:
            logs = await repo.get_recent(limit=limit, offset=offset)
        
        total = await repo.count()
        
        return {
            "logs": [
                AuditLogResponse(
                    id=log.id,
                    action=log.action,
                    entity_type=log.entity_type,
                    entity_id=log.entity_id,
                    details=log.details,
                    admin_ip=log.admin_ip,
                    created_at=str(log.created_at) if log.created_at else None,
                ).model_dump()
                for log in logs
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


# =============================================================================
# Grafana Assistant Configuration Endpoints
# =============================================================================

@router.get("/grafana/config", tags=["Admin - Grafana Config"])
async def get_grafana_config(admin: str = Depends(verify_admin)):
    """
    Get Grafana Assistant LLM configuration.
    Returns the configured model name and available mapped models (display names).
    """
    from app.models_db import SystemConfig

    async with async_session_maker() as session:
        cfg = await session.get(SystemConfig, "grafana_llm_model")
        current = cfg.value.strip() if cfg and cfg.value else ""

    available: set[str] = set()
    try:
        from app.config import model_mapper
        # Force-load mappings from DB/cache file
        await model_mapper.reload()
        # Add all mapped display names
        available.update(model_mapper.get_all_mappings().keys())
    except Exception as exc:
        logger.warning(f"Failed to get mapped model list for Grafana config: {exc}")

    try:
        # Add all live models from nodes (both Ollama and vLLM)
        from app.node_manager import node_manager
        all_models = await node_manager.get_all_models_from_nodes()
        logger.info(f"[GrafanaConfig] Live models fetched: {len(all_models.get('models', []))} total, "
                    f"queried={all_models.get('nodes_queried')}, failed={all_models.get('nodes_failed')}")
        for m in all_models.get("models", []):
            name = m.get("name")
            if name:
                available.add(name)
                logger.debug(f"[GrafanaConfig] Adding live model: {name}")
    except Exception as exc:
        logger.warning(f"Failed to get live models for Grafana config: {exc}")

    # Also add real model names from DB (as fallback if node discovery failed)
    try:
        from app.models_db import NodeModel
        async with async_session_maker() as session:
            result = await session.execute(
                select(NodeModel.model_name).where(NodeModel.is_available == True).distinct()
            )
            available.update(row[0] for row in result.all() if row[0])
    except Exception as exc:
        logger.warning(f"Failed to get DB models for Grafana config: {exc}")

    # Ensure currently selected model stays in the list even if unmapped
    if current:
        available.add(current)

    # Build enriched model details
    model_details = []
    try:
        from app.models_db import NodeModel, OllamaNode
        async with async_session_maker() as session:
            result = await session.execute(
                select(NodeModel.model_name, OllamaNode.name, OllamaNode.node_type)
                .join(OllamaNode, NodeModel.node_id == OllamaNode.id)
                .where(NodeModel.is_available == True)
            )
            model_nodes_map: Dict[str, List[Dict[str, str]]] = {}
            for model_name, node_name, node_type in result.all():
                model_nodes_map.setdefault(model_name, []).append({"name": node_name, "node_type": node_type})

        for name in sorted(available):
            display_name = model_mapper.get_display_model_name(name)
            is_mapped = display_name is not None and display_name != name
            nodes = model_nodes_map.get(name, [])
            model_details.append({
                "name": name,
                "display_name": display_name if is_mapped else None,
                "is_mapped": is_mapped,
                "nodes": [{"name": n["name"], "node_type": n["node_type"]} for n in nodes],
            })
    except Exception as exc:
        logger.warning(f"Failed to build model details for Grafana config: {exc}")

    return {
        "model": current,
        "available_models": sorted(available),
        "model_details": model_details,
    }


@router.put("/grafana/config", tags=["Admin - Grafana Config"])
async def update_grafana_config(
    request: Request,
    update: dict,
    admin: str = Depends(verify_admin)
):
    """
    Update Grafana Assistant LLM configuration (e.g. which model to use).
    """
    from app.models_db import SystemConfig

    model_name = str(update.get("model", "")).strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="'model' is required")

    async with async_session_maker() as session:
        cfg = await session.get(SystemConfig, "grafana_llm_model")
        if cfg:
            cfg.value = model_name
        else:
            session.add(SystemConfig(
                key="grafana_llm_model",
                value=model_name,
                description="LLM model for Grafana Assistant proxy",
            ))
        await session.commit()

    await _audit_log(
        "update_grafana_config", "system_config",
        entity_id="grafana_llm_model",
        details=update,
        request=request,
    )

    return {"model": model_name, "status": "updated"}
