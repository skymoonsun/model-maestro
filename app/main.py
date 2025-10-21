"""Main FastAPI application - Ollama Proxy with JWT Authentication"""

from typing import Any, Dict
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
import logging
import json

from app.auth import get_current_user, check_model_access
from app.proxy import ollama_proxy
from app.config import get_settings
from app.redis import RedisManager
from app.models import (
    OllamaGenerateRequest,
    OllamaChatRequest,
    OllamaEmbeddingsRequest,
    OllamaShowRequest,
    OllamaCopyRequest,
    OllamaDeleteRequest,
    OllamaPullRequest,
    OllamaPushRequest,
    OllamaCreateRequest,
)
from app.admin import router as admin_router
from app.user_manager import user_manager

# Setup logging
settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Global instances
redis_manager = RedisManager(settings.redis_url)

# Set global Redis manager in config
from app.config import redis_manager as config_redis_manager
import app.config
app.config.redis_manager = redis_manager

# Create FastAPI app
app = FastAPI(
    title="Ollama Proxy API",
    description="JWT authenticated proxy for Ollama with cloud model mapping",
    version="1.0.0"
)

# Include admin router
app.include_router(admin_router)

# Disable response buffering for streaming
@app.on_event("startup")
async def startup_event():
    logger.info("Starting Ollama Proxy API with streaming support and PostgreSQL")
    
    # Connect to Redis
    await redis_manager.connect()


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Ollama Proxy API")
    
    # Disconnect from Redis
    await redis_manager.disconnect()


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Ollama Proxy API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/api/generate")
async def generate(
    request: OllamaGenerateRequest,
    username: str = Depends(get_current_user)
):
    """
    Generate completion from a model
    
    Requires JWT authentication and model access
    """
    logger.info(f"User {username} requesting generate with model {request.model}")
    
    # Check model access
    has_access = await check_model_access(username, request.model)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {request.model}"
        )
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/generate",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/chat")
async def chat(
    request: OllamaChatRequest,
    username: str = Depends(get_current_user)
):
    """
    Generate chat completion
    
    Requires JWT authentication and model access
    """
    logger.info(f"User {username} requesting chat with model {request.model}")
    
    # Check model access
    has_access = await check_model_access(username, request.model)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {request.model}"
        )
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/chat",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/embeddings")
async def embeddings(
    request: OllamaEmbeddingsRequest,
    username: str = Depends(get_current_user)
):
    """
    Generate embeddings from a model
    
    Requires JWT authentication and model access
    """
    logger.info(f"User {username} requesting embeddings with model {request.model}")
    
    # Check model access
    has_access = await check_model_access(username, request.model)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {request.model}"
        )
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/embeddings",
        data=request.model_dump(exclude_none=True)
    )


