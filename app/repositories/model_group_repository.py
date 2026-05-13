"""Repository for ModelGroup and ModelGroupMember operations"""

from typing import Optional, List, Tuple
from sqlalchemy import select, delete, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models_db import (
    ModelGroup,
    ModelGroupMember,
    OllamaNode,
    model_group_member_nodes,
)


class ModelGroupRepository:
    """Repository for ModelGroup CRUD operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_group(
        self,
        name: str,
        description: Optional[str] = None,
        strategy: str = "round_robin",
        is_active: bool = True,
    ) -> ModelGroup:
        """Create a new model group"""
        group = ModelGroup(
            name=name,
            description=description,
            strategy=strategy,
            is_active=is_active,
        )
        self.session.add(group)
        await self.session.flush()
        return group

    async def get_group_by_name(self, name: str) -> Optional[ModelGroup]:
        """Get group by name"""
        stmt = select(ModelGroup).where(ModelGroup.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_groups(self, active_only: bool = False) -> List[ModelGroup]:
        """Get all groups"""
        stmt = select(ModelGroup)
        if active_only:
            stmt = stmt.where(ModelGroup.is_active == True)
        stmt = stmt.order_by(ModelGroup.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_group(
        self, name: str, **kwargs
    ) -> Optional[ModelGroup]:
        """Update an existing group"""
        group = await self.get_group_by_name(name)
        if not group:
            return None

        for key, value in kwargs.items():
            if hasattr(group, key) and key not in ("id", "created_at"):
                setattr(group, key, value)

        await self.session.flush()
        return group

    async def delete_group(self, name: str) -> bool:
        """Delete a group by name"""
        stmt = delete(ModelGroup).where(ModelGroup.name == name)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_group_with_members(self, name: str) -> Optional[Tuple[ModelGroup, List[ModelGroupMember]]]:
        """Get group with all its members ordered by priority"""
        stmt = (
            select(ModelGroup)
            .options(
                selectinload(ModelGroup.members).selectinload(ModelGroupMember.preferred_nodes),
            )
            .where(ModelGroup.name == name)
        )
        result = await self.session.execute(stmt)
        group = result.scalar_one_or_none()
        if not group:
            return None
        members = sorted(group.members, key=lambda m: m.priority)
        return (group, members)

    async def get_vision_capable_member(self, group_name: str) -> Optional[ModelGroupMember]:
        """Get the first vision-capable member from a group"""
        stmt = (
            select(ModelGroupMember)
            .join(ModelGroup)
            .options(selectinload(ModelGroupMember.preferred_nodes))
            .where(ModelGroup.name == group_name)
            .where(ModelGroupMember.is_active == True)
            .where(ModelGroupMember.capability_tags.contains(["vision"]))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_fallback_member(
        self, group_name: str, exclude_model: Optional[str] = None
    ) -> Optional[ModelGroupMember]:
        """Get a fallback member from a group, optionally excluding a specific model"""
        stmt = (
            select(ModelGroupMember)
            .join(ModelGroup)
            .options(selectinload(ModelGroupMember.preferred_nodes))
            .where(ModelGroup.name == group_name)
            .where(ModelGroupMember.is_active == True)
            .where(ModelGroupMember.is_fallback == True)
        )
        if exclude_model:
            stmt = stmt.where(ModelGroupMember.model_display_name != exclude_model)

        stmt = stmt.order_by(ModelGroupMember.priority)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _sync_preferred_nodes(self, member: ModelGroupMember, node_ids: Optional[List[int]]) -> None:
        """Persist preferred nodes via junction table (avoids lazy-load on secondary in async)."""
        await self.session.execute(
            delete(model_group_member_nodes).where(
                model_group_member_nodes.c.member_id == member.id
            )
        )
        if not node_ids:
            return
        uniq = sorted({int(x) for x in node_ids})
        result = await self.session.execute(select(OllamaNode).where(OllamaNode.id.in_(uniq)))
        nodes = list(result.scalars().all())
        found = {n.id for n in nodes}
        missing = set(uniq) - found
        if missing:
            raise ValueError(f"Unknown node id(s): {sorted(missing)}")
        await self.session.execute(
            insert(model_group_member_nodes),
            [{"member_id": member.id, "node_id": nid} for nid in uniq],
        )

    async def add_member(
        self,
        group_name: str,
        model_display_name: str,
        capability_tags: Optional[List[str]] = None,
        weight: int = 1,
        priority: int = 0,
        is_fallback: bool = False,
        is_active: bool = True,
        preferred_node_ids: Optional[List[int]] = None,
    ) -> Optional[ModelGroupMember]:
        """Add a member to a group"""
        group = await self.get_group_by_name(group_name)
        if not group:
            return None

        member = ModelGroupMember(
            group_id=group.id,
            model_display_name=model_display_name,
            capability_tags=capability_tags,
            weight=weight,
            priority=priority,
            is_fallback=is_fallback,
            is_active=is_active,
        )
        self.session.add(member)
        await self.session.flush()
        await self._sync_preferred_nodes(member, preferred_node_ids)
        return member

    async def remove_member(self, group_name: str, model_display_name: str) -> bool:
        """Remove a member from a group"""
        stmt = (
            delete(ModelGroupMember)
            .where(ModelGroupMember.model_display_name == model_display_name)
            .where(
                ModelGroupMember.group_id == select(ModelGroup.id)
                .where(ModelGroup.name == group_name)
                .scalar_subquery()
            )
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_members_by_group_name(self, group_name: str) -> List[ModelGroupMember]:
        """Get all members of a group"""
        stmt = (
            select(ModelGroupMember)
            .options(selectinload(ModelGroupMember.preferred_nodes))
            .join(ModelGroup)
            .where(ModelGroup.name == group_name)
            .order_by(ModelGroupMember.priority)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def group_exists(self, name: str) -> bool:
        """Check if a group exists"""
        stmt = select(ModelGroup.id).where(ModelGroup.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def reorder_members(self, group_name: str, member_priorities: list) -> List[ModelGroupMember]:
        """
        Update priority of multiple members at once.

        Args:
            group_name: Name of the group
            member_priorities: List of dicts with 'id' and 'priority' keys

        Returns:
            Updated list of members ordered by priority
        """
        group = await self.get_group_by_name(group_name)
        if not group:
            return []

        members = await self.get_members_by_group_name(group_name)
        member_map = {m.id: m for m in members}

        for item in member_priorities:
            member_id = item.get("id")
            priority = item.get("priority")
            if member_id in member_map:
                member_map[member_id].priority = priority

        await self.session.flush()

        return await self.get_members_by_group_name(group_name)
