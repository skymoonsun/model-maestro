"""
Admin endpoints for user and model management.
Requires admin token for authentication.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from app.models import (
    CreateUserRequest,
    AssignModelsRequest,
    CreateMappingRequest,
    UserResponse,
    UserWithModelsResponse,
    UserModelsResponse,
    ModelMappingResponse,
    SetUserLimitRequest,
    UserLimitResponse
)
from app.auth import verify_admin
from app.user_manager import user_manager
from app.config import model_mapper, parse_context_length, format_context_length

router = APIRouter(prefix="/admin")


# ============================================================================
# User Management Endpoints
# ============================================================================

@router.post("/users", response_model=UserResponse, status_code=201, tags=["Admin - User Management"])
async def create_user_admin(
    request: CreateUserRequest,
    admin: str = Depends(verify_admin)
):
    """
    Create a new user (Admin only).
    
    Returns user info with generated JWT token.
    """
    try:
        user_data = await user_manager.create_user(request.username)
        return UserResponse(
            username=user_data["username"],
            token=user_data["token"],
            created_at=user_data.get("created_at"),
            updated_at=user_data.get("updated_at"),
            is_active=user_data.get("is_active", True)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/users/{username}", status_code=204, tags=["Admin - User Management"])
async def delete_user_admin(
    username: str,
    admin: str = Depends(verify_admin)
):
    """
    Delete a user (soft delete: is_active=False).
    
    Admin only.
    """
    try:
        await user_manager.delete_user(username)
        return None
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/users/{username}/token", response_model=UserResponse, tags=["Admin - User Management"])
async def refresh_user_token_admin(
    username: str,
    admin: str = Depends(verify_admin)
):
    """
    Refresh user's JWT token (Admin only).
    
    Generates a new token and invalidates the old one.
    """
    try:
        user_data = await user_manager.refresh_token(username)
        return UserResponse(
            username=user_data["username"],
            token=user_data["token"],
            created_at=user_data.get("created_at"),
            updated_at=user_data.get("updated_at"),
            is_active=user_data.get("is_active", True)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/users/{username}", response_model=UserWithModelsResponse, tags=["Admin - User Management"])
async def get_user_admin(
    username: str,
    admin: str = Depends(verify_admin)
):
    """
    Get user details including assigned models (Admin only).
    """
    try:
        user_data = await user_manager.get_user(username)
        user_models = await user_manager.get_user_models(username)
        
        return UserWithModelsResponse(
            username=user_data["username"],
            token=user_data["token"],
            created_at=user_data.get("created_at"),
            updated_at=user_data.get("updated_at"),
            is_active=user_data.get("is_active", True),
            has_all_models=user_models["has_all_models"],
            models=user_models["models"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/users", response_model=List[UserWithModelsResponse], tags=["Admin - User Management"])
async def list_users_admin(admin: str = Depends(verify_admin)):
    """
    List all users with their assigned models (Admin only).
    """
    users = await user_manager.list_users()
    
    result = []
    for user in users:
        user_models = await user_manager.get_user_models(user["username"])
        result.append(UserWithModelsResponse(
            username=user["username"],
            token=user["token"],
            created_at=user.get("created_at"),
            updated_at=user.get("updated_at"),
            is_active=user.get("is_active", True),
            has_all_models=user_models["has_all_models"],
            models=user_models["models"]
        ))
    
    return result


# ============================================================================
# Model Assignment Endpoints
# ============================================================================

@router.post("/users/{username}/models", response_model=UserModelsResponse, tags=["Admin - Model Assignment"])
async def assign_models(
    username: str,
    request: AssignModelsRequest,
    admin: str = Depends(verify_admin)
):
    """
    Assign specific models to a user (Admin only).
    
    Request body:
    {
        "models": ["gpt-oss:120b", "deepseek-v3.1:671b"]
    }
    
    This replaces existing model assignments (except has_all_models).
    """
    try:
        await user_manager.assign_models_to_user(username, request.models)
        
        # Invalidate model access cache
        from app.redis import redis_manager, CACHE_KEYS
        await redis_manager.delete(CACHE_KEYS["USER_ACCESS"].format(username=username))
        
        user_models = await user_manager.get_user_models(username)
        
        return UserModelsResponse(
            username=username,
            has_all_models=user_models["has_all_models"],
            models=user_models["models"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/users/{username}/models/all", response_model=UserModelsResponse, tags=["Admin - Model Assignment"])
async def grant_all_models(
    username: str,
    admin: str = Depends(verify_admin)
):
    """
    Grant user access to all models (Admin only).
    
    Sets has_all_models=True and removes specific model assignments.
    """
    try:
        await user_manager.grant_all_models(username)
        
        # Invalidate model access cache
        from app.redis import redis_manager, CACHE_KEYS
        await redis_manager.delete(CACHE_KEYS["USER_ACCESS"].format(username=username))
        
        user_models = await user_manager.get_user_models(username)
        
        return UserModelsResponse(
            username=username,
            has_all_models=user_models["has_all_models"],
            models=user_models["models"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/users/{username}/models", response_model=UserModelsResponse, tags=["Admin - Model Assignment"])
async def get_user_models_admin(
    username: str,
    admin: str = Depends(verify_admin)
):
    """
    Get user's assigned models (Admin only).
    """
    try:
        user_models = await user_manager.get_user_models(username)
        
        return UserModelsResponse(
            username=username,
            has_all_models=user_models["has_all_models"],
            models=user_models["models"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/users/{username}/models/{model_name}", status_code=204, tags=["Admin - Model Assignment"])
async def revoke_model(
    username: str,
    model_name: str,
    admin: str = Depends(verify_admin)
):
    """
    Revoke model access from user (Admin only).
    
    - If model_name is "all": Revokes ALL model access (including has_all_models)
    - Otherwise: Revokes specific model access
    
    Examples:
    - DELETE /admin/users/john/models/all → Removes all model access
    - DELETE /admin/users/john/models/gpt-oss:120b → Removes only gpt-oss:120b
    """
    try:
        if model_name.lower() == "all":
            # Revoke all model access
            await user_manager.revoke_all_models(username)
        else:
            # Revoke specific model
            await user_manager.revoke_model(username, model_name)
        return None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Model Mapping Management Endpoints
# ============================================================================

@router.post("/model-mappings", response_model=ModelMappingResponse, tags=["Admin - Model Mapping"])
async def create_or_update_model_mapping(
    request: CreateMappingRequest,
    admin: str = Depends(verify_admin)
):
    """
    Create or update a model mapping (Admin only).

    Eğer display_name zaten mevcutsa günceller, yoksa yeni oluşturur.

    Request body:
    {
        "display_name": "glm-5:cloud",
        "real_name": "glm-5:cloud",
        "node_id": 1,             // Opsiyonel: NULL = global (tüm node'lar)
        "context_length": "198K"  // Opsiyonel: "128K", "256K", "1M", "32768"
    }

    Cache is automatically reloaded after creation/update.
    """
    try:
        # Parse context_length if provided
        ctx_length_tokens = None
        if request.context_length and request.context_length.strip():
            try:
                ctx_length_tokens = parse_context_length(request.context_length)
            except (ValueError, TypeError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Geçersiz context_length formatı: '{request.context_length}'. "
                           f"Desteklenen formatlar: '198K', '128K', '1M', '32768'. Hata: {str(e)}"
                )

        mapping = await model_mapper.create_or_update_mapping(
            request.display_name,
            request.real_name,
            ctx_length_tokens,
            request.capabilities,
            request.node_id
        )

        # Resolve node name and type for response
        node_name = None
        node_type = None
        if mapping.get("node_id"):
            from app.repositories.node_repository import NodeRepository
            from app.database import async_session_maker
            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                node = await node_repo.get_by_id(mapping["node_id"])
                if node:
                    node_name = node.name
                    node_type = node.node_type

        # Format context_length for display
        ctx_display = format_context_length(mapping.get("context_length")) if mapping.get("context_length") else None

        return ModelMappingResponse(
            display_name=mapping["display_name"],
            real_name=mapping["real_name"],
            node_id=mapping.get("node_id"),
            node_name=node_name,
            node_type=node_type,
            context_length=mapping.get("context_length"),
            context_length_display=ctx_display,
            capabilities=mapping.get("capabilities"),
            created_at=mapping.get("created_at")
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/model-mappings", response_model=List[ModelMappingResponse], tags=["Admin - Model Mapping"])
async def list_model_mappings(admin: str = Depends(verify_admin)):
    """
    List all model mappings (Admin only).
    """
    from app.repositories.node_repository import NodeRepository
    from app.database import async_session_maker

    mappings = await model_mapper.list_mappings()

    # Resolve node names and types
    node_names = {}
    node_types = {}
    async with async_session_maker() as session:
        node_repo = NodeRepository(session)
        node_ids = {m.get("node_id") for m in mappings if m.get("node_id")}
        for nid in node_ids:
            node = await node_repo.get_by_id(nid)
            if node:
                node_names[nid] = node.name
                node_types[nid] = node.node_type

    return [
        ModelMappingResponse(
            display_name=m["display_name"],
            real_name=m["real_name"],
            node_id=m.get("node_id"),
            node_name=node_names.get(m.get("node_id")),
            node_type=node_types.get(m.get("node_id")),
            context_length=m.get("context_length"),
            context_length_display=format_context_length(m.get("context_length")) if m.get("context_length") else None,
            capabilities=m.get("capabilities"),
            created_at=m.get("created_at")
        )
        for m in mappings
    ]


@router.put("/model-mappings/{old_display_name}", response_model=ModelMappingResponse, tags=["Admin - Model Mapping"])
async def update_model_mapping(
    old_display_name: str,
    request: CreateMappingRequest,
    admin: str = Depends(verify_admin)
):
    """
    Update an existing model mapping including its display name (Admin only).

    Request body:
    {
        "display_name": "yeni-isim:latest",
        "real_name": "yeni-isim:cloud",
        "node_id": 1,
        "context_length": "198K"
    }
    """
    try:
        ctx_length_tokens = None
        if request.context_length and request.context_length.strip():
            try:
                ctx_length_tokens = parse_context_length(request.context_length)
            except (ValueError, TypeError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Geçersiz context_length formatı: '{request.context_length}'. "
                           f"Desteklenen formatlar: '198K', '128K', '1M', '32768'. Hata: {str(e)}"
                )

        mapping = await model_mapper.update_mapping(
            old_display_name,
            request.display_name,
            request.real_name,
            ctx_length_tokens,
            request.capabilities,
            request.node_id
        )

        node_name = None
        node_type = None
        if mapping.get("node_id"):
            from app.repositories.node_repository import NodeRepository
            from app.database import async_session_maker
            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                node = await node_repo.get_by_id(mapping["node_id"])
                if node:
                    node_name = node.name
                    node_type = node.node_type

        ctx_display = format_context_length(mapping.get("context_length")) if mapping.get("context_length") else None

        return ModelMappingResponse(
            display_name=mapping["display_name"],
            real_name=mapping["real_name"],
            node_id=mapping.get("node_id"),
            node_name=node_name,
            node_type=node_type,
            context_length=mapping.get("context_length"),
            context_length_display=ctx_display,
            capabilities=mapping.get("capabilities"),
            created_at=mapping.get("created_at")
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/model-mappings/{display_name}", status_code=204, tags=["Admin - Model Mapping"])
async def delete_model_mapping(
    display_name: str,
    admin: str = Depends(verify_admin)
):
    """
    Delete a model mapping (Admin only).
    
    Note: This does not affect user model assignments.
    Cache is automatically reloaded after deletion.
    """
    try:
        await model_mapper.delete_mapping(display_name)
        # Cache is automatically updated in delete_mapping
        return None
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/model-mappings/invalidate-cache", status_code=200, tags=["Admin - Model Mapping"])
async def invalidate_model_mapping_cache(
    admin: str = Depends(verify_admin)
):
    """
    Invalidate model mapping cache (Admin only).
    Forces reload from database on next request.
    """
    try:
        await model_mapper.invalidate_cache()
        return {"message": "Cache invalidated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to invalidate cache: {str(e)}")


# ============================================================================
# User Limit Management Endpoints
# ============================================================================

@router.post("/users/{username}/limits", response_model=UserLimitResponse, tags=["Admin - User Limits"])
async def set_user_limit(
    username: str,
    request: SetUserLimitRequest,
    admin: str = Depends(verify_admin)
):
    """
    Set user request and token limits (Admin only).
    
    Request body:
    {
        "request_limit": 1000,  # None for unlimited
        "token_limit": 1000000  # None for unlimited
    }
    
    To set unlimited access, send null values:
    {
        "request_limit": null,
        "token_limit": null
    }
    """
    try:
        limit_data = await user_manager.set_user_limit(
            username,
            request.request_limit,
            request.token_limit
        )
        
        if not limit_data:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Invalidate limit cache
        from app.redis import redis_manager, CACHE_KEYS
        await redis_manager.delete(CACHE_KEYS["USER_LIMIT"].format(username=username))
        
        return UserLimitResponse(**limit_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{username}/limits", response_model=UserLimitResponse, tags=["Admin - User Limits"])
async def get_user_limit(
    username: str,
    admin: str = Depends(verify_admin)
):
    """
    Get user request and token limits (Admin only).
    """
    try:
        limit_data = await user_manager.get_user_limit(username)
        
        if not limit_data:
            raise HTTPException(status_code=404, detail="User not found")
        
        return UserLimitResponse(**limit_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{username}/limits", status_code=204, tags=["Admin - User Limits"])
async def remove_user_limit(
    username: str,
    admin: str = Depends(verify_admin)
):
    """
    Remove user limits (Admin only).
    This will remove all limits for the user, making them unlimited.
    """
    try:
        result = await user_manager.remove_user_limit(username)
        
        if not result:
            raise HTTPException(status_code=404, detail="User not found or no limits to remove")
        
        # Invalidate limit cache
        from app.redis import redis_manager, CACHE_KEYS
        await redis_manager.delete(CACHE_KEYS["USER_LIMIT"].format(username=username))
        
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# User Activity Log Endpoints
# ============================================================================

@router.get("/users/{username}/activity", tags=["Admin - Activity Logs"])
async def get_user_activity(
    username: str,
    limit: int = 100,
    offset: int = 0,
    admin: str = Depends(verify_admin)
):
    """
    Get user activity logs (Admin only).
    
    Query parameters:
    - limit: Number of logs to return (default: 100)
    - offset: Number of logs to skip (default: 0)
    """
    try:
        activities = await user_manager.get_user_activity(username, limit, offset)
        
        if activities is None:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {
            "username": username,
            "activities": activities,
            "total_returned": len(activities),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{username}/token-usage", tags=["Admin - Activity Logs"])
async def get_user_token_usage(
    username: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin: str = Depends(verify_admin)
):
    """
    Get user token usage statistics (Admin only).
    
    Query parameters:
    - start_date: Start date in ISO format (e.g., "2024-01-01")
    - end_date: End date in ISO format (e.g., "2024-01-31")
    """
    try:
        from datetime import datetime
        
        start_dt = None
        end_dt = None
        
        if start_date:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        
        usage = await user_manager.get_user_token_usage(username, start_dt, end_dt)
        
        if usage is None:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {
            "username": username,
            "usage": usage,
            "period": {
                "start_date": start_date,
                "end_date": end_date
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{username}/model-usage", tags=["Admin - Activity Logs"])
async def get_user_model_usage(
    username: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin: str = Depends(verify_admin)
):
    """
    Get user model usage statistics (Admin only).
    
    Query parameters:
    - start_date: Start date in ISO format (e.g., "2024-01-01")
    - end_date: End date in ISO format (e.g., "2024-01-31")
    """
    try:
        from datetime import datetime
        
        start_dt = None
        end_dt = None
        
        if start_date:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        
        model_usage = await user_manager.get_user_model_usage(username, start_dt, end_dt)
        
        if model_usage is None:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {
            "username": username,
            "model_usage": model_usage,
            "period": {
                "start_date": start_date,
                "end_date": end_date
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


