"""Repository for ToolSet operations"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional, List

from app.models_db import ToolSet


class ToolSetRepository:
    """Repository for ToolSet CRUD operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_all(self) -> List[ToolSet]:
        """Get all tool sets"""
        stmt = select(ToolSet).order_by(ToolSet.name)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_by_id(self, tool_set_id: int) -> Optional[ToolSet]:
        """Get tool set by ID"""
        stmt = select(ToolSet).where(ToolSet.id == tool_set_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_name(self, name: str) -> Optional[ToolSet]:
        """Get tool set by name"""
        stmt = select(ToolSet).where(ToolSet.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create(self, name: str, tools: Optional[List[str]] = None, description: Optional[str] = None) -> ToolSet:
        """Create a new tool set"""
        tool_set = ToolSet(name=name, tools=tools, description=description)
        self.session.add(tool_set)
        await self.session.flush()
        return tool_set
    
    async def update(self, tool_set_id: int, **kwargs) -> Optional[ToolSet]:
        """Update an existing tool set"""
        tool_set = await self.get_by_id(tool_set_id)
        if not tool_set:
            return None
        
        for key, value in kwargs.items():
            if hasattr(tool_set, key) and key not in ('id', 'created_at'):
                setattr(tool_set, key, value)
        
        await self.session.flush()
        return tool_set
    
    async def delete_by_id(self, tool_set_id: int) -> bool:
        """Delete a tool set by ID"""
        stmt = delete(ToolSet).where(ToolSet.id == tool_set_id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0
