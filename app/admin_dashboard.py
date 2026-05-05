"""
Admin Dashboard endpoints.
Provides statistics, charts, and system health information.
"""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query

from app.auth import verify_admin
from app.models import DashboardStatsResponse, ChartDataResponse, RequestLogResponse, UserStatsResponse
from app.services.dashboard_service import dashboard_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/dashboard")


@router.get("/stats", response_model=DashboardStatsResponse, tags=["Admin - Dashboard"])
async def get_dashboard_stats(admin: str = Depends(verify_admin)):
    """
    Get dashboard statistics.
    
    Returns:
    - users: total, active_today, new_this_week
    - requests: today, this_week, this_month
    - tokens: today, this_week, this_month
    - models: most_used (top 10), total_models
    - system: redis, postgres, ollama connection status, queue_pending
    """
    stats = await dashboard_service.get_stats()
    return DashboardStatsResponse(**stats)


@router.get("/charts/requests", response_model=ChartDataResponse, tags=["Admin - Dashboard"])
async def get_requests_chart(
    period: str = "7d",
    admin: str = Depends(verify_admin)
):
    """
    Get request count chart data.
    
    Query parameters:
    - period: Time period (e.g., "7d", "14d", "30d", "2w", "1m")
    """
    chart_data = await dashboard_service.get_requests_chart(period)
    return ChartDataResponse(**chart_data)


@router.get("/charts/tokens", response_model=ChartDataResponse, tags=["Admin - Dashboard"])
async def get_tokens_chart(
    period: str = "7d",
    admin: str = Depends(verify_admin)
):
    """
    Get token usage chart data.
    
    Query parameters:
    - period: Time period (e.g., "7d", "14d", "30d", "2w", "1m")
    """
    chart_data = await dashboard_service.get_tokens_chart(period)
    return ChartDataResponse(**chart_data)


@router.get("/charts/models", response_model=ChartDataResponse, tags=["Admin - Dashboard"])
async def get_models_chart(
    period: str = "7d",
    admin: str = Depends(verify_admin)
):
    """
    Get model usage distribution chart data.
    
    Query parameters:
    - period: Time period (e.g., "7d", "14d", "30d", "2w", "1m")
    """
    chart_data = await dashboard_service.get_models_chart(period)
    return ChartDataResponse(**chart_data)


@router.get("/requests-log", response_model=RequestLogResponse, tags=["Admin - Dashboard"])
async def get_requests_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    username: Optional[str] = Query(None),
    model_name: Optional[str] = Query(None),
    status_code: Optional[int] = Query(None),
    status_category: Optional[str] = Query(None),
    request_type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    url_path: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    admin: str = Depends(verify_admin)
):
    """
    Get paginated, filterable request logs.

    Query parameters:
    - limit: Number of logs to return (default: 50, max: 200)
    - offset: Number of logs to skip (default: 0)
    - username: Filter by username (exact match)
    - model_name: Filter by model name (partial match, case-insensitive)
    - status_code: Filter by exact HTTP status code
    - status_category: Filter by status category ("success" for 2xx, "error" for 4xx/5xx/null)
    - request_type: Filter by request type (chat, generate, embeddings, etc.)
    - source: Filter by request source (Cursor, Claude, OpenClaw, Ollama Native, OpenAI-Compatible, Grafana)
    - url_path: Filter by URL path (partial match)
    - start_date: Start date in ISO format (e.g., "2024-01-01")
    - end_date: End date in ISO format (e.g., "2024-01-31")
    """
    start_dt = None
    end_dt = None

    if start_date:
        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
    if end_date:
        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))

    result = await dashboard_service.get_requests_log(
        limit=limit,
        offset=offset,
        username=username,
        model_name=model_name,
        status_code=status_code,
        status_category=status_category,
        request_type=request_type,
        source=source,
        url_path=url_path,
        start_date=start_dt,
        end_date=end_dt
    )
    return RequestLogResponse(**result)


@router.get("/user-stats", response_model=UserStatsResponse, tags=["Admin - Dashboard"])
async def get_user_stats(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    admin: str = Depends(verify_admin)
):
    """
    Get aggregated per-user statistics.

    Query parameters:
    - start_date: Start date in ISO format (e.g., "2024-01-01")
    - end_date: End date in ISO format (e.g., "2024-01-31")
    """
    start_dt = None
    end_dt = None

    if start_date:
        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
    if end_date:
        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))

    result = await dashboard_service.get_all_user_stats(
        start_date=start_dt,
        end_date=end_dt
    )
    return UserStatsResponse(**result)
