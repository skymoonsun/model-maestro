"""
Dashboard Service - Statistics, charts, and system health for admin panel.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from sqlalchemy import select, func, and_, text, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models_db import User, UserActivityLog, ModelMapping, UserLimit

logger = logging.getLogger(__name__)


class DashboardService:
    """Service for dashboard statistics and chart data"""
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get dashboard statistics"""
        async with async_session_maker() as session:
            users_stats = await self._get_users_stats(session)
            requests_stats = await self._get_requests_stats(session)
            tokens_stats = await self._get_tokens_stats(session)
            models_stats = await self._get_models_stats(session)
            system_stats = await self._get_system_status()
        
        return {
            "users": users_stats,
            "requests": requests_stats,
            "tokens": tokens_stats,
            "models": models_stats,
            "system": system_stats,
        }
    
    async def _get_users_stats(self, session: AsyncSession) -> Dict[str, Any]:
        """Get user statistics"""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        
        # Total users
        total_result = await session.execute(
            select(func.count(User.id)).where(User.is_active == True)
        )
        total = total_result.scalar() or 0
        
        # Active today (users who made at least one request today)
        active_today_result = await session.execute(
            select(func.count(func.distinct(UserActivityLog.user_id))).where(
                UserActivityLog.created_at >= today_start
            )
        )
        active_today = active_today_result.scalar() or 0
        
        # New this week
        new_week_result = await session.execute(
            select(func.count(User.id)).where(
                and_(User.created_at >= week_start, User.is_active == True)
            )
        )
        new_this_week = new_week_result.scalar() or 0
        
        return {
            "total": total,
            "active_today": active_today,
            "new_this_week": new_this_week,
        }
    
    async def _get_requests_stats(self, session: AsyncSession) -> Dict[str, Any]:
        """Get request statistics with success/error breakdown"""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        # Today
        today_result = await session.execute(
            select(
                func.count(UserActivityLog.id).label("total"),
                func.sum(case((UserActivityLog.status_code >= 200, 1), else_=0)).label("success"),
                func.sum(case((UserActivityLog.status_code >= 400, 1), else_=0)).label("errors"),
                func.avg(UserActivityLog.duration_ms).label("avg_duration")
            ).where(UserActivityLog.created_at >= today_start)
        )
        today_row = today_result.fetchone()

        # This week
        week_result = await session.execute(
            select(func.count(UserActivityLog.id)).where(
                UserActivityLog.created_at >= week_start
            )
        )
        this_week = week_result.scalar() or 0

        # This month
        month_result = await session.execute(
            select(func.count(UserActivityLog.id)).where(
                UserActivityLog.created_at >= month_start
            )
        )
        this_month = month_result.scalar() or 0

        # All-time total
        total_result = await session.execute(
            select(func.count(UserActivityLog.id))
        )
        total = total_result.scalar() or 0

        # All-time avg duration
        total_avg_result = await session.execute(
            select(func.avg(UserActivityLog.duration_ms))
        )
        total_avg_duration = round(float(total_avg_result.scalar() or 0))

        return {
            "today": today_row.total or 0,
            "today_success": int(today_row.success or 0),
            "today_errors": int(today_row.errors or 0),
            "today_avg_duration": round(float(today_row.avg_duration or 0)),
            "this_week": this_week,
            "this_month": this_month,
            "total": total,
            "total_avg_duration": total_avg_duration,
        }
    
    async def _get_tokens_stats(self, session: AsyncSession) -> Dict[str, Any]:
        """Get token usage statistics with prompt/completion breakdown"""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        # Today's token breakdown
        today_result = await session.execute(
            select(
                func.sum(UserActivityLog.prompt_tokens).label("prompt"),
                func.sum(UserActivityLog.completion_tokens).label("completion"),
                func.sum(UserActivityLog.total_tokens).label("total")
            ).where(UserActivityLog.created_at >= today_start)
        )
        today_row = today_result.fetchone()

        # This week's token breakdown
        week_result = await session.execute(
            select(
                func.sum(UserActivityLog.prompt_tokens).label("prompt"),
                func.sum(UserActivityLog.completion_tokens).label("completion"),
                func.sum(UserActivityLog.total_tokens).label("total")
            ).where(UserActivityLog.created_at >= week_start)
        )
        week_row = week_result.fetchone()

        # This month's token breakdown
        month_result = await session.execute(
            select(
                func.sum(UserActivityLog.prompt_tokens).label("prompt"),
                func.sum(UserActivityLog.completion_tokens).label("completion"),
                func.sum(UserActivityLog.total_tokens).label("total")
            ).where(UserActivityLog.created_at >= month_start)
        )
        month_row = month_result.fetchone()

        # All-time token breakdown
        all_time_result = await session.execute(
            select(
                func.sum(UserActivityLog.prompt_tokens).label("prompt"),
                func.sum(UserActivityLog.completion_tokens).label("completion"),
                func.sum(UserActivityLog.total_tokens).label("total")
            )
        )
        all_time_row = all_time_result.fetchone()

        def row_to_dict(row):
            return {
                "total": row.total or 0,
                "prompt": row.prompt or 0,
                "completion": row.completion or 0,
            }

        return {
            "today": row_to_dict(today_row),
            "this_week": row_to_dict(week_row),
            "this_month": row_to_dict(month_row),
            "all_time": row_to_dict(all_time_row),
        }
    
    async def _get_models_stats(self, session: AsyncSession) -> Dict[str, Any]:
        """Get model usage statistics"""
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Most used models (this month)
        most_used_result = await session.execute(
            select(
                UserActivityLog.model_name,
                func.count(UserActivityLog.id).label("requests"),
                func.sum(UserActivityLog.total_tokens).label("tokens")
            ).where(
                UserActivityLog.created_at >= month_start
            ).group_by(
                UserActivityLog.model_name
            ).order_by(
                func.count(UserActivityLog.id).desc()
            ).limit(10)
        )
        most_used = [
            {
                "name": row.model_name,
                "requests": row.requests,
                "tokens": row.tokens or 0,
            }
            for row in most_used_result.fetchall()
        ]
        
        # Total model count
        total_models_result = await session.execute(
            select(func.count(ModelMapping.id))
        )
        total_models = total_models_result.scalar() or 0
        
        return {
            "most_used": most_used,
            "total_models": total_models,
        }
    
    async def _get_system_status(self) -> Dict[str, Any]:
        """Get system connection status"""
        status = {}
        
        # Redis status
        try:
            from app.redis import redis_manager
            if redis_manager and redis_manager._connected:
                await redis_manager.redis_client.ping()
                status["redis"] = "connected"
            else:
                status["redis"] = "disconnected"
        except Exception:
            status["redis"] = "error"
        
        # PostgreSQL status
        try:
            async with async_session_maker() as session:
                await session.execute(text("SELECT 1"))
                status["postgres"] = "connected"
        except Exception:
            status["postgres"] = "error"
        
        # Ollama status
        try:
            import httpx
            from app.config import get_settings
            settings = get_settings()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/version")
                if resp.status_code == 200:
                    status["ollama"] = "connected"
                else:
                    status["ollama"] = "error"
        except Exception:
            status["ollama"] = "disconnected"
        
        # Queue pending count
        try:
            from app.redis import redis_manager
            if redis_manager and redis_manager._connected:
                pending = await redis_manager.redis_client.llen("activity_log_queue")
                status["queue_pending"] = pending
            else:
                status["queue_pending"] = -1
        except Exception:
            status["queue_pending"] = -1
        
        return status
    
    async def get_requests_chart(self, period: str = "7d") -> Dict[str, Any]:
        """Get request count chart data"""
        days = self._parse_period(period)
        
        async with async_session_maker() as session:
            now = datetime.utcnow()
            start_date = now - timedelta(days=days)
            
            # Query daily request counts
            result = await session.execute(
                select(
                    func.date(UserActivityLog.created_at).label("date"),
                    func.count(UserActivityLog.id).label("count")
                ).where(
                    UserActivityLog.created_at >= start_date
                ).group_by(
                    func.date(UserActivityLog.created_at)
                ).order_by(
                    func.date(UserActivityLog.created_at)
                )
            )
            rows = result.fetchall()
            
            # Build complete date range with zeros for missing dates
            labels = []
            data = []
            date_map = {str(row.date): row.count for row in rows}
            
            for i in range(days):
                date = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
                labels.append(date)
                data.append(date_map.get(date, 0))
        
        return {"labels": labels, "data": data, "period": period}
    
    async def get_tokens_chart(self, period: str = "7d") -> Dict[str, Any]:
        """Get token usage chart data"""
        days = self._parse_period(period)
        
        async with async_session_maker() as session:
            now = datetime.utcnow()
            start_date = now - timedelta(days=days)
            
            result = await session.execute(
                select(
                    func.date(UserActivityLog.created_at).label("date"),
                    func.sum(UserActivityLog.total_tokens).label("tokens")
                ).where(
                    UserActivityLog.created_at >= start_date
                ).group_by(
                    func.date(UserActivityLog.created_at)
                ).order_by(
                    func.date(UserActivityLog.created_at)
                )
            )
            rows = result.fetchall()
            
            labels = []
            data = []
            date_map = {str(row.date): row.tokens or 0 for row in rows}
            
            for i in range(days):
                date = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
                labels.append(date)
                data.append(date_map.get(date, 0))
        
        return {"labels": labels, "data": data, "period": period}
    
    async def get_models_chart(self, period: str = "7d") -> Dict[str, Any]:
        """Get model usage distribution chart data"""
        days = self._parse_period(period)
        
        async with async_session_maker() as session:
            now = datetime.utcnow()
            start_date = now - timedelta(days=days)
            
            result = await session.execute(
                select(
                    UserActivityLog.model_name,
                    func.count(UserActivityLog.id).label("requests")
                ).where(
                    UserActivityLog.created_at >= start_date
                ).group_by(
                    UserActivityLog.model_name
                ).order_by(
                    func.count(UserActivityLog.id).desc()
                ).limit(10)
            )
            rows = result.fetchall()
            
            labels = [row.model_name for row in rows]
            data = [row.requests for row in rows]
        
        return {"labels": labels, "data": data, "period": period}
    
    def _parse_period(self, period: str) -> int:
        """Parse period string to number of days"""
        period = period.lower().strip()
        if period.endswith("d"):
            return int(period[:-1])
        elif period.endswith("w"):
            return int(period[:-1]) * 7
        elif period.endswith("m"):
            return int(period[:-1]) * 30
        return 7  # default 7 days

    async def get_requests_log(
        self,
        limit: int = 50,
        offset: int = 0,
        username: Optional[str] = None,
        model_name: Optional[str] = None,
        status_code: Optional[int] = None,
        status_category: Optional[str] = None,
        request_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get paginated, filterable global request logs"""
        from app.repositories import UserRepository, UserActivityRepository

        user_id: Optional[int] = None
        if username:
            async with async_session_maker() as session:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_username(username)
                if user:
                    user_id = user.id
                else:
                    # Username specified but not found — return empty result
                    return {"logs": [], "total": 0, "limit": limit, "offset": offset}

        async with async_session_maker() as session:
            activity_repo = UserActivityRepository(session)
            logs, total = await activity_repo.get_requests_log(
                limit=limit,
                offset=offset,
                user_id=user_id,
                model_name=model_name,
                status_code=status_code,
                status_category=status_category,
                request_type=request_type,
                start_date=start_date,
                end_date=end_date
            )

            log_items = []
            for log in logs:
                log_items.append({
                    "id": log.id,
                    "username": getattr(log, '_username', None),
                    "model_name": log.model_name,
                    "request_type": log.request_type,
                    "prompt_tokens": log.prompt_tokens,
                    "completion_tokens": log.completion_tokens,
                    "total_tokens": log.total_tokens,
                    "status_code": log.status_code,
                    "duration_ms": log.duration_ms,
                    "error_message": log.error_message,
                    "created_at": log.created_at.isoformat() if log.created_at else None
                })

            return {
                "logs": log_items,
                "total": total,
                "limit": limit,
                "offset": offset
            }

    async def get_all_user_stats(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get aggregated per-user statistics"""
        from app.repositories import UserActivityRepository

        async with async_session_maker() as session:
            activity_repo = UserActivityRepository(session)
            stats = await activity_repo.get_all_user_stats(
                start_date=start_date,
                end_date=end_date
            )

            return {
                "users": stats,
                "total_users": len(stats)
            }


# Global dashboard service instance
dashboard_service = DashboardService()