@app.get("/api/tags")
async def list_models(
    username: str = Depends(get_current_user)
):
    """
    List available models (filtered by user access)
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting model list")
    logger.info(f"DEBUG: /api/tags endpoint called with username: {username}")
    
    # Get all models from Ollama
    logger.info(f"Calling ollama_proxy.proxy_request for /api/tags")
    logger.info(f"ollama_proxy instance: {ollama_proxy}")
    all_models_response = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/api/tags"
    )
    logger.info(f"Received response from Ollama: {type(all_models_response)}")
    
    # Get user's model access
    user_models_data = await user_manager.get_user_models(username)
    logger.info(f"User {username} model access: {user_models_data}")
    
    # If user has access to all models, return everything
    if user_models_data and user_models_data["has_all_models"]:
        return all_models_response
    
    # If user_models_data is None, deny access
    if not user_models_data:
        logger.warning(f"User {username} not found or has no model access")
        return {"models": []}
    
    # Filter models based on user access
    allowed_models = set(user_models_data["models"])
    
    if isinstance(all_models_response, dict) and "models" in all_models_response:
        filtered_models = [
            model for model in all_models_response["models"]
            if model.get("name") in allowed_models or model.get("model") in allowed_models
        ]
        return {"models": filtered_models}
    
    return all_models_response


@app.post("/api/show")
async def show_model(
    request: OllamaShowRequest,
    username: str = Depends(get_current_user)
):
    """
    Show model information
    
    Requires JWT authentication and model access
    """
    logger.info(f"User {username} requesting show for model {request.name}")
    
    # Check model access
    has_access = await check_model_access(username, request.name)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {request.name}"
        )
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/show",
        data=request.model_dump(exclude_none=True)
    )


@app.post("/api/copy")
async def copy_model(
    request: OllamaCopyRequest,
    username: str = Depends(get_current_user)
):
    """
    Copy a model
    
    Requires JWT authentication
    """
    logger.info(f"User {username} copying model from {request.source} to {request.destination}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/copy",
        data=request.model_dump(exclude_none=True)
    )


@app.delete("/api/delete")
async def delete_model(
    request: OllamaDeleteRequest,
    username: str = Depends(get_current_user)
):
    """
    Delete a model
    
    Requires JWT authentication
    """
    logger.info(f"User {username} deleting model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="DELETE",
        endpoint="/api/delete",
        data=request.model_dump(exclude_none=True)
    )


@app.post("/api/pull")
async def pull_model(
    request: OllamaPullRequest,
    username: str = Depends(get_current_user)
):
    """
    Pull a model from registry
    
    Requires JWT authentication
    """
    logger.info(f"User {username} pulling model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/pull",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/push")
async def push_model(
    request: OllamaPushRequest,
    username: str = Depends(get_current_user)
):
    """
    Push a model to registry
    
    Requires JWT authentication
    """
    logger.info(f"User {username} pushing model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/push",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/create")
async def create_model(
    request: OllamaCreateRequest,
    username: str = Depends(get_current_user)
):
    """
    Create a model from a Modelfile
    
    Requires JWT authentication
    """
    logger.info(f"User {username} creating model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/create",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


# OpenAI Compatible Endpoints for Cursor
# /v1/models endpoint with filtering
@app.get("/v1/models")
async def openai_list_models(username: str = Depends(get_current_user)):
    """
    List available models in OpenAI format (filtered by user access)
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting OpenAI model list")
    
    # Get all models from Ollama
    all_models_response = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/v1/models"
    )
    
    # Get user's model access
    user_models_data = await user_manager.get_user_models(username)
    
    # If user has access to all models, return everything
    if user_models_data["has_all_models"]:
        return all_models_response
    
    # Filter models based on user access
    allowed_models = set(user_models_data["models"])
    
    if isinstance(all_models_response, dict) and "data" in all_models_response:
        filtered_models = [
            model for model in all_models_response["data"]
            if model.get("id") in allowed_models
        ]
        return {
            "object": all_models_response.get("object", "list"),
            "data": filtered_models
        }
    
    return all_models_response


# Simple pass-through proxy with JWT auth only
@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def openai_v1_proxy(
    path: str,
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    Proxy all /v1/* requests to Ollama with JWT authentication
    Only adds JWT, everything else passes through unchanged
    Model mapping is handled in proxy.py
    """
    method = request.method
    endpoint = f"/v1/{path}"
    
    # Get request body if exists
    data = None
    if method in ["POST", "PUT", "PATCH"]:
        body = await request.body()
        if body:
            data = json.loads(body.decode('utf-8'))
    
    # Determine if streaming
    stream = data.get("stream", False) if data else False
    
    # Log request details
    if data:
        msg_count = len(data.get('messages', [])) if 'messages' in data else 0
        model_name = data.get('model', '')
        logger.info(f"User {username} proxy {method} {endpoint} - model: {model_name}, messages: {msg_count}")
        
        # Check model access for chat completions
        if model_name and 'chat/completions' in endpoint:
            has_access = await check_model_access(username, model_name)
            if not has_access:
                raise HTTPException(
                    status_code=403,
                    detail=f"Bu modele erişim yetkiniz yok: {model_name}"
                )
        
        # Some models don't support tools parameter (e.g., deepseek-v3.1)
        # Remove tools for models that don't support them
        models_without_tool_support = ['deepseek-v3.1:671b', 'deepseek-v3.1']
        
        if any(unsupported in model_name for unsupported in models_without_tool_support):
            if 'tools' in data or 'tool_choice' in data:
                data = {k: v for k, v in data.items() if k not in ['tools', 'tool_choice']}
                logger.info(f"Removed tools/tool_choice for model {model_name} (not supported)")
    else:
        logger.info(f"User {username} proxy {method} {endpoint}")
    
    # Direct proxy with cleaned data
    return await ollama_proxy.proxy_request(
        method=method,
        endpoint=endpoint,
        data=data,
        stream=stream
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

