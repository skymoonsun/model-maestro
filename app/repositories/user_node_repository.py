"""User-Node relationship repository for node-level access control"""

from typing import List, Optional
from sqlalchemy import select, delete, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models_db import UserNode, OllamaNode


class UserNodeRepository:
    """Repository for UserNode operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def assign_node(self, user_id: int, node_id: int) -> UserNode:
        """Assign a specific node to user"""
        user_node = UserNode(user_id=user_id, node_id=node_id)
        self.session.add(user_node)
        await self.session.commit()
        await self.session.refresh(user_node)
        return user_node

    async def get_user_nodes(self, user_id: int) -> List[int]:
        """Get list of node IDs user can access. Empty list means no restrictions."""
        result = await self.session.execute(
            select(UserNode.node_id).where(UserNode.user_id == user_id)
        )
        return [row[0] for row in result.all()]

    async def has_node_access(self, user_id: int, node_id: int) -> bool:
        """Check if user has access to specific node. If no entries exist, access is allowed."""
        # Check if user has any node restrictions
        result = await self.session.execute(
            select(UserNode).where(UserNode.user_id == user_id)
        )
        entries = result.scalars().all()

        # If no entries, access is unrestricted
        if not entries:
            return True

        # Check if specific node is allowed
        result = await self.session.execute(
            select(UserNode).where(
                and_(UserNode.user_id == user_id, UserNode.node_id == node_id)
            )
        )
        return result.scalar_one_or_none() is not None

    async def revoke_node(self, user_id: int, node_id: int) -> bool:
        """Revoke access to specific node"""
        result = await self.session.execute(
            delete(UserNode).where(
                and_(UserNode.user_id == user_id, UserNode.node_id == node_id)
            )
        )
        await self.session.commit()
        return result.rowcount > 0

    async def revoke_all_nodes(self, user_id: int) -> bool:
        """Revoke all node access"""
        result = await self.session.execute(
            delete(UserNode).where(UserNode.user_id == user_id)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def get_user_nodes_with_details(self, user_id: int) -> List[dict]:
        """Get list of nodes user can access with node details"""
        result = await self.session.execute(
            select(UserNode, OllamaNode)
            .join(OllamaNode, UserNode.node_id == OllamaNode.id)
            .where(UserNode.user_id == user_id)
        )
        rows = result.all()
        return [
            {
                "node_id": node.id,
                "node_name": node.name,
                "node_type": node.node_type,
                "base_url": node.base_url,
                "created_at": user_node.created_at.isoformat() if user_node.created_at else None,
            }
            for user_node, node in rows
        ]

    async def has_any_node_restriction(self, user_id: int) -> bool:
        """Check if user has any node restrictions at all"""
        result = await self.session.execute(
            select(UserNode).where(UserNode.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None
