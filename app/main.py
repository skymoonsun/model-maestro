"""Model Maestro - Unified LLM Gateway with JWT Authentication"""

from typing import Any, Dict
from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
import secrets
import logging
import httpx
import json

from app.auth import get_current_user, check_model_access
from app.proxy import ollama_proxy
from app.config import get_settings, model_mapper, filter_tools_for_model, get_context_length_for_model
from app.redis import RedisManager
from app.models import (
    OllamaGenerateRequest,
    OllamaChatRequest,
    OllamaEmbeddingsRequest,
)
from app.admin import router as admin_router
from app.admin_auth import router as admin_auth_router
from app.admin_config import router as admin_config_router
from app.admin_dashboard import router as admin_dashboard_router
from app.admin_models import router as admin_models_router
from app.admin_nodes import router as admin_nodes_router
from app.admin_groups import router as admin_groups_router
from app.openclaw import router as openclaw_router
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
    title="Model Maestro",
    description="Unified LLM gateway with model routing, load balancing, and multi-provider support",
    version="1.0.0",
    docs_url=None,  # Disable default docs
    redoc_url=None,  # Disable default redoc
    openapi_url=None,  # Disable default openapi
    swagger_ui_parameters={"persistAuthorization": True}
)

# CORS middleware (frontend panel için)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production'da frontend URL'i ile sınırlandırılmalı
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include admin routers (auth first - login has no auth)
app.include_router(admin_auth_router)
app.include_router(admin_router)
app.include_router(admin_config_router)
app.include_router(admin_dashboard_router)
app.include_router(admin_models_router)
app.include_router(admin_nodes_router)
app.include_router(admin_groups_router)
app.include_router(openclaw_router)

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
    import asyncio
    from sqlalchemy import text
    from app.database import async_session_maker
    
    logger.info("Starting Model Maestro with streaming support and PostgreSQL")
    
    # Connect to Redis
    await redis_manager.connect()
    
    # Wait for Database
    logger.info("Waiting for database connection...")
    retries = 30
    for i in range(retries):
        try:
            async with async_session_maker() as session:
                await session.execute(text("SELECT 1"))
            logger.info("Database connection established")
            break
        except Exception as e:
            if i == retries - 1:
                logger.error(f"Failed to connect to database after {retries} attempts: {e}")
                raise e
            logger.warning(f"Database not ready yet, retrying in 1s... ({i+1}/{retries})")
            await asyncio.sleep(1)
    
    # Load configuration from database
    from app.services import config_manager
    await config_manager.load_all()
    logger.info("Configuration loaded from database")
    
    # Start background tasks
    from app.background_tasks import start_background_tasks
    await start_background_tasks()


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Model Maestro")
    
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
        "message": "Model Maestro",
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
    List available models from all healthy nodes (filtered by user access and mapped display names)

    Requires JWT authentication
    """
    global _models_cache_ts, _models_cache
    logger.info(f"User {username} requesting model list")

    # Use cached response if fresh enough
    import time
    now = time.monotonic()
    if _models_cache and (now - _models_cache_ts) < _MODELS_CACHE_TTL:
        all_models_response = _models_cache
    else:
        # Get all models from all healthy nodes
        from app.node_manager import node_manager

        all_models_response = await node_manager.get_all_models_from_nodes()

        # Cache the response
        _models_cache = all_models_response
        _models_cache_ts = now

    # If no nodes responded, fallback to proxy (single node)
    if not all_models_response.get("models"):
        logger.warning("[ModelList] No models from nodes, falling back to proxy")
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
    await model_mapper.ensure_loaded()
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


@app.post("/api/show", tags=["Ollama Native API"])
async def show_model(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    Show model information (details, parameters, template, capabilities, etc.)
    
    Request body:
    ```json
    {"model": "glm-5.1:cloud"}
    ```
    
    Optional parameters:
    - verbose (bool): Returns full data for verbose response fields
    - system (str): Override the system prompt for display purposes
    
    Requires JWT authentication
    """
    body = await request.body()
    data = json.loads(body.decode('utf-8')) if body else {}
    
    model_name = data.get('model', '')
    logger.info(f"User {username} requesting show for model {model_name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/show",
        data=data,
        username=username
    )


# ============================================================================
# Search Provider Mock Endpoints (Brave Search Compatible)
# ============================================================================

