"""Repository for SystemConfig operations"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional, Dict, List

from app.models_db import SystemConfig


class SystemConfigRepository:
    """Repository for SystemConfig CRUD operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_all(self) -> List[SystemConfig]:
        """Get all config entries"""
        stmt = select(SystemConfig).order_by(SystemConfig.key)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_by_key(self, key: str) -> Optional[SystemConfig]:
        """Get a config entry by key"""
        stmt = select(SystemConfig).where(SystemConfig.key == key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_prefix(self, prefix: str) -> List[SystemConfig]:
        """Get all config entries with keys starting with prefix"""
        stmt = select(SystemConfig).where(
            SystemConfig.key.like(f"{prefix}%")
        ).order_by(SystemConfig.key)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def upsert(self, key: str, value: str, description: Optional[str] = None) -> SystemConfig:
        """Create or update a config entry"""
        existing = await self.get_by_key(key)
        
        if existing:
            existing.value = value
            if description is not None:
                existing.description = description
            await self.session.flush()
            return existing
        
        config = SystemConfig(key=key, value=value, description=description)
        self.session.add(config)
        await self.session.flush()
        return config
    
    async def delete_by_key(self, key: str) -> bool:
        """Delete a config entry"""
        stmt = delete(SystemConfig).where(SystemConfig.key == key)
        result = await self.session.execute(stmt)
        return result.rowcount > 0
    
    async def bulk_upsert(self, configs: Dict[str, str]) -> List[SystemConfig]:
        """Create or update multiple config entries"""
        results = []
        for key, value in configs.items():
            config = await self.upsert(key, value)
            results.append(config)
        return results
