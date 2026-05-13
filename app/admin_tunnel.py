"""Admin tunnel endpoints for exposing local API publicly"""

from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import verify_admin
from app.tunnel_manager import tunnel_manager
from app.services import config_manager
from app.database import async_session_maker
from app.repositories.system_config_repository import SystemConfigRepository

router = APIRouter(prefix="/admin")


class TunnelConfig(BaseModel):
    provider: str = ""  # "cloudflare", "ngrok", or ""
    api_token: str = ""
    public_url: str = ""


class TunnelStatusResponse(BaseModel):
    running: bool = False
    provider: str = ""
    public_url: str = ""
    pid: int | None = None
    error: str = ""


@router.get("/tunnel/status", response_model=TunnelStatusResponse, tags=["Admin - Tunnel"])
async def get_tunnel_status(admin: str = Depends(verify_admin)):
    """Get current tunnel status"""
    return TunnelStatusResponse(**await tunnel_manager.get_status())


@router.post("/tunnel/start", response_model=TunnelStatusResponse, tags=["Admin - Tunnel"])
async def start_tunnel(admin: str = Depends(verify_admin)):
    """Start tunnel with configured provider"""
    await config_manager.ensure_loaded()
    provider = config_manager.get("tunnel.provider", "")
    if not provider:
        raise HTTPException(status_code=400, detail="Tunnel provider not configured")

    api_token = config_manager.get("tunnel.api_token", "")
    local_port = int(config_manager.get("defaults.local_port", "8000"))

    result = await tunnel_manager.start(provider=provider, local_port=local_port, api_token=api_token)
    return TunnelStatusResponse(**result)


@router.post("/tunnel/stop", response_model=TunnelStatusResponse, tags=["Admin - Tunnel"])
async def stop_tunnel(admin: str = Depends(verify_admin)):
    """Stop running tunnel"""
    result = await tunnel_manager.stop()
    return TunnelStatusResponse(**result)


@router.get("/tunnel/config", response_model=TunnelConfig, tags=["Admin - Tunnel"])
async def get_tunnel_config(admin: str = Depends(verify_admin)):
    """Get tunnel configuration from system config"""
    await config_manager.ensure_loaded()
    return TunnelConfig(
        provider=config_manager.get("tunnel.provider", ""),
        api_token=config_manager.get("tunnel.api_token", ""),
        public_url=config_manager.get("tunnel.public_url", ""),
    )


@router.put("/tunnel/config", response_model=TunnelConfig, tags=["Admin - Tunnel"])
async def update_tunnel_config(config: TunnelConfig, admin: str = Depends(verify_admin)):
    """Update tunnel configuration"""
    async with async_session_maker() as session:
        repo = SystemConfigRepository(session)
        await repo.upsert("tunnel.provider", config.provider, "Tunnel provider (cloudflare, ngrok)")
        await repo.upsert("tunnel.api_token", config.api_token, "Tunnel API token")
        await repo.upsert("tunnel.public_url", config.public_url, "Tunnel public URL")
        await session.commit()

    # Refresh cache
    await config_manager.load_all()

    return config
