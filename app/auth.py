"""JWT authentication middleware with admin and model access control"""

from typing import Optional
from fastapi import Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends

from app.user_manager import user_manager
from app.config import get_settings


security = HTTPBearer()
settings = get_settings()


async def get_current_user(
    authorization: Optional[str] = Header(None)
) -> str:
    """
    Validate JWT token and return current username
    
    Args:
        authorization: Authorization header (Bearer token)
    
    Returns:
        Username if token is valid
    
    Raises:
        HTTPException: 401 if token is invalid or missing
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = parts[1]
    
    # Verify token
    username = await user_manager.verify_token(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return username


async def verify_admin(
    authorization: Optional[str] = Header(None)
) -> str:
    """
    Verify admin token
    
    Args:
        authorization: Authorization header (Bearer token)
    
    Returns:
        "admin" if token is valid
    
    Raises:
        HTTPException: 401 if token is invalid or missing
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = parts[1]
    
    # Check admin token
    if token != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin token"
        )
    
    return "admin"


async def check_model_access(username: str, model_name: str) -> bool:
    """
    Check if user has access to specific model
    
    Args:
        username: Username
        model_name: Model display name
    
    Returns:
        True if user has access, False otherwise
    """
    return await user_manager.check_model_access(username, model_name)

