"""Main FastAPI application - Ollama Proxy with JWT Authentication"""

from typing import Any, Dict
from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
import secrets
import logging
import json

from app.auth import get_current_user, check_model_access
from app.proxy import ollama_proxy
from app.config import get_settings, model_mapper
from app.redis import RedisManager
from app.models import (
    OllamaGenerateRequest,
    OllamaChatRequest,
    OllamaEmbeddingsRequest,
)
from app.admin import router as admin_router
from app.user_manager import user_manager

# Setup logging
settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Initialize global Redis manager
import app.redis
app.redis.redis_manager = RedisManager(settings.redis_url)
redis_manager = app.redis.redis_manager

# Security schemes
security = HTTPBasic()
bearer_scheme = HTTPBearer()

# Set global Redis manager in config
import app.config
app.config.redis_manager = redis_manager

# Create FastAPI app with docs disabled (we'll add auth)
app = FastAPI(
    title="Ollama Proxy API",
    description="JWT authenticated proxy for Ollama with cloud model mapping",
    version="1.0.0",
    docs_url=None,  # Disable default docs
    redoc_url=None,  # Disable default redoc
    openapi_url=None,  # Disable default openapi
    swagger_ui_parameters={"persistAuthorization": True}
)

# Include admin router
app.include_router(admin_router)


# Basic Auth for documentation
def verify_docs_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify basic auth credentials for documentation access"""
    correct_username = secrets.compare_digest(credentials.username, settings.docs_username)
    correct_password = secrets.compare_digest(credentials.password, settings.docs_password)
    
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# Protected documentation endpoints
@app.get("/api/docs", include_in_schema=False)
async def get_documentation(username: str = Depends(verify_docs_credentials)):
    """Swagger UI with basic auth"""
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="API Docs")


@app.get("/api/redoc", include_in_schema=False)
async def get_redoc(username: str = Depends(verify_docs_credentials)):
    """ReDoc with basic auth"""
    return get_redoc_html(openapi_url="/api/openapi.json", title="API Docs")


@app.get("/api/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(username: str = Depends(verify_docs_credentials)):
    """OpenAPI schema with basic auth"""
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Add JWT security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your JWT token in the format: your-token-here (without 'Bearer' prefix)"
        }
    }
    
    # Apply security globally
    openapi_schema["security"] = [{"BearerAuth": []}]
    
    return openapi_schema

# Disable response buffering for streaming
@app.on_event("startup")
async def startup_event():
    logger.info("Starting Ollama Proxy API with streaming support and PostgreSQL")
    
    # Connect to Redis
    await redis_manager.connect()
    
    # Start background tasks
    from app.background_tasks import start_background_tasks
    await start_background_tasks()


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Ollama Proxy API")
    
    # Stop background tasks
    from app.background_tasks import stop_background_tasks
    await stop_background_tasks()
    
    # Close HTTP client connection pool
    await ollama_proxy.close()
    
    # Disconnect from Redis
    await redis_manager.disconnect()


# ============================================================================
# System Endpoints
# ============================================================================

@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint"""
    return {
        "message": "Ollama Proxy API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health", tags=["System"])
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


# ============================================================================
# Ollama Native API Endpoints
# ============================================================================