async def get_search_user(request: Request) -> str:
    """Dependency to get user from X-Subscription-Token or Authorization header"""
    token = request.headers.get("X-Subscription-Token")
    
    if not token:
        # Fallback to standard Authorization Bearer
        auth = request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth[7:]
            
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing search API token (X-Subscription-Token or Bearer Token required)"
        )
    
    # We create a fake bearer header to inject into get_current_user
    # get_current_user expects the exact header format: "Bearer <token>"
    return await get_current_user(f"Bearer {token}")


@app.get("/res/v1/web/search", tags=["Search Provider Mock"])
async def brave_search_mock(
    request: Request,
    q: str,
    count: int = 10,
    username: str = Depends(get_search_user)
):
    """
    Mock Brave Search API that forwards requests to Ollama Official Web Search.
    This behaves EXACTLY like Brave Search API (same URL, params, and response format)
    but runs Ollama's web search in the background.
    """
    logger.info(f"User {username} requesting Brave Search mock with query: {q}")
    settings = get_settings()
    
    # Format the request exactly as Ollama Cloud expects
    ollama_request_data = {"query": q}
    ollama_results = []
    
    if settings.ollama_api_key:
        try:
            headers = {
                "Authorization": f"Bearer {settings.ollama_api_key}",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    settings.ollama_web_search_url,
                    json=ollama_request_data,
                    headers=headers
                )
                
                if response.status_code == 200:
                    data = response.json()
                    ollama_results = data.get("results", [])
                else:
                    logger.error(f"Ollama Web Search error: {response.text}")
        except Exception as e:
            logger.error(f"Error fetching from Ollama web_search: {e}")
    else:
        logger.warning("OLLAMA_API_KEY is not set. Using mocked fallback results for web search.")
        # Fallback to mock search result (taklit/mock fallback)
        ollama_results = [
            {
                "title": f"Mock Title for: {q} (No API Key set)",
                "url": "https://example.com/mock-search-result",
                "content": "Configure OLLAMA_API_KEY in .env to enable real web search."
            },
            {
                "title": "Ollama",
                "url": "https://ollama.com/",
                "content": "Cloud models are now available..."
            }
        ]
        
    # Transform the Ollama response to match Brave Search API exactly
    # Brave Format: { "type": "search", "web": { "results": [{ "title": "...", "url": "...", "description": "..." }] } }
    brave_results = []
    for item in ollama_results:
        brave_results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "description": item.get("content", ""),
        })
        
    return {
        "type": "search",
        "query": {
            "original": q
        },
        "web": {
            "type": "search",
            "results": brave_results
        }
    }


# ============================================================================
# OpenAI Compatible API Endpoints
# ============================================================================

# Model list cache to avoid hitting nodes on every /v1/models call
_models_cache: Dict[str, Any] = {}
_models_cache_ts: float = 0.0
_MODELS_CACHE_TTL = 30.0  # seconds


