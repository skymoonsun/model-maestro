"""
Admin Dashboard endpoints.
Provides statistics, charts, and system health information.
"""

import logging
from fastapi import APIRouter, Depends

from app.auth import verify_admin
from app.models import DashboardStatsResponse, ChartDataResponse
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