@app.get("/api/tags", tags=["Ollama Native API"])
async def list_models(username: str = Depends(get_current_user)):
    """
    List available models (filtered by user access and mapped display names)
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting model list")
    
    # Get all models from Ollama
    all_models_response = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/api/tags",
        username=username
    )
    
    # Get user's model access
    user_models_data = await user_manager.get_user_models(username)
    
    # If user_models_data is None, deny access
    if not user_models_data:
        logger.warning(f"User {username} not found or has no model access")
        return {"models": []}
    
    # Get all mappings from database
    all_mappings = model_mapper.get_all_mappings()
    
    # Apply model mapping to display names
    if isinstance(all_models_response, dict) and "models" in all_models_response:
        # Use a dict to track unique display names and avoid duplicates
        models_dict = {}
        
        # First, add all models from Ollama with reverse mapping
        for model in all_models_response["models"]:
            model_name = model.get("name") or model.get("model")
            if model_name:
                # Get ALL display names for this real model
                display_names = model_mapper.get_all_display_names_for_real_name(model_name)
                
                for display_name in display_names:
                    if display_name not in models_dict:
                        model_copy = model.copy()
                        model_copy["name"] = display_name
                        model_copy["model"] = display_name
                        models_dict[display_name] = model_copy
        
        # Second, add all display names from mappings (even if real model doesn't exist in Ollama)
        # This allows multiple display names to point to the same real model
        for display_name, real_name in all_mappings.items():
            if display_name not in models_dict:
                # Create a synthetic model entry for this display name
                # Use a template from Ollama models or create a minimal one
                base_model = all_models_response["models"][0] if all_models_response["models"] else {}
                model_entry = base_model.copy() if base_model else {}
                model_entry["name"] = display_name
                model_entry["model"] = display_name
                models_dict[display_name] = model_entry
        
        mapped_models = list(models_dict.values())
        
        # Filter models based on user access (using display names)
        if user_models_data["has_all_models"]:
            return {"models": mapped_models}
        
        allowed_models = set(user_models_data["models"])
        filtered_models = [
            model for model in mapped_models
            if model.get("name") in allowed_models or model.get("model") in allowed_models
        ]
        return {"models": filtered_models}
    
    return all_models_response


@app.post("/api/generate", tags=["Ollama Native API"])
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
    
    # Check user limits BEFORE making request to Ollama
    within_limits = await ollama_proxy.check_user_limits(username, "generate")
    if not within_limits:
        raise HTTPException(
            status_code=429,
            detail="User has exceeded their request or token limit"
        )
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/generate",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False,
        username=username
    )


@app.post("/api/chat", tags=["Ollama Native API"])
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
    
    # Check user limits BEFORE making request to Ollama
    within_limits = await ollama_proxy.check_user_limits(username, "chat")
    if not within_limits:
        raise HTTPException(
            status_code=429,
            detail="User has exceeded their request or token limit"
        )
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/chat",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False,
        username=username
    )


@app.post("/api/embeddings", tags=["Ollama Native API"])
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
    
    # Check user limits BEFORE making request to Ollama
    within_limits = await ollama_proxy.check_user_limits(username, "embeddings")
    if not within_limits:
        raise HTTPException(
            status_code=429,
            detail="User has exceeded their request or token limit"
        )
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/embeddings",
        data=request.model_dump(exclude_none=True),
        username=username
    )


# ============================================================================
# OpenAI Compatible API Endpoints
# ============================================================================

@app.get("/v1/models", tags=["OpenAI Compatible API"])
async def openai_list_models(username: str = Depends(get_current_user)):
    """
    List available models in OpenAI format (filtered by user access and mapped display names)
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting OpenAI model list")
    
    # Get all models from Ollama
    all_models_response = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/v1/models",
        username=username
    )
    
    # Get user's model access
    user_models_data = await user_manager.get_user_models(username)
    
    # If user_models_data is None, deny access
    if not user_models_data:
        logger.warning(f"User {username} not found or has no model access")
        return {"object": "list", "data": []}
    
    # Get all mappings from database
    all_mappings = model_mapper.get_all_mappings()
    
    # Apply model mapping to display names
    if isinstance(all_models_response, dict) and "data" in all_models_response:
        # Use a dict to track unique display names and avoid duplicates
        models_dict = {}
        
        # First, add all models from Ollama with reverse mapping
        for model in all_models_response["data"]:
            model_id = model.get("id")
            if model_id:
                # Get ALL display names for this real model
                display_names = model_mapper.get_all_display_names_for_real_name(model_id)
                
                for display_name in display_names:
                    if display_name not in models_dict:
                        model_copy = model.copy()
                        model_copy["id"] = display_name
                        models_dict[display_name] = model_copy
        
        # Second, add all display names from mappings (even if real model doesn't exist in Ollama)
        for display_name, real_name in all_mappings.items():
            if display_name not in models_dict:
                # Create a synthetic model entry for this display name
                base_model = all_models_response["data"][0] if all_models_response["data"] else {}
                model_entry = base_model.copy() if base_model else {}
                model_entry["id"] = display_name
                models_dict[display_name] = model_entry
        
        mapped_models = list(models_dict.values())
        
        # Filter models based on user access (using display names)
        if user_models_data["has_all_models"]:
            return {
                "object": all_models_response.get("object", "list"),
                "data": mapped_models
            }
        
        allowed_models = set(user_models_data["models"])
        filtered_models = [
            model for model in mapped_models
            if model.get("id") in allowed_models
        ]
        return {
            "object": all_models_response.get("object", "list"),
            "data": filtered_models
        }
    
    return all_models_response


