"""
Admin endpoints for model group management.
Requires admin token for authentication.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ModelGroupCreateRequest,
    ModelGroupUpdateRequest,
    ModelGroupMemberRequest,
    ModelGroupResponse,
    ModelGroupDetailResponse,
    ModelGroupMemberResponse,
    ModelGroupListResponse,
    MemberReorderRequest,
)
from app.auth import verify_admin
from app.config import model_group_manager
from app.repositories.model_group_repository import ModelGroupRepository
from app.database import async_session_maker
from app.models_db import ModelGroupMember, model_group_member_nodes

router = APIRouter(prefix="/admin/model-groups", tags=["Admin - Model Groups"])


# ============================================================================
# Model Group CRUD Endpoints
# ============================================================================

@router.post("", response_model=ModelGroupDetailResponse, status_code=201)
async def create_model_group(
    request: ModelGroupCreateRequest,
    admin: str = Depends(verify_admin)
):
    """
    Create a new model group with optional members.

    Request body:
    {
        "name": "smart-models",
        "description": "Smart models with fallback",
        "strategy": "round_robin",  // round_robin, weighted, priority
        "is_active": true,
        "members": [
            {
                "model_display_name": "glm-5:cloud",
                "capability_tags": ["vision", "code"],
                "weight": 1,
                "priority": 0,
                "is_fallback": false,
                "is_active": true
            }
        ]
    }
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)

        # Check if group already exists
        if await repo.group_exists(request.name):
            raise HTTPException(
                status_code=400,
                detail=f"Model group '{request.name}' already exists"
            )

        # Create group
        group = await repo.create_group(
            name=request.name,
            description=request.description,
            strategy=request.strategy,
            is_active=request.is_active,
        )

        # Add members if provided
        members: List[ModelGroupMember] = []
        if request.members:
            for member_req in request.members:
                member = await repo.add_member(
                    group_name=request.name,
                    model_display_name=member_req.model_display_name,
                    capability_tags=member_req.capability_tags,
                    weight=member_req.weight,
                    priority=member_req.priority,
                    is_fallback=member_req.is_fallback,
                    is_active=member_req.is_active,
                    preferred_node_ids=member_req.preferred_node_ids,
                )
                if member:
                    members.append(member)

        await session.commit()

        await model_group_manager.reload()

        member_responses = await _members_to_response(session, members)

        return ModelGroupDetailResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            strategy=group.strategy,
            is_active=group.is_active,
            created_at=group.created_at.isoformat() if group.created_at else None,
            updated_at=group.updated_at.isoformat() if group.updated_at else None,
            members=member_responses,
        )


@router.get("", response_model=ModelGroupListResponse)
async def list_model_groups(admin: str = Depends(verify_admin)):
    """
    List all model groups.

    Returns all groups (active and inactive).
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)
        groups = await repo.get_all_groups(active_only=False)

        return ModelGroupListResponse(
            groups=[
                ModelGroupResponse(
                    id=group.id,
                    name=group.name,
                    description=group.description,
                    strategy=group.strategy,
                    is_active=group.is_active,
                    created_at=group.created_at.isoformat() if group.created_at else None,
                    updated_at=group.updated_at.isoformat() if group.updated_at else None,
                )
                for group in groups
            ],
            total=len(groups),
        )


@router.get("/{name}", response_model=ModelGroupDetailResponse)
async def get_model_group(
    name: str,
    admin: str = Depends(verify_admin)
):
    """
    Get model group details with members.

    Returns the group and all its members.
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)
        result = await repo.get_group_with_members(name)

        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"Model group '{name}' not found"
            )

        group, members = result

        return ModelGroupDetailResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            strategy=group.strategy,
            is_active=group.is_active,
            created_at=group.created_at.isoformat() if group.created_at else None,
            updated_at=group.updated_at.isoformat() if group.updated_at else None,
            members=await _members_to_response(session, members),
        )


@router.put("/{name}", response_model=ModelGroupDetailResponse)
async def update_model_group(
    name: str,
    request: ModelGroupUpdateRequest,
    admin: str = Depends(verify_admin)
):
    """
    Update model group properties.

    Request body (partial update):
    {
        "description": "Updated description",
        "strategy": "priority",
        "is_active": false
    }
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)

        # Build update dict from request
        update_data = {}
        if request.description is not None:
            update_data["description"] = request.description
        if request.strategy is not None:
            update_data["strategy"] = request.strategy
        if request.is_active is not None:
            update_data["is_active"] = request.is_active

        if not update_data:
            # No fields to update, return current state
            result = await repo.get_group_with_members(name)
            if not result:
                raise HTTPException(
                    status_code=404,
                    detail=f"Model group '{name}' not found"
                )
            group, members = result
            await session.commit()
            return ModelGroupDetailResponse(
                id=group.id,
                name=group.name,
                description=group.description,
                strategy=group.strategy,
                is_active=group.is_active,
                created_at=group.created_at.isoformat() if group.created_at else None,
                updated_at=group.updated_at.isoformat() if group.updated_at else None,
                members=await _members_to_response(session, members),
            )

        group = await repo.update_group(name, **update_data)

        if not group:
            raise HTTPException(
                status_code=404,
                detail=f"Model group '{name}' not found"
            )

        # Get members
        members = await repo.get_members_by_group_name(name)

        await session.commit()

        await model_group_manager.reload()

        return ModelGroupDetailResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            strategy=group.strategy,
            is_active=group.is_active,
            created_at=group.created_at.isoformat() if group.created_at else None,
            updated_at=group.updated_at.isoformat() if group.updated_at else None,
            members=await _members_to_response(session, members),
        )


@router.delete("/{name}", status_code=204)
async def delete_model_group(
    name: str,
    admin: str = Depends(verify_admin)
):
    """
    Delete a model group permanently (DB row and members removed).
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)

        group = await repo.get_group_by_name(name)
        if not group:
            raise HTTPException(
                status_code=404,
                detail=f"Model group '{name}' not found"
            )

        await repo.delete_group(name)
        await session.commit()

    await model_group_manager.reload()

    return None


