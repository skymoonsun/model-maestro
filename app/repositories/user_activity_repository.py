"""Repository for UserActivityLog operations"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, text
from typing import List, Optional
from datetime import datetime, timedelta

from app.models_db import UserActivityLog


class UserActivityRepository:
    """Repository for UserActivityLog operations"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def log_activity(
        self,
        user_id: int,
        model_name: str,
        request_type: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0
    ) -> UserActivityLog:
        """Log user activity"""
        activity_log = UserActivityLog(
            user_id=user_id,
            model_name=model_name,
            request_type=request_type,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens)
        )
        
        self.session.add(activity_log)
        await self.session.flush()
        return activity_log
    
    async def get_user_activity(
        self,
        user_id: int,
        limit: int = 100,
        offset: int = 0
    ) -> List[UserActivityLog]:
        """Get user activity logs"""
        stmt = (
            select(UserActivityLog)
            .where(UserActivityLog.user_id == user_id)
            .order_by(UserActivityLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_user_token_usage(
        self,
        user_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> dict:
        """Get user token usage statistics"""
        stmt = select(
            func.sum(UserActivityLog.prompt_tokens).label("total_prompt_tokens"),
            func.sum(UserActivityLog.completion_tokens).label("total_completion_tokens"),
            func.sum(UserActivityLog.total_tokens).label("total_tokens"),
            func.count(UserActivityLog.id).label("total_requests")
        ).where(UserActivityLog.user_id == user_id)
        
        # Add date filters if provided
        if start_date:
            stmt = stmt.where(UserActivityLog.created_at >= start_date)
        if end_date:
            stmt = stmt.where(UserActivityLog.created_at <= end_date)
        
        result = await self.session.execute(stmt)
        row = result.fetchone()
        
        return {
            "prompt_tokens": row.total_prompt_tokens or 0,
            "completion_tokens": row.total_completion_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "total_requests": row.total_requests or 0
        }
    
    async def get_user_model_usage(
        self,
        user_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[dict]:
        """Get user model usage statistics"""
        stmt = (
            select(
                UserActivityLog.model_name,
                func.count(UserActivityLog.id).label("request_count"),
                func.sum(UserActivityLog.total_tokens).label("total_tokens")
            )
            .where(UserActivityLog.user_id == user_id)
            .group_by(UserActivityLog.model_name)
            .order_by(func.sum(UserActivityLog.total_tokens).desc())
        )
        
        # Add date filters if provided
        if start_date:
            stmt = stmt.where(UserActivityLog.created_at >= start_date)
        if end_date:
            stmt = stmt.where(UserActivityLog.created_at <= end_date)
        
        result = await self.session.execute(stmt)
        rows = result.fetchall()
        
        return [
            {
                "model_name": row.model_name,
                "request_count": row.request_count,
                "total_tokens": row.total_tokens or 0
            }
            for row in rows
        ]