@app.get("/v1/models", tags=["OpenAI Compatible API"])
async def openai_list_models(username: str = Depends(get_current_user)):
    """
    List available models in OpenAI format from all healthy nodes
    (filtered by user access and mapped display names)

    Requires JWT authentication
    """
    global _models_cache_ts, _models_cache
    logger.info(f"User {username} requesting OpenAI model list")

    # Use cached response if fresh enough
    import time
    now = time.monotonic()
    if _models_cache and (now - _models_cache_ts) < _MODELS_CACHE_TTL:
        native_models_response = _models_cache
    else:
        # Get all models from all healthy nodes (in Ollama native format)
        from app.node_manager import node_manager

        native_models_response = await node_manager.get_all_models_from_nodes()

        # Cache the response
        _models_cache = native_models_response
        _models_cache_ts = now

    # If no nodes responded, fallback to proxy (single node)
    if not native_models_response.get("models"):
        logger.warning("[ModelList] No models from nodes, falling back to proxy")
        native_models_response = await ollama_proxy.proxy_request(
            method="GET",
            endpoint="/api/tags",
            username=username
        )

    # Convert Ollama native format to OpenAI format
    openai_models = []
    if isinstance(native_models_response, dict) and "models" in native_models_response:
        for model in native_models_response["models"]:
            model_id = model.get("name") or model.get("model")
            if model_id:
                openai_model = {
                    "id": model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": "ollama",
                }
                # Preserve extra fields if present
                for key in ("size", "digest", "details"):
                    if key in model:
                        openai_model[key] = model[key]
                openai_models.append(openai_model)

    all_models_response = {"object": "list", "data": openai_models}

    # Get user's model access
    user_models_data = await user_manager.get_user_models(username)

    # If user_models_data is None, deny access
    if not user_models_data:
        logger.warning(f"User {username} not found or has no model access")
        return {"object": "list", "data": []}

    # Get all mappings from database
    await model_mapper.ensure_loaded()
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
                        # Cursor IDE reads max_model_len to show context usage % and trigger summarization
                        ctx_len = get_context_length_for_model(display_name)
                        if ctx_len:
                            model_copy["max_model_len"] = ctx_len
                        models_dict[display_name] = model_copy

        # Second, add all display names from mappings (even if real model doesn't exist in Ollama)
        for display_name, real_name in all_mappings.items():
            if display_name not in models_dict:
                # Create a synthetic model entry for this display name
                base_model = all_models_response["data"][0] if all_models_response["data"] else {}
                model_entry = base_model.copy() if base_model else {}
                model_entry["id"] = display_name
                # Cursor IDE reads max_model_len to show context usage % and trigger summarization
                ctx_len = get_context_length_for_model(display_name)
                if ctx_len:
                    model_entry["max_model_len"] = ctx_len
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
    
    from app.services import config_manager
    
    # Check if model is in maintenance mode
    if config_manager.is_model_in_maintenance(model_name):
        raise HTTPException(
            status_code=503,
            detail=f"Bu model şu anda bakımdadır: {model_name}"
        )

    # Find unsupported params for this model from database
    unsupported_params = config_manager.get_model_unsupported_params(model_name)
    
    # Remove unsupported parameters for this specific model
    if unsupported_params:
        removed_params = [p for p in unsupported_params if p in data]
        if removed_params:
            data = {k: v for k, v in data.items() if k not in removed_params}
            logger.info(f"Removed {', '.join(removed_params)} for model {model_name} (not supported)")
    
    # Model-specific tool filtering (minimax vb. - Ollama 500 önlemek için)
    if "tools" in data and data["tools"]:
        filtered_tools = filter_tools_for_model(model_name, data["tools"])
        if len(filtered_tools) != len(data["tools"]):
            data["tools"] = filtered_tools
            allowed_names = {t.get("function", {}).get("name") for t in filtered_tools if t.get("type") == "function"}
            # tool_choice: filtrelenmiş listede olmayan bir fonksiyon seçilmişse "auto" yap
            tool_choice = data.get("tool_choice")
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                fn_name = tool_choice.get("function", {}).get("name")
                if fn_name and fn_name not in allowed_names:
                    data["tool_choice"] = "auto"
            logger.info(f"Filtered tools for {model_name}: {len(data['tools'])} tools (reduced set)")
    
    # Remove parameters that Ollama doesn't recognize to avoid parsing errors
    # Fetched from dynamic system configuration
    ollama_unsupported_params = config_manager.get_ollama_unsupported_params()
    
    # Check if any unsupported parameters exist and remove them
    removed_ollama_params = [param for param in ollama_unsupported_params if param in data]
    if removed_ollama_params:
        data = {k: v for k, v in data.items() if k not in removed_ollama_params}
        logger.debug(f"Removed Ollama unsupported parameters: {', '.join(removed_ollama_params)}")
    
    # Ensure stream parameter is set (Cursor might not always send it)
    if 'stream' not in data:
        data['stream'] = stream
    
    # CONTEXT FIX: Streaming'de token kullanımını Cursor'a bildirmek için
    # stream_options inject et (Cursor context % göstergesi için gerekli)
    if stream and 'stream_options' not in data:
        data['stream_options'] = {'include_usage': True}
    
    # CONTEXT FIX: Model bazlı num_ctx ayarla (Ollama varsayılanı 4096 - çok düşük)
    # Bu, context limit hatalarını önler
    ctx_length = get_context_length_for_model(model_name)
    if 'options' not in data:
        data['options'] = {}
    if isinstance(data['options'], dict) and 'num_ctx' not in data['options']:
        data['options']['num_ctx'] = ctx_length
        logger.info(f"Injected num_ctx={ctx_length} for model {model_name}")

    # COLD-START FIX: Modeli VRAM'de tut — bir kez yüklendikten sonra unload etme.
    # Ollama varsayılan keep_alive=5dk; -1 ile server restart'a kadar yüklü kalır.
    if 'keep_alive' not in data:
        data['keep_alive'] = -1
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=data,
        stream=stream,
        username=username
    )


