"""
Admin endpoints for model group management.
Requires admin token for authentication.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.models import (
    ModelGroupCreateRequest,
    ModelGroupUpdateRequest,
    ModelGroupMemberRequest,
    ModelGroupResponse,
    ModelGroupDetailResponse,
    ModelGroupMemberResponse,
    ModelGroupListResponse,
)
from app.auth import verify_admin
from app.repositories.model_group_repository import ModelGroupRepository
from app.database import async_session_maker

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
        members = []
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
                )
                if member:
                    members.append(_member_to_response(member))

        await session.commit()

        return ModelGroupDetailResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            strategy=group.strategy,
            is_active=group.is_active,
            created_at=group.created_at.isoformat() if group.created_at else None,
            updated_at=group.updated_at.isoformat() if group.updated_at else None,
            members=members,
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
            members=[_member_to_response(m) for m in members],
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
                members=[_member_to_response(m) for m in members],
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

        return ModelGroupDetailResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            strategy=group.strategy,
            is_active=group.is_active,
            created_at=group.created_at.isoformat() if group.created_at else None,
            updated_at=group.updated_at.isoformat() if group.updated_at else None,
            members=[_member_to_response(m) for m in members],
        )


@router.delete("/{name}", status_code=204)
async def delete_model_group(
    name: str,
    admin: str = Depends(verify_admin)
):
    """
    Delete a model group (soft delete: is_active=False).

    The group is marked as inactive instead of being permanently deleted.
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

        # Soft delete: set is_active=False
        await repo.update_group(name, is_active=False)
        await session.commit()

        return None


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
        )

        if not member:
            raise HTTPException(
                status_code=500,
                detail="Failed to add member to group"
            )

        await session.commit()

        return _member_to_response(member)


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

        return None


# ============================================================================
# Helper Functions
# ============================================================================

def _member_to_response(member) -> ModelGroupMemberResponse:
    """Convert ModelGroupMember to response model"""
    return ModelGroupMemberResponse(
        id=member.id,
        model_display_name=member.model_display_name,
        capability_tags=member.capability_tags,
        weight=member.weight,
        priority=member.priority,
        is_fallback=member.is_fallback,
        is_active=member.is_active,
    )