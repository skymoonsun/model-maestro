"""
Admin endpoints for system prompt management.

CRUD over admin-defined system prompts that are transparently injected into
matching text-generation requests (see app/services/system_prompt_service.py).
Requires admin authentication. Every mutation invalidates the Redis cache and
writes an audit log entry.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import verify_admin
from app.database import async_session_maker
from app.repositories.system_prompt_repository import (
    SystemPromptRepository,
    VALID_SCOPE_TYPES,
)
from app.repositories.audit_log_repository import AuditLogRepository
from app.services.system_prompt_service import system_prompt_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/system-prompts", tags=["Admin - System Prompts"])


# ============================================================================
# Schemas
# ============================================================================

class SystemPromptCreate(BaseModel):
    scope_type: str = Field(..., description="user | model | mapping | node | group")
    scope_value: str = Field(..., min_length=1, max_length=255)
    prompt: str = Field(..., min_length=1)
    priority: int = 0
    is_active: bool = True
    description: Optional[str] = None


class SystemPromptUpdate(BaseModel):
    scope_type: Optional[str] = None
    scope_value: Optional[str] = Field(None, min_length=1, max_length=255)
    prompt: Optional[str] = Field(None, min_length=1)
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None


class SystemPromptReorder(BaseModel):
    ids: List[int] = Field(..., min_length=1, description="Prompt ids in desired order, first = highest priority")


class SystemPromptResponse(BaseModel):
    id: int
    scope_type: str
    scope_value: str
    prompt: str
    priority: int
    is_active: bool
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _to_response(row) -> SystemPromptResponse:
    return SystemPromptResponse(
        id=row.id,
        scope_type=row.scope_type,
        scope_value=row.scope_value,
        prompt=row.prompt,
        priority=row.priority,
        is_active=row.is_active,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_scope_type(scope_type: str) -> None:
    if scope_type not in VALID_SCOPE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scope_type '{scope_type}'. Must be one of: {', '.join(VALID_SCOPE_TYPES)}",
        )


async def _audit(session, action: str, entity_id, details: dict, request: Request) -> None:
    audit_repo = AuditLogRepository(session)
    admin_ip = request.client.host if request and request.client else None
    await audit_repo.create(
        action=action,
        entity_type="system_prompt",
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details,
        admin_ip=admin_ip,
    )


# ============================================================================
# Endpoints
# ============================================================================

@router.get("", response_model=List[SystemPromptResponse])
async def list_system_prompts(admin: str = Depends(verify_admin)):
    """List all system prompts (active and inactive)."""
    async with async_session_maker() as session:
        repo = SystemPromptRepository(session)
        rows = await repo.list_all()
        return [_to_response(r) for r in rows]


@router.put("/reorder", response_model=List[SystemPromptResponse])
async def reorder_system_prompts(
    body: SystemPromptReorder,
    request: Request,
    admin: str = Depends(verify_admin),
):
    """
    Reorder prompts by drag-and-drop: ``ids`` in desired order (first = highest).

    Reuses the group's existing priority values (sorted desc) when they are
    distinct, so cross-scope priority relationships are preserved; otherwise
    assigns a fresh descending sequence (len-1 .. 0).
    """
    async with async_session_maker() as session:
        repo = SystemPromptRepository(session)
        rows = await repo.get_by_ids(body.ids)
        if len(rows) != len(set(body.ids)):
            raise HTTPException(status_code=404, detail="One or more prompts not found")

        current = sorted((r.priority or 0 for r in rows), reverse=True)
        if len(set(current)) == len(current):
            new_priorities = current  # keep the same value set, permuted
        else:
            new_priorities = list(range(len(rows) - 1, -1, -1))

        mapping = {pid: new_priorities[i] for i, pid in enumerate(body.ids)}
        await repo.set_priorities(mapping)
        await _audit(session, "reorder_system_prompts", None, {"order": body.ids}, request)
        await session.commit()

        reordered = await repo.get_by_ids(body.ids)

    await system_prompt_service.invalidate_cache()
    reordered.sort(key=lambda r: body.ids.index(r.id))
    return [_to_response(r) for r in reordered]


@router.post("", response_model=SystemPromptResponse, status_code=201)
async def create_system_prompt(
    body: SystemPromptCreate,
    request: Request,
    admin: str = Depends(verify_admin),
):
    """Create a system prompt for a (scope_type, scope_value) target.

    Multiple prompts may target the same scope; they stack in priority order.
    """
    _validate_scope_type(body.scope_type)
    async with async_session_maker() as session:
        repo = SystemPromptRepository(session)
        row = await repo.create(
            scope_type=body.scope_type,
            scope_value=body.scope_value,
            prompt=body.prompt,
            priority=body.priority,
            is_active=body.is_active,
            description=body.description,
        )
        await _audit(
            session, "create_system_prompt", row.id,
            {"scope_type": row.scope_type, "scope_value": row.scope_value}, request,
        )
        await session.commit()

    await system_prompt_service.invalidate_cache()
    return _to_response(row)


@router.patch("/{prompt_id}", response_model=SystemPromptResponse)
async def update_system_prompt(
    prompt_id: int,
    body: SystemPromptUpdate,
    request: Request,
    admin: str = Depends(verify_admin),
):
    """Update fields of an existing system prompt."""
    if body.scope_type is not None:
        _validate_scope_type(body.scope_type)
    async with async_session_maker() as session:
        repo = SystemPromptRepository(session)
        row = await repo.get_by_id(prompt_id)
        if not row:
            raise HTTPException(status_code=404, detail="System prompt not found")

        row = await repo.update(
            row,
            scope_type=body.scope_type,
            scope_value=body.scope_value,
            prompt=body.prompt,
            priority=body.priority,
            is_active=body.is_active,
            description=body.description,
        )
        await _audit(
            session, "update_system_prompt", row.id,
            {"scope_type": row.scope_type, "scope_value": row.scope_value}, request,
        )
        await session.commit()

    await system_prompt_service.invalidate_cache()
    return _to_response(row)


@router.delete("/{prompt_id}", status_code=204)
async def delete_system_prompt(
    prompt_id: int,
    request: Request,
    admin: str = Depends(verify_admin),
):
    """Delete a system prompt."""
    async with async_session_maker() as session:
        repo = SystemPromptRepository(session)
        row = await repo.get_by_id(prompt_id)
        if not row:
            raise HTTPException(status_code=404, detail="System prompt not found")
        scope = {"scope_type": row.scope_type, "scope_value": row.scope_value}
        await repo.delete(row)
        await _audit(session, "delete_system_prompt", prompt_id, scope, request)
        await session.commit()

    await system_prompt_service.invalidate_cache()
    return None
