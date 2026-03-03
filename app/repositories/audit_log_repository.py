"""Repository for AuditLog operations"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.models_db import AuditLog


class AuditLogRepository:
    """Repository for AuditLog operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(
        self,
        action: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        admin_ip: Optional[str] = None
    ) -> AuditLog:
        """Create a new audit log entry"""
        log = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            admin_ip=admin_ip
        )
        self.session.add(log)
        await self.session.flush()
        return log
    
    async def get_recent(self, limit: int = 50, offset: int = 0) -> List[AuditLog]:
        """Get recent audit logs"""
        stmt = (
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_by_action(self, action: str, limit: int = 50) -> List[AuditLog]:
        """Get audit logs by action type"""
        stmt = (
            select(AuditLog)
            .where(AuditLog.action == action)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_by_entity(self, entity_type: str, entity_id: Optional[str] = None, limit: int = 50) -> List[AuditLog]:
        """Get audit logs by entity type/id"""
        stmt = select(AuditLog).where(AuditLog.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def count(self) -> int:
        """Get total audit log count"""
        stmt = select(func.count(AuditLog.id))
        result = await self.session.execute(stmt)
        return result.scalar() or 0
