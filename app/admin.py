"""
Admin endpoints for user and model management.
Requires admin token for authentication.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.models import (
    CreateUserRequest,
    AssignModelsRequest,
    CreateMappingRequest,
    UserResponse,
    UserWithModelsResponse,
    UserModelsResponse,
    ModelMappingResponse
)
from app.auth import verify_admin
from app.user_manager import user_manager
from app.config import ModelMappingManager

router = APIRouter(prefix="/admin", tags=["Admin"])
model_mapping_manager = ModelMappingManager()


# ============================================================================
# User Management Endpoints
# ============================================================================

@router.post("/users", response_model=UserResponse, status_code=201)
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


@router.delete("/users/{username}", status_code=204)
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


@router.put("/users/{username}/token", response_model=UserResponse)
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


@router.get("/users/{username}", response_model=UserWithModelsResponse)
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


@router.get("/users", response_model=List[UserWithModelsResponse])
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

@router.post("/users/{username}/models", response_model=UserModelsResponse)
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
        user_models = await user_manager.get_user_models(username)
        
        return UserModelsResponse(
            username=username,
            has_all_models=user_models["has_all_models"],
            models=user_models["models"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/users/{username}/models/all", response_model=UserModelsResponse)
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
        user_models = await user_manager.get_user_models(username)
        
        return UserModelsResponse(
            username=username,
            has_all_models=user_models["has_all_models"],
            models=user_models["models"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/users/{username}/models", response_model=UserModelsResponse)
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


@router.delete("/users/{username}/models/{model_name}", status_code=204)
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

@router.post("/model-mappings", response_model=ModelMappingResponse, status_code=201)
async def create_model_mapping(
    request: CreateMappingRequest,
    admin: str = Depends(verify_admin)
):
    """
    Create a new model mapping (Admin only).
    
    Request body:
    {
        "display_name": "gpt-oss:120b",
        "real_name": "gpt-oss:120b-cloud"
    }
    
    Cache is automatically reloaded after creation.
    """
    try:
        mapping = await model_mapping_manager.create_mapping(
            request.display_name,
            request.real_name
        )
        
               # Cache is automatically updated in create_mapping
        
        return ModelMappingResponse(
            display_name=mapping["display_name"],
            real_name=mapping["real_name"],
            created_at=mapping.get("created_at")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/model-mappings", response_model=List[ModelMappingResponse])
async def list_model_mappings(admin: str = Depends(verify_admin)):
    """
    List all model mappings (Admin only).
    """
    mappings = await model_mapping_manager.list_mappings()
    
    return [
        ModelMappingResponse(
            display_name=m["display_name"],
            real_name=m["real_name"],
            created_at=m.get("created_at")
        )
        for m in mappings
    ]


@router.delete("/model-mappings/{display_name}", status_code=204)
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
        await model_mapping_manager.delete_mapping(display_name)
        # Cache is automatically updated in delete_mapping
        return None
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/model-mappings/invalidate-cache", status_code=200)
async def invalidate_model_mapping_cache(
    admin: str = Depends(verify_admin)
):
    """
    Invalidate model mapping cache (Admin only).
    Forces reload from database on next request.
    """
    try:
        await model_mapping_manager.invalidate_cache()
        return {"message": "Cache invalidated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to invalidate cache: {str(e)}")