@app.post("/v1/chat/completions", tags=["OpenAI Compatible API"])
async def openai_chat_completions(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    OpenAI compatible chat completions endpoint
    
    Fully compatible with Cursor IDE and other OpenAI-compatible clients.
    Supports streaming responses with proper SSE format.
    
    Requires JWT authentication and model access
    """
    # Parse request body
    body = await request.body()
    data = json.loads(body.decode('utf-8')) if body else {}
    
    model_name = data.get('model', '')
    msg_count = len(data.get('messages', []))
    stream = data.get("stream", False)
    
    logger.info(f"User {username} requesting OpenAI chat - model: {model_name}, messages: {msg_count}, stream: {stream}")
    
    # Check model access
    if model_name:
        has_access = await check_model_access(username, model_name)
        if not has_access:
            raise HTTPException(
                status_code=403,
                detail=f"Bu modele erişim yetkiniz yok: {model_name}"
            )
        
        # Check user limits
        within_limits = await ollama_proxy.check_user_limits(username, "chat")
        if not within_limits:
            raise HTTPException(
                status_code=429,
                detail="User has exceeded their request or token limit"
            )
    
    # Model-specific unsupported parameters
    # Key: model name prefix (matches any tag like :latest, :671b etc.)
    # Value: list of parameters that should be removed for this model
    model_unsupported_params = {
        # Deepseek models - don't support tools
        'deepseek': ['tools', 'tool_choice'],
        # Kimi models - don't support tools and top_p
        'kimi': ['tools', 'tool_choice', 'top_p'],
        # Minimax models - don't support tools
        'minimax': ['tools', 'tool_choice'],
        # Gemini models - don't support tools, top_p, and some other params
        'gemini': ['tools', 'tool_choice', 'top_p', 'presence_penalty', 'frequency_penalty'],
        # Qwen models - generally good but may have issues with some params
        'qwen': ['tools', 'tool_choice'],
        # Claude models via Ollama - limited tool support
        'claude': ['tools', 'tool_choice'],
        # Llama models - varying tool support
        'llama': ['tools', 'tool_choice'],
        # Mistral models
        'mistral': ['tools', 'tool_choice'],
        # Phi models
        'phi': ['tools', 'tool_choice'],
        # CodeLlama
        'codellama': ['tools', 'tool_choice'],
        # Starcoder
        'starcoder': ['tools', 'tool_choice'],
    }
    
    # Find unsupported params for this model using prefix matching
    unsupported_params = []
    model_name_lower = model_name.lower()
    
    for prefix, params in model_unsupported_params.items():
        if model_name_lower.startswith(prefix):
            unsupported_params = params
            break
    
    # Remove unsupported parameters for this specific model
    if unsupported_params:
        removed_params = [p for p in unsupported_params if p in data]
        if removed_params:
            data = {k: v for k, v in data.items() if k not in removed_params}
            logger.info(f"Removed {', '.join(removed_params)} for model {model_name} (not supported)")
    
    # Ollama's /v1/chat/completions endpoint may not support all OpenAI parameters
    # Remove parameters that Ollama doesn't recognize to avoid parsing errors
    # These are Cursor/OpenAI specific parameters that Ollama doesn't handle
    ollama_unsupported_params = [
        'logit_bias',
        'logprobs',
        'top_logprobs',
        'top_k',  # Ollama uses different format for top_k
        'response_format',  # Ollama may not support structured outputs
        'user',  # OpenAI tracking field
        'service_tier',  # OpenAI specific
        'parallel_tool_calls',  # OpenAI specific
        'stream_options',  # OpenAI specific streaming options
        'store',  # OpenAI specific
        'metadata',  # OpenAI specific
        'prediction',  # OpenAI specific
        'modalities',  # OpenAI specific
        'audio',  # OpenAI specific
    ]
    
    # Check if any unsupported parameters exist and remove them
    removed_ollama_params = [param for param in ollama_unsupported_params if param in data]
    if removed_ollama_params:
        data = {k: v for k, v in data.items() if k not in removed_ollama_params}
        logger.debug(f"Removed Ollama unsupported parameters: {', '.join(removed_ollama_params)}")
    
    # Ensure stream parameter is set (Cursor might not always send it)
    if 'stream' not in data:
        data['stream'] = stream
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=data,
        stream=stream,
        username=username
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
