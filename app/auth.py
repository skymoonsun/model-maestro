"""JWT authentication middleware with admin and model access control"""

from typing import Optional
from fastapi import Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends

from app.user_manager import user_manager
from app.config import get_settings
from app.redis import CACHE_KEYS, CACHE_TTL


security = HTTPBearer()
settings = get_settings()


async def get_current_user(
    authorization: Optional[str] = Header(None)
) -> str:
    """
    Validate JWT token and return current username
    
    Uses Redis cache to avoid DB lookups on every request.
    
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
    
    # Import redis_manager at runtime
    from app.redis import redis_manager
    
    # Try Redis cache first
    cache_key = f"token:{token}"
    if redis_manager:
        cached_username = await redis_manager.get(cache_key)
        
        if cached_username:
            return cached_username
    
    # Cache miss - verify token from DB
    username = await user_manager.verify_token(token)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Cache the token -> username mapping (unlimited TTL)
    if redis_manager:
        await redis_manager.set(cache_key, username, expire=CACHE_TTL["TOKEN_USERNAME"])
    
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
    
    Uses Redis cache to avoid DB lookups on every request.
    
    Args:
        username: Username
        model_name: Model display name
    
    Returns:
        True if user has access, False otherwise
    """
    # Import redis_manager at runtime
    from app.redis import redis_manager
    
    # Try Redis cache first
    cache_key = CACHE_KEYS["USER_ACCESS"].format(username=username)
    if redis_manager:
        cached_access = await redis_manager.get(cache_key)
        
        if cached_access:
            # cached_access = {"has_all": True} or {"has_all": False, "models": [...]}
            if cached_access.get("has_all"):
                return True
            return model_name in cached_access.get("models", [])
    
    # Cache miss - get from DB
    access_data = await user_manager.get_user_model_access(username)
    
    # Cache the result (unlimited TTL)
    if redis_manager:
        await redis_manager.set(cache_key, access_data, expire=CACHE_TTL["USER_ACCESS"])
    
    if access_data.get("has_all"):
        return True
    return model_name in access_data.get("models", [])


async def check_node_access(username: str, node_id: int) -> bool:
    """
    Check if user has access to specific node.

    Uses Redis cache to avoid DB lookups on every request.

    Args:
        username: Username
        node_id: Node ID

    Returns:
        True if user has access, False otherwise
    """
    from app.redis import redis_manager

    cache_key = f"user_node_access:{username}"
    if redis_manager:
        cached = await redis_manager.get(cache_key)
        if cached is not None:
            if cached.get("has_all"):
                return True
            return node_id in cached.get("nodes", [])

    access_data = await user_manager.get_user_node_access(username)

    if redis_manager:
        await redis_manager.set(cache_key, access_data, expire=CACHE_TTL["USER_ACCESS"])

    if access_data.get("has_all"):
        return True
    return node_id in access_data.get("nodes", [])


async def check_node_model_access(username: str, node_id: int, model_name: str) -> bool:
    """
    Check if user has access to specific node+model combination.

    Uses Redis cache to avoid DB lookups on every request.

    Args:
        username: Username
        node_id: Node ID
        model_name: Model name (real name)

    Returns:
        True if user has access, False otherwise
    """
    from app.redis import redis_manager

    cache_key = f"user_node_model_access:{username}"
    if redis_manager:
        cached = await redis_manager.get(cache_key)
        if cached is not None:
            if cached.get("has_all"):
                return True
            node_models = cached.get("node_models", [])
            return any(nm.get("node_id") == node_id and nm.get("model_name") == model_name for nm in node_models)

    access_data = await user_manager.get_user_node_model_access(username)

    if redis_manager:
        await redis_manager.set(cache_key, access_data, expire=CACHE_TTL["USER_ACCESS"])

    if access_data.get("has_all"):
        return True
    node_models = access_data.get("node_models", [])
    return any(nm.get("node_id") == node_id and nm.get("model_name") == model_name for nm in node_models)

