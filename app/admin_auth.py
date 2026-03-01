"""
Admin login endpoint - no auth required.
Validates username/password against env, returns admin token on success.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(prefix="/admin/auth", tags=["Admin - Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str


@router.post("/login", response_model=LoginResponse)
async def admin_login(request: LoginRequest):
    """
    Admin login - validates credentials against ADMIN_USERNAME and ADMIN_PASSWORD.
    Returns admin token on success. Token is used for subsequent API calls.
    """
    settings = get_settings()
    if request.username != settings.admin_username or request.password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return LoginResponse(token=settings.admin_token)
