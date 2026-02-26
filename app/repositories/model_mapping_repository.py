"""Model mapping repository for database operations"""

from typing import Optional, List, Dict, Tuple
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models_db import ModelMapping


class ModelMappingRepository:
    """Repository for ModelMapping operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, display_name: str, real_name: str, context_length: Optional[int] = None) -> ModelMapping:
        """Create a new model mapping"""
        mapping = ModelMapping(
            display_name=display_name,
            real_name=real_name,
            context_length=context_length
        )
        self.session.add(mapping)
        await self.session.commit()
        await self.session.refresh(mapping)
        return mapping
    
    async def update(self, display_name: str, real_name: Optional[str] = None, context_length: Optional[int] = None) -> Optional[ModelMapping]:
        """Update an existing model mapping"""
        mapping = await self.get_by_display_name(display_name)
        if not mapping:
            return None
        
        if real_name is not None:
            mapping.real_name = real_name
        if context_length is not None:
            mapping.context_length = context_length
        
        await self.session.commit()
        await self.session.refresh(mapping)
        return mapping
    
    async def upsert(self, display_name: str, real_name: str, context_length: Optional[int] = None) -> Tuple[ModelMapping, bool]:
        """Create or update a model mapping. Returns (mapping, is_new)"""
        existing = await self.get_by_display_name(display_name)
        if existing:
            existing.real_name = real_name
            if context_length is not None:
                existing.context_length = context_length
            await self.session.commit()
            await self.session.refresh(existing)
            return existing, False
        else:
            mapping = await self.create(display_name, real_name, context_length)
            return mapping, True
    
    async def get_by_display_name(self, display_name: str) -> Optional[ModelMapping]:
        """Get mapping by display name"""
        result = await self.session.execute(
            select(ModelMapping).where(ModelMapping.display_name == display_name)
        )
        return result.scalar_one_or_none()
    
    async def get_by_real_name(self, real_name: str) -> Optional[ModelMapping]:
        """Get mapping by real name"""
        result = await self.session.execute(
            select(ModelMapping).where(ModelMapping.real_name == real_name)
        )
        return result.scalar_one_or_none()
    
    async def list_all(self) -> List[ModelMapping]:
        """List all mappings"""
        result = await self.session.execute(select(ModelMapping))
        return list(result.scalars().all())
    
    async def get_all_as_dict(self) -> Dict[str, str]:
        """Get all mappings as dict {display_name: real_name}"""
        mappings = await self.list_all()
        return {m.display_name: m.real_name for m in mappings}
    
    async def get_reverse_dict(self) -> Dict[str, List[str]]:
        """Get all mappings as reverse dict {real_name: [display_name1, display_name2, ...]}"""
        mappings = await self.list_all()
        reverse_dict = {}
        for m in mappings:
            if m.real_name not in reverse_dict:
                reverse_dict[m.real_name] = []
            reverse_dict[m.real_name].append(m.display_name)
        return reverse_dict
    
    async def get_context_lengths(self) -> Dict[str, int]:
        """Get all context lengths as dict {display_name: context_length}"""
        mappings = await self.list_all()
        return {m.display_name: m.context_length for m in mappings if m.context_length}
    
    async def delete_by_display_name(self, display_name: str) -> bool:
        """Delete mapping by display name"""
        result = await self.session.execute(
            delete(ModelMapping).where(ModelMapping.display_name == display_name)
        )
        await self.session.commit()
        return result.rowcount > 0
    
    async def delete(self, display_name: str) -> bool:
        """Alias for delete_by_display_name"""
        return await self.delete_by_display_name(display_name)
    
    async def exists(self, display_name: str) -> bool:
        """Check if mapping exists"""
        result = await self.session.execute(
            select(ModelMapping.id).where(ModelMapping.display_name == display_name)
        )
        return result.scalar_one_or_none() is not None
