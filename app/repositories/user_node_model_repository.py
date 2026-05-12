"""User-Node-Model relationship repository for fine-grained access control"""

from typing import List, Optional
from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models_db import UserNodeModel, OllamaNode


class UserNodeModelRepository:
    """Repository for UserNodeModel operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def assign_node_model(self, user_id: int, node_id: int, model_name: str) -> UserNodeModel:
        """Assign a specific node+model combination to user"""
        user_node_model = UserNodeModel(
            user_id=user_id,
            node_id=node_id,
            model_name=model_name
        )
        self.session.add(user_node_model)
        await self.session.commit()
        await self.session.refresh(user_node_model)
        return user_node_model

    async def get_user_node_models(self, user_id: int) -> List[tuple]:
        """Get list of (node_id, model_name) tuples user can access."""
        result = await self.session.execute(
            select(UserNodeModel.node_id, UserNodeModel.model_name)
            .where(UserNodeModel.user_id == user_id)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def has_node_model_access(self, user_id: int, node_id: int, model_name: str) -> bool:
        """Check if user has access to specific node+model.
        If no entries exist at all, access is allowed.
        """
        # Check if user has any node-model restrictions
        result = await self.session.execute(
            select(UserNodeModel).where(UserNodeModel.user_id == user_id)
        )
        entries = result.scalars().all()

        # If no entries, access is unrestricted
        if not entries:
            return True

        # Check if specific node+model is allowed
        result = await self.session.execute(
            select(UserNodeModel).where(
                and_(
                    UserNodeModel.user_id == user_id,
                    UserNodeModel.node_id == node_id,
                    UserNodeModel.model_name == model_name
                )
            )
        )
        return result.scalar_one_or_none() is not None

    async def revoke_node_model(self, user_id: int, node_id: int, model_name: str) -> bool:
        """Revoke access to specific node+model"""
        result = await self.session.execute(
            delete(UserNodeModel).where(
                and_(
                    UserNodeModel.user_id == user_id,
                    UserNodeModel.node_id == node_id,
                    UserNodeModel.model_name == model_name
                )
            )
        )
        await self.session.commit()
        return result.rowcount > 0

    async def revoke_all_node_models(self, user_id: int) -> bool:
        """Revoke all node-model access"""
        result = await self.session.execute(
            delete(UserNodeModel).where(UserNodeModel.user_id == user_id)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def get_user_node_models_with_details(self, user_id: int) -> List[dict]:
        """Get list of node-models user can access with node details"""
        result = await self.session.execute(
            select(UserNodeModel, OllamaNode)
            .join(OllamaNode, UserNodeModel.node_id == OllamaNode.id)
            .where(UserNodeModel.user_id == user_id)
        )
        rows = result.all()
        return [
            {
                "node_id": node.id,
                "node_name": node.name,
                "node_type": node.node_type,
                "model_name": unm.model_name,
                "created_at": unm.created_at.isoformat() if unm.created_at else None,
            }
            for unm, node in rows
        ]

    async def has_any_node_model_restriction(self, user_id: int) -> bool:
        """Check if user has any node-model restrictions at all"""
        result = await self.session.execute(
            select(UserNodeModel).where(UserNodeModel.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None
