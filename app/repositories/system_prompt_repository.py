"""System prompt repository — data access for admin-defined system prompts."""

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_db import SystemPrompt

# Allowed scope types for validation (kept here as the single source of truth).
VALID_SCOPE_TYPES = ("model", "mapping", "node", "group")


class SystemPromptRepository:
    """Repository for SystemPrompt CRUD operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> List[SystemPrompt]:
        """Return all prompts ordered for admin display (scope_type, priority desc)."""
        result = await self.session.execute(
            select(SystemPrompt).order_by(
                SystemPrompt.scope_type,
                SystemPrompt.priority.desc(),
                SystemPrompt.scope_value,
            )
        )
        return list(result.scalars().all())

    async def get_all_active(self) -> List[SystemPrompt]:
        """Return active prompts (used to build the injection cache)."""
        result = await self.session.execute(
            select(SystemPrompt).where(SystemPrompt.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def get_by_id(self, prompt_id: int) -> Optional[SystemPrompt]:
        result = await self.session.execute(
            select(SystemPrompt).where(SystemPrompt.id == prompt_id)
        )
        return result.scalar_one_or_none()

    async def get_by_scope(self, scope_type: str, scope_value: str) -> Optional[SystemPrompt]:
        result = await self.session.execute(
            select(SystemPrompt).where(
                SystemPrompt.scope_type == scope_type,
                SystemPrompt.scope_value == scope_value,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        scope_type: str,
        scope_value: str,
        prompt: str,
        priority: int = 0,
        is_active: bool = True,
        description: Optional[str] = None,
    ) -> SystemPrompt:
        row = SystemPrompt(
            scope_type=scope_type,
            scope_value=scope_value,
            prompt=prompt,
            priority=priority,
            is_active=is_active,
            description=description,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def update(self, row: SystemPrompt, **fields) -> SystemPrompt:
        """Apply provided (non-None) fields and persist."""
        for key, value in fields.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, row: SystemPrompt) -> None:
        await self.session.delete(row)
        await self.session.commit()