# =============================================================================
# CURSOR-SPECIFIC ENDPOINT
# =============================================================================
# Cursor IDE sometimes sends requests in OpenAI Responses API format (with 'input')
# but expects responses in Chat Completions format.
# This endpoint handles both formats.

@app.post("/cursor/chat/completions", tags=["Cursor IDE"])
async def cursor_chat_completions(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    Cursor IDE specific endpoint
    
    Handles Cursor's unique request format:
    - Accepts requests in OpenAI Responses API format (input field)
    - Returns responses in OpenAI Chat Completions format (choices, delta)
    
    Usage: Set Cursor Base URL to https://your-server/cursor
    Cursor will append /chat/completions automatically
    """
    # Parse request body
    body = await request.body()
    data = json.loads(body.decode('utf-8')) if body else {}
    
    # Transform Responses API format to Chat Completions format if needed
    if 'input' in data and 'messages' not in data:
        logger.debug("Detected Responses API format, transforming to Chat Completions")
        
        # Convert 'input' array to 'messages' array
        input_items = data.get('input', [])
        messages = []
        
        for item in input_items:
            if isinstance(item, dict):
                item_type = item.get('type', 'text')
                
                if item_type == 'text':
                    # Simple text input
                    messages.append({
                        'role': 'user',
                        'content': item.get('text', '')
                    })
                elif item_type == 'message':
                    # Already a message format
                    messages.append({
                        'role': item.get('role', 'user'),
                        'content': item.get('content', '')
                    })
            elif isinstance(item, str):
                # Simple string input
                messages.append({
                    'role': 'user',
                    'content': item
                })
        
        # Replace input with messages
        data['messages'] = messages
        del data['input']
        logger.debug(f"Transformed input to {len(messages)} messages")
    
    model_name = data.get('model', '')
    msg_count = len(data.get('messages', []))
    stream = data.get("stream", True)
    
    logger.info(f"User {username} requesting Cursor chat - model: {model_name}, messages: {msg_count}, stream: {stream}")
    
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
    
    from app.services import config_manager
    
    # Check if model is in maintenance mode
    if config_manager.is_model_in_maintenance(model_name):
        raise HTTPException(
            status_code=503,
            detail=f"Bu model şu anda bakımdadır: {model_name}"
        )

    # Get system-wide and model-specific unsupported parameters
    system_unsupported_params = config_manager.get_ollama_unsupported_params()
    model_unsupported_params = config_manager.get_model_unsupported_params(model_name)
    
    problematic_params = list(set([
        'user', 'n', 'logprobs', 'top_logprobs', 'presence_penalty', 'frequency_penalty',
        'parallel_tool_calls', 'service_tier',
    ] + system_unsupported_params + model_unsupported_params))
    data = {k: v for k, v in data.items() if k not in problematic_params}
    
    # Model-specific tool filtering (minimax vb. - Ollama 500 önlemek için)
    if "tools" in data and data["tools"]:
        filtered_tools = filter_tools_for_model(model_name, data["tools"])
        if len(filtered_tools) != len(data["tools"]):
            data["tools"] = filtered_tools
            allowed_names = {t.get("function", {}).get("name") for t in filtered_tools if t.get("type") == "function"}
            tool_choice = data.get("tool_choice")
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                fn_name = tool_choice.get("function", {}).get("name")
                if fn_name and fn_name not in allowed_names:
                    data["tool_choice"] = "auto"
            logger.info(f"Filtered tools for {model_name}: {len(data['tools'])} tools (reduced set)")
    
    # Ensure stream is set
    data['stream'] = stream
    
    # CONTEXT FIX: Streaming'de token kullanımını Cursor'a bildirmek için
    # stream_options inject et (Cursor context % göstergesi için gerekli)
    if stream and 'stream_options' not in data:
        data['stream_options'] = {'include_usage': True}
    
    # CONTEXT FIX: Model bazlı num_ctx ayarla (Ollama varsayılanı 4096 - çok düşük)
    ctx_length = get_context_length_for_model(model_name)
    if 'options' not in data:
        data['options'] = {}
    if isinstance(data['options'], dict) and 'num_ctx' not in data['options']:
        data['options']['num_ctx'] = ctx_length
        logger.info(f"Injected num_ctx={ctx_length} for model {model_name}")
    
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
