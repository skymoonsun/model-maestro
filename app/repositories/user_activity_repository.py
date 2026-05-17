"""Repository for UserActivityLog operations"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, text, insert
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta

from app.models_db import UserActivityLog, User


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
        total_tokens: int = 0,
        status_code: Optional[int] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        source: Optional[str] = None,
        url_path: Optional[str] = None
    ) -> UserActivityLog:
        """Log user activity"""
        activity_log = UserActivityLog(
            user_id=user_id,
            model_name=model_name,
            request_type=request_type,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
            status_code=status_code,
            duration_ms=duration_ms,
            error_message=error_message,
            source=source,
            url_path=url_path
        )
        
        self.session.add(activity_log)
        await self.session.flush()
        return activity_log

    async def bulk_insert_logs(self, logs: List[Dict[str, Any]]) -> int:
        """Bulk insert activity logs for better performance.

        Args:
            logs: List of dicts with keys matching UserActivityLog columns
                  (user_id, model_name, request_type, prompt_tokens,
                   completion_tokens, total_tokens, status_code,
                   duration_ms, error_message)

        Returns:
            Number of rows inserted
        """
        if not logs:
            return 0

        # Compute total_tokens if not provided
        for log in logs:
            if log.get("total_tokens") is None:
                log["total_tokens"] = (log.get("prompt_tokens", 0) or 0) + (log.get("completion_tokens", 0) or 0)

        stmt = insert(UserActivityLog).values(logs)
        result = await self.session.execute(stmt)
        return result.rowcount

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

    async def get_requests_log(
        self,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[int] = None,
        model_name: Optional[str] = None,
        status_code: Optional[int] = None,
        status_category: Optional[str] = None,
        request_type: Optional[str] = None,
        source: Optional[str] = None,
        url_path: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Tuple[List[UserActivityLog], int]:
        """Get paginated, filterable global request logs"""
        # Build base query with filters
        conditions = []
        if user_id is not None:
            conditions.append(UserActivityLog.user_id == user_id)
        if model_name is not None:
            conditions.append(UserActivityLog.model_name.ilike(f"%{model_name}%"))
        if status_code is not None:
            conditions.append(UserActivityLog.status_code == status_code)
        if status_category is not None:
            if status_category == "success":
                conditions.append(UserActivityLog.status_code >= 200)
                conditions.append(UserActivityLog.status_code < 300)
            elif status_category == "error":
                conditions.append(
                    (UserActivityLog.status_code >= 400) | (UserActivityLog.status_code == None)
                )
            elif status_category == "4xx":
                conditions.append(UserActivityLog.status_code >= 400)
                conditions.append(UserActivityLog.status_code < 500)
            elif status_category == "5xx":
                conditions.append(UserActivityLog.status_code >= 500)
        if request_type is not None:
            conditions.append(UserActivityLog.request_type == request_type)
        if source is not None:
            conditions.append(UserActivityLog.source == source)
        if url_path is not None:
            conditions.append(UserActivityLog.url_path.ilike(f"%{url_path}%"))
        if start_date is not None:
            conditions.append(UserActivityLog.created_at >= start_date)
        if end_date is not None:
            conditions.append(UserActivityLog.created_at <= end_date)

        where_clause = and_(*conditions) if conditions else True

        # Count total
        count_stmt = select(func.count(UserActivityLog.id)).where(where_clause)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Fetch paginated results with user join for username
        stmt = (
            select(UserActivityLog, User.username)
            .join(User, UserActivityLog.user_id == User.id, isouter=True)
            .where(where_clause)
            .order_by(UserActivityLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await self.session.execute(stmt)
        rows = result.fetchall()

        # Attach username to each log object for convenience
        logs = []
        for row in rows:
            log = row[0]
            log._username = row[1]
            logs.append(log)

        return logs, total

    async def get_all_user_stats(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[dict]:
        """Get aggregated per-user statistics"""
        conditions = []
        if start_date is not None:
            conditions.append(UserActivityLog.created_at >= start_date)
        if end_date is not None:
            conditions.append(UserActivityLog.created_at <= end_date)

        where_clause = and_(*conditions) if conditions else True

        stmt = (
            select(
                User.username,
                func.count(UserActivityLog.id).label("total_requests"),
                func.sum(UserActivityLog.prompt_tokens).label("total_prompt_tokens"),
                func.sum(UserActivityLog.completion_tokens).label("total_completion_tokens"),
                func.sum(UserActivityLog.total_tokens).label("total_tokens")
            )
            .join(User, UserActivityLog.user_id == User.id)
            .where(where_clause)
            .group_by(User.id, User.username)
            .order_by(func.sum(UserActivityLog.total_tokens).desc())
        )

        result = await self.session.execute(stmt)
        rows = result.fetchall()

        return [
            {
                "username": row.username,
                "total_requests": row.total_requests or 0,
                "total_prompt_tokens": row.total_prompt_tokens or 0,
                "total_completion_tokens": row.total_completion_tokens or 0,
                "total_tokens": row.total_tokens or 0
            }
            for row in rows
        ]