# ============================================================================
# Member Reorder
# ============================================================================

@router.put("/{name}/members/reorder", response_model=ModelGroupDetailResponse)
async def reorder_group_members(
    name: str,
    request: MemberReorderRequest,
    admin: str = Depends(verify_admin)
):
    """
    Reorder group members by updating their priorities.

    Request body:
    {
        "members": [
            {"id": 1, "priority": 0},
            {"id": 2, "priority": 1},
            {"id": 3, "priority": 2}
        ]
    }
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)

        group = await repo.get_group_by_name(name)
        if not group:
            raise HTTPException(
                status_code=404,
                detail=f"Model group '{name}' not found"
            )

        member_priorities = [
            {"id": m.id, "priority": m.priority}
            for m in request.members
        ]

        members = await repo.reorder_members(name, member_priorities)

        await session.commit()

        await model_group_manager.reload()

        return ModelGroupDetailResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            strategy=group.strategy,
            is_active=group.is_active,
            created_at=group.created_at.isoformat() if group.created_at else None,
            updated_at=group.updated_at.isoformat() if group.updated_at else None,
            members=await _members_to_response(session, members),
        )


# ============================================================================
# Member Management Endpoints
# ============================================================================

@router.post("/{name}/members", response_model=ModelGroupMemberResponse, status_code=201)
async def add_group_member(
    name: str,
    request: ModelGroupMemberRequest,
    admin: str = Depends(verify_admin)
):
    """
    Add a member to a model group.

    Request body:
    {
        "model_display_name": "glm-5:cloud",
        "capability_tags": ["vision", "code"],
        "weight": 1,
        "priority": 0,
        "is_fallback": false,
        "is_active": true
    }
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)

        # Check if group exists
        group = await repo.get_group_by_name(name)
        if not group:
            raise HTTPException(
                status_code=404,
                detail=f"Model group '{name}' not found"
            )

        # Check if member already exists in group
        members = await repo.get_members_by_group_name(name)
        existing_names = {m.model_display_name for m in members}
        if request.model_display_name in existing_names:
            raise HTTPException(
                status_code=400,
                detail=f"Member '{request.model_display_name}' already exists in group '{name}'"
            )

        member = await repo.add_member(
            group_name=name,
            model_display_name=request.model_display_name,
            capability_tags=request.capability_tags,
            weight=request.weight,
            priority=request.priority,
            is_fallback=request.is_fallback,
            is_active=request.is_active,
            preferred_node_ids=request.preferred_node_ids,
        )

        if not member:
            raise HTTPException(
                status_code=500,
                detail="Failed to add member to group"
            )

        await session.commit()

        await model_group_manager.reload()

        return await _member_to_response(session, member)


@router.delete("/{name}/members/{member_id}", status_code=204)
async def remove_group_member(
    name: str,
    member_id: int,
    admin: str = Depends(verify_admin)
):
    """
    Remove a member from a model group.

    Permanently deletes the member from the group.
    """
    async with async_session_maker() as session:
        repo = ModelGroupRepository(session)

        # Check if group exists
        group = await repo.get_group_by_name(name)
        if not group:
            raise HTTPException(
                status_code=404,
                detail=f"Model group '{name}' not found"
            )

        # Find member by id and group
        members = await repo.get_members_by_group_name(name)
        member = next((m for m in members if m.id == member_id), None)

        if not member:
            raise HTTPException(
                status_code=404,
                detail=f"Member with id {member_id} not found in group '{name}'"
            )

        # Remove member
        await repo.remove_member(name, member.model_display_name)
        await session.commit()

        await model_group_manager.reload()

        return None


# ============================================================================
# Helper Functions
# ============================================================================

async def _members_to_response(
    session: AsyncSession, members: List[ModelGroupMember]
) -> List[ModelGroupMemberResponse]:
    """Build member responses without lazy-loading preferred_nodes (async-safe)."""
    if not members:
        return []
    ids = [m.id for m in members]
    r = await session.execute(
        select(model_group_member_nodes.c.member_id, model_group_member_nodes.c.node_id).where(
            model_group_member_nodes.c.member_id.in_(ids)
        )
    )
    by_mid: dict[int, List[int]] = defaultdict(list)
    for mid, nid in r.all():
        by_mid[mid].append(nid)
    for mid in by_mid:
        by_mid[mid].sort()
    return [
        ModelGroupMemberResponse(
            id=m.id,
            model_display_name=m.model_display_name,
            capability_tags=m.capability_tags,
            weight=m.weight,
            priority=m.priority,
            is_fallback=m.is_fallback,
            is_active=m.is_active,
            preferred_node_ids=by_mid.get(m.id, []),
        )
        for m in members
    ]


async def _member_to_response(
    session: AsyncSession, member: ModelGroupMember
) -> ModelGroupMemberResponse:
    rows = await _members_to_response(session, [member])
    return rows[0]