"""Model Maestro - Unified LLM Gateway with JWT Authentication"""

import logging
import secrets
import time
import asyncio
from typing import Any, Dict, List, Optional

# Disable uvicorn access logs immediately
for uv_name in ("uvicorn.access", "uvicorn"):
    logging.getLogger(uv_name).handlers = []
    logging.getLogger(uv_name).propagate = False

from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, Request, HTTPException, status, Query
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
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
    OllamaEmbedRequest,
    OpenAIEmbeddingRequest,
    OpenAIEmbeddingResponse,
    EmbeddingData,
    EmbeddingUsage,
    CompletionRequest,
)
from app.admin import router as admin_router
from app.admin_auth import router as admin_auth_router
from app.admin_config import router as admin_config_router
from app.admin_dashboard import router as admin_dashboard_router
from app.admin_models import router as admin_models_router
from app.admin_nodes import router as admin_nodes_router
from app.admin_groups import router as admin_groups_router
from app.openclaw import router as openclaw_router
from app.claude import router as claude_router
from app.admin_tunnel import router as admin_tunnel_router
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Error-Only Request Log Middleware ====================
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class ErrorOnlyLogMiddleware(BaseHTTPMiddleware):
    """Log only 4xx/5xx requests -- include a short body preview."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        start = time.monotonic()
        receive = request.receive
        buffered_messages: List[dict] = []

        async def receive_with_buffer():
            # Buffer every ASGI message so downstream request.json()
            # still works, while also collecting body bytes for logging.
            msg = await receive()
            buffered_messages.append(msg)
            return msg

        request._receive = receive_with_buffer
        try:
            response = await call_next(request)
        except HTTPException as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._emit(
                request=request,
                status_code=exc.status_code,
                duration_ms=duration_ms,
                buffered_messages=buffered_messages,
                detail=exc.detail,
            )
            raise
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._emit(
                request=request,
                status_code=500,
                duration_ms=duration_ms,
                buffered_messages=buffered_messages,
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        if response.status_code >= 400:
            self._emit(
                request=request,
                status_code=response.status_code,
                duration_ms=duration_ms,
                buffered_messages=buffered_messages,
            )
        return response

    @staticmethod
    def _resolve_username_from_request(request: Request) -> Optional[str]:
        auth = request.headers.get("authorization", "")
        if len(auth) > 7 and auth[:7].lower() == "bearer ":
            token = auth[7:]
            try:
                import jwt as _jwt
                payload = _jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
                return payload.get("sub")
            except Exception:
                return None
        return None

    @staticmethod
    def _infer_source_from_request(request: Request) -> Optional[str]:
        agent = request.headers.get("user-agent", "")
        path = request.url.path.lower()
        if "cursor" in agent.lower():
            return "Cursor"
        if "antigravity" in agent.lower():
            return "Antigravity"
        if "openclaw" in agent.lower():
            return "OpenClaw"
        if "claude" in agent.lower():
            return "Claude"
        if "/openclaw/" in path:
            return "OpenClaw"
        if "/claude/" in path:
            return "Claude"
        if "/api/chat" in path or "/v1/chat/completions" in path:
            return "OpenAI-Compatible"
        if "/api/generate" in path:
            return "Ollama Native"
        return None

    @staticmethod
    def _infer_request_type(url_path: str) -> str:
        if "/chat/completions" in url_path:
            return "chat"
        if "/completions" in url_path:
            return "completions"
        if "/embeddings" in url_path or "/embed" in url_path:
            return "embeddings"
        if "/generate" in url_path:
            return "generate"
        if "/api/tags" in url_path:
            return "tags"
        if "/api/ps" in url_path:
            return "ps"
        return url_path

    def _emit(
        self,
        request: Request,
        status_code: int,
        duration_ms: int,
        buffered_messages: List[dict],
        detail: str = "",
    ):
        # Reassemble body from buffered ASGI messages
        body_parts: List[bytes] = []
        for msg in buffered_messages:
            if msg.get("type") == "http.request":
                chunk = msg.get("body", b"")
                if chunk:
                    body_parts.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
                if not msg.get("more_body", False):
                    break
        body_bytes = b"".join(body_parts)
        # Extract model name from body if available
        body_json: Optional[Dict[str, Any]] = None
        if body_bytes:
            try:
                body_json = json.loads(body_bytes)
            except Exception:
                body_json = None

        msg_lines = [
            f"Error {status_code} {request.method} {request.url.path}",
            f"  duration={duration_ms}ms",
        ]
        if body_json and body_json.get("model"):
            msg_lines.append(f"  model={body_json.get('model')}")
        if detail:
            msg_lines.append(f"  detail={detail[:200]!r}")
        if status_code >= 500:
            logger.error("\n".join(msg_lines))
        else:
            logger.warning("\n".join(msg_lines))

        # Persist to Request Logs (async fire-and-forget)
        username = self._resolve_username_from_request(request)
        model_name = body_json.get("model") if body_json else None
        source = self._infer_source_from_request(request)
        request_type = self._infer_request_type(str(request.url.path))
        try:
            from app.background_tasks import queue_activity_log_async
            asyncio.create_task(
                queue_activity_log_async(
                    username=username or "anonymous",
                    model_name=model_name or "unknown",
                    request_type=request_type,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    error_message=detail or f"HTTP {status_code}",
                    source=source,
                    url_path=str(request.url.path),
                )
            )
        except Exception as e:
            logger.debug(f"Failed to queue error activity log: {e}")


app.add_middleware(ErrorOnlyLogMiddleware)


# Include admin routers (auth first - login has no auth)
app.include_router(admin_auth_router)
app.include_router(admin_router)
app.include_router(admin_config_router)
app.include_router(admin_dashboard_router)
app.include_router(admin_models_router)
app.include_router(admin_nodes_router)
app.include_router(admin_groups_router)
app.include_router(openclaw_router)
app.include_router(claude_router)
app.include_router(admin_tunnel_router)

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

    # Ensure all default config keys exist in DB (auto-seed new ones)
    await config_manager.ensure_defaults()
    logger.info("Default configuration keys ensured")
    
    # Initialize Google OAuth manager if credentials are available
    from app.config import get_settings
    settings = get_settings()
    if settings.google_client_id and settings.google_client_secret:
        from app.google_auth import init_google_oauth
        init_google_oauth(
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
        )
        logger.info("Google OAuth manager initialized")

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
            username=username,
            source="Ollama Native",
            url_path="/api/tags"
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

                if display_names:
                    for display_name in display_names:
                        if display_name not in models_dict:
                            model_copy = model.copy()
                            model_copy["name"] = display_name
                            model_copy["model"] = display_name
                            models_dict[display_name] = model_copy
                else:
                    # No mapping for this model – add with its original name
                    if model_name not in models_dict:
                        models_dict[model_name] = model

        mapped_models = list(models_dict.values())

        # Filter models based on user access (using display names)
        if user_models_data["has_all_models"]:
            filtered_models = mapped_models
        else:
            allowed_models = set(user_models_data["models"])
            filtered_models = [
                model for model in mapped_models
                if model.get("name") in allowed_models or model.get("model") in allowed_models
            ]

        from app.model_list import (
            append_groups_to_ollama_models,
            get_visible_catalog_group_names,
        )

        catalog_groups = await get_visible_catalog_group_names(user_models_data)
        return {"models": append_groups_to_ollama_models(filtered_models, catalog_groups)}

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
        username=username,
        source="Ollama Native",
        url_path="/api/generate"
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
        username=username,
        source="Ollama Native",
        url_path="/api/chat"
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
        username=username,
        source="Ollama Native",
        url_path="/api/embeddings"
    )


@app.post("/api/embed", tags=["Ollama Native API"])
async def embed(
    request: OllamaEmbedRequest,
    username: str = Depends(get_current_user)
):
    """
    Generate embeddings for text(s) using a model.

    Native Ollama /api/embed endpoint. Accepts a single text or a list of texts.

    Requires JWT authentication and model access
    """
    logger.info(f"User {username} requesting embed with model {request.model}")

    # Check model access
    has_access = await check_model_access(username, request.model)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {request.model}"
        )

    # Check user limits BEFORE making request to Ollama
    within_limits = await ollama_proxy.check_user_limits(username, "embed")
    if not within_limits:
        raise HTTPException(
            status_code=429,
            detail="User has exceeded their request or token limit"
        )

    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/embed",
        data=request.model_dump(exclude_none=True),
        username=username,
        source="Ollama Native",
        url_path="/api/embed"
    )


@app.post("/v1/embeddings", tags=["OpenAI Compatible API"])
async def openai_embeddings(
    request: OpenAIEmbeddingRequest,
    username: str = Depends(get_current_user)
):
    """
    OpenAI-compatible embeddings endpoint.

    Accepts single text or list of texts and returns embeddings
    in the standard OpenAI /v1/embeddings format.
    """
    logger.info(f"User {username} requesting OpenAI embeddings with model {request.model}")

    # Check model access
    has_access = await check_model_access(username, request.model)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {request.model}"
        )

    # Check user limits BEFORE making request
    within_limits = await ollama_proxy.check_user_limits(username, "embeddings")
    if not within_limits:
        raise HTTPException(
            status_code=429,
            detail="User has exceeded their request or token limit"
        )

    response = await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/embeddings",
        data=request.model_dump(exclude_none=True),
        username=username,
        source="OpenAI-Compatible",
        url_path="/v1/embeddings"
    )

    return response


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
        username=username,
        source="Ollama Native",
        url_path="/api/show"
    )


# ============================================================================
# Brave Search Compatible Endpoint
# ============================================================================
# Forwards web search requests to the configured backend (Ollama Web Search,
# DuckDuckGo, SerpAPI, etc.) and returns results in Brave Search API format.
# Used by OpenClaw and other clients that expect Brave Search semantics.

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


@app.get("/res/v1/web/search", tags=["Brave Search"])
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

    from app.services import config_manager
    await config_manager.ensure_loaded()

    web_search_url = config_manager.get("search.web_search_url", "https://ollama.com/api/web_search")
    web_search_api_key = config_manager.get("search.web_search_api_key", "")

    # Format the request exactly as Ollama Cloud expects
    ollama_request_data = {"query": q}
    ollama_results = []

    if web_search_api_key:
        try:
            headers = {
                "Authorization": f"Bearer {web_search_api_key}",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    web_search_url,
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
        logger.warning("search.web_search_api_key is not set. Using mocked fallback results for web search.")
        # Fallback to mock search result (taklit/mock fallback)
        ollama_results = [
            {
                "title": f"Mock Title for: {q} (No API Key set)",
                "url": "https://example.com/mock-search-result",
                "content": "Configure search.web_search_api_key in Settings to enable real web search."
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
            username=username,
            source="OpenAI-Compatible",
            url_path="/v1/models",
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
        available_real_names: set[str] = set()
        for model in all_models_response["data"]:
            model_id = model.get("id")
            if model_id:
                available_real_names.add(model_id)
                # Get ALL display names for this real model
                display_names = model_mapper.get_all_display_names_for_real_name(model_id)

                if display_names:
                    for display_name in display_names:
                        if display_name not in models_dict:
                            model_copy = model.copy()
                            model_copy["id"] = display_name
                            # Cursor IDE reads max_model_len to show context usage % and trigger summarization
                            ctx_len = get_context_length_for_model(display_name)
                            if ctx_len:
                                model_copy["max_model_len"] = ctx_len
                            models_dict[display_name] = model_copy
                else:
                    # No mapping for this model – add with its original id
                    if model_id not in models_dict:
                        models_dict[model_id] = model

        # Second, add display names from mappings only if the real model is currently available
        for display_name, real_name in all_mappings.items():
            if display_name not in models_dict and real_name in available_real_names:
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
            filtered_models = mapped_models
        else:
            allowed_models = set(user_models_data["models"])
            filtered_models = [
                model for model in mapped_models
                if model.get("id") in allowed_models
            ]

        from app.model_list import (
            append_groups_to_openai_models,
            get_visible_catalog_group_names,
        )

        catalog_groups = await get_visible_catalog_group_names(user_models_data)
        return {
            "object": all_models_response.get("object", "list"),
            "data": append_groups_to_openai_models(filtered_models, catalog_groups),
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

    # Forward client headers (Cookie, x-*, etc.) to upstream
    # Cookie is excluded — the client cookie belongs to model-maestro, not upstream.
    # accept and content-type are excluded — httpx sends them automatically.
    skip_headers = {'host', 'content-length', 'transfer-encoding', 'connection', 'accept-encoding', 'cookie', 'accept', 'content-type'}
    client_headers = {k: v for k, v in request.headers.items() if k.lower() not in skip_headers}

    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=data,
        stream=stream,
        username=username,
        client_headers=client_headers,
        source="OpenAI-Compatible",
        url_path="/v1/chat/completions"
    )


# =============================================================================
# OPENAI RESPONSES API ENDPOINT (for Codex Desktop App)
# =============================================================================
# The Codex Desktop App uses OpenAI's Responses API (POST /v1/responses)
# rather than Chat Completions. We accept in Responses format, convert
# to Chat Completions internally, and convert the response back.

@app.post("/v1/responses", tags=["OpenAI Compatible API"])
async def openai_responses(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    OpenAI Responses API compatible endpoint.

    Accepts OpenAI Responses API request format (used by Codex Desktop App):
    - input: list of content items or messages
    - model: model identifier
    - reasoning, tools, temperature, etc.

    Converts to Chat Completions internally and returns Responses format.
    Streaming not yet supported (returned as full response).
    """
    body = await request.body()
    req = json.loads(body.decode('utf-8')) if body else {}

    model_name = req.get('model', '')
    logger.info(f"User {username} requesting Responses API - model: {model_name}")

    # Check model access
    has_access = await check_model_access(username, model_name)
    if not has_access:
        raise HTTPException(status_code=403, detail=f"Bu modele erişim yetkiniz yok: {model_name}")

    within_limits = await ollama_proxy.check_user_limits(username, "chat")
    if not within_limits:
        raise HTTPException(status_code=429, detail="User has exceeded their request or token limit")

    from app.services import config_manager
    if config_manager.is_model_in_maintenance(model_name):
        raise HTTPException(status_code=503, detail=f"Bu model şu anda bakımdadır: {model_name}")

    # Convert Responses API request to Chat Completions request
    data = _responses_to_chat_completions(req)

    # Remove unsupported params for this model
    unsupported_params = config_manager.get_model_unsupported_params(model_name)
    if unsupported_params:
        removed = [p for p in unsupported_params if p in data]
        if removed:
            data = {k: v for k, v in data.items() if k not in removed}
            logger.info(f"Removed {', '.join(removed)} for model {model_name}")

    # Tool filtering
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

    # Ollama unsupported params
    ollama_unsupported = config_manager.get_ollama_unsupported_params()
    removed_ollama = [p for p in ollama_unsupported if p in data]
    if removed_ollama:
        data = {k: v for k, v in data.items() if k not in ollama_unsupported}

    # Inject context length and keep_alive
    ctx_length = get_context_length_for_model(model_name)
    if 'options' not in data:
        data['options'] = {}
    if isinstance(data['options'], dict) and 'num_ctx' not in data['options']:
        data['options']['num_ctx'] = ctx_length
    if 'keep_alive' not in data:
        data['keep_alive'] = -1

    # Client headers
    skip_headers = {'host', 'content-length', 'transfer-encoding', 'connection', 'accept-encoding', 'cookie', 'accept', 'content-type'}
    client_headers = {k: v for k, v in request.headers.items() if k.lower() not in skip_headers}

    # Call chat completions internally (non-streaming)
    data['stream'] = False
    if 'stream_options' in data:
        del data['stream_options']

    raw_response = await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=data,
        stream=False,
        username=username,
        client_headers=client_headers,
        source="Codex-Desktop-Responses",
        url_path="/v1/responses"
    )

    # Convert Chat Completions response to Responses API format
    return _chat_completions_to_responses(raw_response, model_name)


def _responses_to_chat_completions(req: dict) -> dict:
    """Convert OpenAI Responses API request to Chat Completions format."""
    data = {}

    # Copy standard params
    for key in ['model', 'temperature', 'top_p', 'max_tokens', 'max_completion_tokens',
                'frequency_penalty', 'presence_penalty', 'stop', 'seed', 'response_format',
                'parallel_tool_calls', 'store', 'metadata', 'service_tier']:
        if key in req:
            data[key] = req[key]

    # Convert 'input' to 'messages'
    input_items = req.get('input', [])
    messages = []
    system_content = None

    if isinstance(input_items, list):
        for item in input_items:
            if isinstance(item, dict):
                item_type = item.get('type', '')
                if item_type == 'text' or item_type == 'input_text':
                    messages.append({'role': 'user', 'content': item.get('text', '')})
                elif item_type == 'message':
                    role = item.get('role', 'user')
                    content = item.get('content', [])
                    # Handle content as string or array
                    if isinstance(content, str):
                        messages.append({'role': role, 'content': content})
                    elif isinstance(content, list):
                        # Content items (text, image, etc.)
                        text_parts = []
                        for c in content:
                            if isinstance(c, dict):
                                if c.get('type') == 'text':
                                    text_parts.append(c.get('text', ''))
                                elif 'text' in c:
                                    text_parts.append(c['text'])
                        if text_parts:
                            messages.append({'role': role, 'content': '\n'.join(text_parts)})
                    else:
                        messages.append({'role': role, 'content': str(content)})
                elif item_type == 'input_image':
                    # Images not supported in chat completions directly; skip
                    pass
            elif isinstance(item, str):
                messages.append({'role': 'user', 'content': item})
    elif isinstance(input_items, str):
        messages.append({'role': 'user', 'content': input_items})

    # Handle 'instructions' as system prompt
    if 'instructions' in req and isinstance(req['instructions'], str):
        system_content = req['instructions']

    if system_content:
        messages.insert(0, {'role': 'system', 'content': system_content})

    data['messages'] = messages

    # Convert tools
    if 'tools' in req:
        data['tools'] = req['tools']
    if 'tool_choice' in req:
        data['tool_choice'] = req['tool_choice']

    # Convert reasoning to reasoning_effort
    if 'reasoning' in req and isinstance(req['reasoning'], dict):
        effort = req['reasoning'].get('effort')
        if effort:
            data['reasoning_effort'] = effort

    return data


def _chat_completions_to_responses(cc_response, model_name: str) -> dict:
    """Convert Chat Completions response to OpenAI Responses API format."""
    # Handle different response types (dict, JSONResponse, str, etc.)
    cc: dict = {}
    if isinstance(cc_response, dict):
        cc = cc_response
    elif isinstance(cc_response, str):
        try:
            cc = json.loads(cc_response)
        except (json.JSONDecodeError, ValueError):
            cc = {}
    elif hasattr(cc_response, 'body'):
        body = cc_response.body
        if isinstance(body, bytes):
            cc = json.loads(body.decode('utf-8'))
        elif hasattr(body, 'decode'):
            cc = json.loads(body.decode())
        else:
            cc = json.loads(body)
    else:
        cc = {}

    # Build response
    choice = cc.get('choices', [{}])[0] if cc.get('choices') else {}
    message = choice.get('message', {}) if choice else {}

    # Extract text content
    content_text = ''
    if isinstance(message.get('content'), str):
        content_text = message['content']

    # Extract tool calls
    tool_calls = message.get('tool_calls', [])

    # Build output items
    output_items = []

    if content_text:
        output_items.append({
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'output_text', 'text': content_text}]
        })

    if tool_calls:
        for tc in tool_calls:
            output_items.append({
                'type': 'function_call',
                'id': tc.get('id', ''),
                'call_id': tc.get('id', ''),
                'name': tc.get('function', {}).get('name', ''),
                'arguments': tc.get('function', {}).get('arguments', '{}')
            })

    # If no output, add empty message
    if not output_items:
        output_items.append({
            'type': 'message',
            'role': 'assistant',
            'content': []
        })

    # Usage
    usage = cc.get('usage', {})

    # Build final response
    response = {
        'id': cc.get('id', ''),
        'object': 'response',
        'created': cc.get('created', int(time.time())),
        'model': model_name,
        'status': 'completed',
        'output': output_items,
        'usage': {
            'input_tokens': usage.get('prompt_tokens', 0),
            'output_tokens': usage.get('completion_tokens', 0),
            'total_tokens': usage.get('total_tokens', 0)
        }
    }

    # Add optional fields if present
    if 'system_fingerprint' in cc:
        response['system_fingerprint'] = cc['system_fingerprint']

    return response


# =============================================================================
# CODEX DESKTOP APP ENDPOINTS
# =============================================================================
# The Codex Desktop App (like Ollama launch) uses a dedicated provider config
# with profile + model_catalog_json + model_provider. These endpoints mirror
# the OpenAI API but under /codex/ prefix so the app sees Maestro as a native
# provider (wire_api = "responses").

@app.get("/codex/models", tags=["Codex Desktop App"])
async def codex_list_models(username: str = Depends(get_current_user)):
    """
    Return models in Codex Desktop App catalog format.

    Same as /v1/models but with the rich Codex model metadata needed for
    model_catalog_json (base_instructions, context_window, capabilities, etc.)
    """
    logger.info(f"User {username} requesting Codex model catalog")

    # Get models from /v1/models endpoint (already filtered by user access)
    openai_models = await openai_list_models(username)
    data = openai_models.get("data", [])

    # Build Codex catalog format
    codex_models = []
    for idx, m in enumerate(data):
        model_id = m.get("id", "")
        # Skip group entries (they don't have real endpoints)
        if not model_id or model_id.startswith("group-"):
            continue

        # Get context length from model config
        ctx = get_context_length_for_model(model_id)

        # Determine capabilities
        input_modalities = ["text"]
        supports_image = False
        # Simple heuristic for vision support
        vision_keywords = ["vision", "kimi", "claude", "gpt-4", "glm", "gemini"]
        if any(kw in model_id.lower() for kw in vision_keywords):
            input_modalities = ["text", "image"]
            supports_image = True

        codex_models.append({
            "id": model_id,
            "name": model_id,
            "object": "model",
            "created": 0,
            "owned_by": "maestro",
            "context_window": ctx,
            "max_context_window": ctx,
            "effective_context_window_percent": 95,
            "description": f"Maestro model: {model_id}",
            "display_name": m.get("name", model_id),
            "slug": model_id,
            "priority": idx,
            "shell_type": "default",
            "input_modalities": input_modalities,
            "supported_in_api": True,
            "supports_image_detail_original": supports_image,
            "supports_parallel_tool_calls": False,
            "supports_reasoning_summaries": False,
            "supports_search_tool": False,
            "support_verbosity": False,
            "truncation_policy": {"limit": 10000, "mode": "bytes"},
            "default_reasoning_summary": "auto",
            "visibility": "list",
            "web_search_tool_type": "text",
            "additional_speed_tiers": [],
            "apply_patch_tool_type": None,
            "auto_compact_token_limit": None,
            "availability_nux": None,
            "base_instructions": "You are Codex, a helpful coding assistant.",
            "default_reasoning_level": None,
            "default_verbosity": None,
            "experimental_supported_tools": [],
            "model_messages": None,
            "supported_reasoning_levels": [],
            "upgrade": None,
        })

    return {"models": codex_models}


@app.post("/codex/responses", tags=["Codex Desktop App"])
async def codex_responses(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    Codex Desktop App Responses API endpoint.

    Converts Responses API input payload to Chat Completions,
    proxies through Model Maestro's load-balanced proxy,
    and streams back Response Streaming SSE formatted messages.
    """
    import uuid
    body = await request.body()
    data = json.loads(body.decode('utf-8')) if body else {}

    model_name = data.get('model', '')
    logger.info(f"User {username} requesting Codex Responses - model: {model_name}")
    logger.debug(f"[Codex] Received raw payload from client: {json.dumps(data)}")

    has_access = await check_model_access(username, model_name)
    if not has_access:
        raise HTTPException(status_code=403, detail=f"Bu modele erişim yetkiniz yok: {model_name}")

    within_limits = await ollama_proxy.check_user_limits(username, "chat")
    if not within_limits:
        raise HTTPException(status_code=429, detail="User has exceeded their request or token limit")

    from app.services import config_manager
    if config_manager.is_model_in_maintenance(model_name):
        raise HTTPException(status_code=503, detail=f"Bu model şu anda bakımdadır: {model_name}")

    # Step 1: Convert Responses API to Chat Completions format
    chat_data = {
        "model": model_name,
        "stream": True,
    }

    # Messages conversion
    messages = []

    # Instructions -> system message
    instructions = data.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    # Input mapping
    raw_input = data.get("input")
    logger.info(f"[Codex] Input type status: type={type(raw_input).__name__}, items={len(raw_input) if isinstance(raw_input, list) else 1}")
    if raw_input:
        if isinstance(raw_input, str):
            messages.append({"role": "user", "content": raw_input})
        elif isinstance(raw_input, list):
            for item in raw_input:
                item_type = item.get("type", "message")

                # Check for historical messages
                if item_type == "message" or "role" in item:
                    role = item.get("role")
                    content = item.get("content")
                    # If content is list of parts (like text, image)
                    if isinstance(content, list):
                        mapped_parts = []
                        for part in content:
                            part_type = part.get("type")
                            if part_type in ("input_text", "text", "output_text"):
                                mapped_parts.append({"type": "text", "text": part.get("text", "")})
                            elif part_type in ("input_image", "image"):
                                image_url = part.get("image_url", {})
                                mapped_parts.append({"type": "image_url", "image_url": image_url})
                        messages.append({"role": role, "content": mapped_parts})
                    else:
                        # plain string content
                        messages.append({"role": role, "content": content or ""})

                elif item_type == "function_call":
                    # Tool call history representation in chat history
                    tool_calls = [{
                        "id": item.get("call_id", f"call_{uuid.uuid4().hex[:12]}"),
                        "type": "function",
                        "function": {
                            "name": item.get("name"),
                            "arguments": item.get("arguments")
                        }
                    }]
                    # Find last assistant message or merge
                    if messages and messages[-1]["role"] == "assistant":
                        if "tool_calls" not in messages[-1]:
                            messages[-1]["tool_calls"] = []
                        messages[-1]["tool_calls"].extend(tool_calls)
                    else:
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": tool_calls
                        })

                elif item_type == "function_call_output":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": item.get("call_id"),
                        "content": item.get("output", "")
                    })

    chat_data["messages"] = messages

    # Tools mapping
    tools = data.get("tools")
    if tools:
        mapped_tools = []
        for t in tools:
            mapped_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {})
                }
            })
        chat_data["tools"] = mapped_tools
        if "tool_choice" in data:
            chat_data["tool_choice"] = data["tool_choice"]

    # Forward parameters
    if "temperature" in data:
        chat_data["temperature"] = data["temperature"]
    if "top_p" in data:
        chat_data["top_p"] = data["top_p"]
    if "max_output_tokens" in data:
        chat_data["max_tokens"] = data["max_output_tokens"]

    # Filter unsupported / system parameters like standard chat completions
    unsupported_params = config_manager.get_model_unsupported_params(model_name)
    if unsupported_params:
        removed_params = [p for p in unsupported_params if p in chat_data]
        if removed_params:
            chat_data = {k: v for k, v in chat_data.items() if k not in removed_params}

    # Remove parameters that Ollama doesn't recognize
    ollama_unsupported_params = config_manager.get_ollama_unsupported_params()
    removed_ollama_params = [param for param in ollama_unsupported_params if param in chat_data]
    if removed_ollama_params:
        chat_data = {k: v for k, v in chat_data.items() if k not in removed_ollama_params}

    # Inject stream and steam options
    chat_data['stream'] = True
    chat_data['stream_options'] = {'include_usage': True}

    ctx_length = get_context_length_for_model(model_name)
    if 'options' not in chat_data:
        chat_data['options'] = {}
    if isinstance(chat_data['options'], dict) and 'num_ctx' not in chat_data['options']:
        chat_data['options']['num_ctx'] = ctx_length

    if 'keep_alive' not in chat_data:
        chat_data['keep_alive'] = -1

    skip_headers = {'host', 'content-length', 'transfer-encoding', 'connection', 'accept-encoding', 'cookie', 'accept', 'content-type'}
    client_headers = {k: v for k, v in request.headers.items() if k.lower() not in skip_headers}

    # Step 2: Make request to Model Maestro's load-balanced proxy
    response = await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=chat_data,
        stream=True,
        username=username,
        client_headers=client_headers,
        source="Codex-Desktop"
    )

    if not isinstance(response, StreamingResponse):
        # If proxy returned a normal response (likely error), return it directly
        return response

    async def responses_stream_converter():
        response_id = f"resp_{uuid.uuid4().hex[:16]}"
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        reasoning_item_id = f"rs_{uuid.uuid4().hex[:16]}"

        seq_num = 1

        reasoning_started = False
        reasoning_done = False
        message_started = False
        message_done = False

        text_content = ""
        reasoning_content = ""

        # Tools representation
        tool_call_started = False
        tool_call_items = []
        active_tool_index = -1

        # Usage stats
        usage_input_tokens = 0
        usage_output_tokens = 0

        # Init base response object
        response_obj = {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "completed_at": None,
            "status": "in_progress",
            "model": model_name,
            "instructions": data.get("instructions"),
            "background": False,
            "error": None,
            "incomplete_details": None,
            "max_tool_calls": None,
            "previous_response_id": None,
            "prompt_cache_key": None,
            "reasoning": None,
            "safety_identifier": None,
            "service_tier": "default",
            "store": False,
            "text": {"format": {"type": "text"}},
            "top_logprobs": 0,
            "output": [],
            "tools": data.get("tools", []),
            "tool_choice": data.get("tool_choice", "auto"),
            "truncation": data.get("truncation", "disabled"),
            "temperature": data.get("temperature", 1.0),
            "top_p": data.get("top_p", 1.0),
            "presence_penalty": 0,
            "frequency_penalty": 0,
            "max_output_tokens": data.get("max_output_tokens"),
            "parallel_tool_calls": True,
            "metadata": {},
            "usage": None
        }

        try:
            # 1. response.created
            created_event = {
                "type": "response.created",
                "sequence_number": seq_num,
                "response": response_obj
            }
            logger.info(f"[Codex] Emitting response.created (seq={seq_num})")
            yield f"event: response.created\ndata: {json.dumps(created_event)}\n\n".encode('utf-8')
            seq_num += 1

            # 2. response.in_progress
            in_progress_event = {
                "type": "response.in_progress",
                "sequence_number": seq_num,
                "response": response_obj
            }
            logger.info(f"[Codex] Emitting response.in_progress (seq={seq_num})")
            yield f"event: response.in_progress\ndata: {json.dumps(in_progress_event)}\n\n".encode('utf-8')
            seq_num += 1

            buffer = ""
            done_received = False
            # Read from source stream
            async for raw_chunk in response.body_iterator:
                if done_received:
                    break

                if isinstance(raw_chunk, bytes):
                    buffer += raw_chunk.decode('utf-8', errors='ignore')
                else:
                    buffer += raw_chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue

                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        done_received = True
                        break

                    try:
                        chunk_json = json.loads(data_str)
                        choices = chunk_json.get("choices", [])
                        usage = chunk_json.get("usage")
                        if usage:
                            usage_input_tokens = usage.get("prompt_tokens", 0)
                            usage_output_tokens = usage.get("completion_tokens", 0)

                        if not choices:
                            continue

                        choice = choices[0]
                        delta = choice.get("delta", {})

                        # ── Option A: Reasoning (Thinking) content ───────────────────
                        reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning_delta and not reasoning_done:
                            if not reasoning_started:
                                reasoning_started = True
                                added_event = {
                                    "type": "response.output_item.added",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item": {
                                        "id": reasoning_item_id,
                                        "type": "reasoning",
                                        "status": "in_progress",
                                        "summary": []
                                    }
                                }
                                yield f"event: response.output_item.added\ndata: {json.dumps(added_event)}\n\n".encode('utf-8')
                                seq_num += 1

                            reasoning_content += reasoning_delta
                            delta_event = {
                                "type": "response.reasoning_summary_text.delta",
                                "sequence_number": seq_num,
                                "output_index": 0,
                                "item_id": reasoning_item_id,
                                "summary_index": 0,
                                "delta": reasoning_delta
                            }
                            yield f"event: response.reasoning_summary_text.delta\ndata: {json.dumps(delta_event)}\n\n".encode('utf-8')
                            seq_num += 1
                            continue

                        # ── Option B: Functions/Tools call ───────────────────────────
                        tool_calls = delta.get("tool_calls")
                        if tool_calls:
                            # If reasoning was active and not closed, close it first
                            if reasoning_started and not reasoning_done:
                                reasoning_done = True
                                done_event1 = {
                                    "type": "response.reasoning_summary_text.done",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item_id": reasoning_item_id,
                                    "summary_index": 0,
                                    "text": reasoning_content
                                }
                                yield f"event: response.reasoning_summary_text.done\ndata: {json.dumps(done_event1)}\n\n".encode('utf-8')
                                seq_num += 1

                                done_event2 = {
                                    "type": "response.output_item.done",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item": {
                                        "id": reasoning_item_id,
                                        "type": "reasoning",
                                        "status": "completed",
                                        "summary": [{"type": "summary_text", "text": reasoning_content}],
                                        "encrypted_content": reasoning_content
                                    }
                                }
                                yield f"event: response.output_item.done\ndata: {json.dumps(done_event2)}\n\n".encode('utf-8')
                                seq_num += 1

                            # Handle tool call events
                            tc = tool_calls[0]
                            tc_id = tc.get("id")
                            tc_func = tc.get("function", {})
                            tc_name = tc_func.get("name")
                            tc_args = tc_func.get("arguments", "")

                            if tc_id:
                                # New tool call starts
                                tool_call_started = True
                                active_tool_index = len(tool_call_items)
                                tc_item_id = f"fc_{response_id}_{active_tool_index}"
                                tool_call_items.append({
                                    "id": tc_item_id,
                                    "type": "function_call",
                                    "status": "in_progress",
                                    "call_id": tc_id,
                                    "name": tc_name or "",
                                    "arguments": tc_args or ""
                                })

                                added_event = {
                                    "type": "response.output_item.added",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item": tool_call_items[active_tool_index]
                                }
                                yield f"event: response.output_item.added\ndata: {json.dumps(added_event)}\n\n".encode('utf-8')
                                seq_num += 1

                            if tc_args and active_tool_index >= 0:
                                tool_call_items[active_tool_index]["arguments"] += tc_args
                                delta_event = {
                                    "type": "response.function_call_arguments.delta",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item_id": tool_call_items[active_tool_index]["id"],
                                    "delta": tc_args
                                }
                                yield f"event: response.function_call_arguments.delta\ndata: {json.dumps(delta_event)}\n\n".encode('utf-8')
                                seq_num += 1
                            continue

                        # ── Option C: Message Text Content ───────────────────────────
                        text_delta = delta.get("content")
                        if text_delta:
                            # If reasoning was active and not closed, close it first
                            if reasoning_started and not reasoning_done:
                                reasoning_done = True
                                done_event1 = {
                                    "type": "response.reasoning_summary_text.done",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item_id": reasoning_item_id,
                                    "summary_index": 0,
                                    "text": reasoning_content
                                }
                                yield f"event: response.reasoning_summary_text.done\ndata: {json.dumps(done_event1)}\n\n".encode('utf-8')
                                seq_num += 1

                                done_event2 = {
                                    "type": "response.output_item.done",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item": {
                                        "id": reasoning_item_id,
                                        "type": "reasoning",
                                        "status": "completed",
                                        "summary": [{"type": "summary_text", "text": reasoning_content}],
                                        "encrypted_content": reasoning_content
                                    }
                                }
                                yield f"event: response.output_item.done\ndata: {json.dumps(done_event2)}\n\n".encode('utf-8')
                                seq_num += 1

                            # If first message content, add output item and first content part
                            if not message_started:
                                message_started = True
                                added_event = {
                                    "type": "response.output_item.added",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item": {
                                        "id": message_id,
                                        "type": "message",
                                        "status": "in_progress",
                                        "role": "assistant",
                                        "content": []
                                    }
                                }
                                yield f"event: response.output_item.added\ndata: {json.dumps(added_event)}\n\n".encode('utf-8')
                                seq_num += 1

                                part_added_event = {
                                    "type": "response.content_part.added",
                                    "sequence_number": seq_num,
                                    "output_index": 0,
                                    "item_id": message_id,
                                    "content_index": 0,
                                    "part": {
                                        "type": "output_text",
                                        "text": "",
                                        "annotations": [],
                                        "logprobs": []
                                    }
                                }
                                yield f"event: response.content_part.added\ndata: {json.dumps(part_added_event)}\n\n".encode('utf-8')
                                seq_num += 1

                            text_content += text_delta
                            delta_event = {
                                "type": "response.output_text.delta",
                                "sequence_number": seq_num,
                                "output_index": 0,
                                "item_id": message_id,
                                "content_index": 0,
                                "delta": text_delta,
                                "logprobs": []
                            }
                            yield f"event: response.output_text.delta\ndata: {json.dumps(delta_event)}\n\n".encode('utf-8')
                            seq_num += 1
                    except Exception as e:
                        logger.error(f"Error parsing OpenAI stream chunk line: {line}. Error: {e}")

            # ── Step 3: Stream finalized, wrap up remaining done/completed events ─────
            # A. Resolve active reasoning if not completed
            if reasoning_started and not reasoning_done:
                reasoning_done = True
                done_event1 = {
                    "type": "response.reasoning_summary_text.done",
                    "sequence_number": seq_num,
                    "output_index": 0,
                    "item_id": reasoning_item_id,
                    "summary_index": 0,
                    "text": reasoning_content
                }
                yield f"event: response.reasoning_summary_text.done\ndata: {json.dumps(done_event1)}\n\n".encode('utf-8')
                seq_num += 1

                done_event2 = {
                    "type": "response.output_item.done",
                    "sequence_number": seq_num,
                    "output_index": 0,
                    "item": {
                        "id": reasoning_item_id,
                        "type": "reasoning",
                        "status": "completed",
                        "summary": [{"type": "summary_text", "text": reasoning_content}],
                        "encrypted_content": reasoning_content
                    }
                }
                yield f"event: response.output_item.done\ndata: {json.dumps(done_event2)}\n\n".encode('utf-8')
                seq_num += 1

            # B. Resolve active tool calls
            if tool_call_started:
                for idx, item in enumerate(tool_call_items):
                    item["status"] = "completed"
                    done_args_event = {
                        "type": "response.function_call_arguments.done",
                        "sequence_number": seq_num,
                        "output_index": 0,
                        "item_id": item["id"],
                        "arguments": item["arguments"]
                    }
                    yield f"event: response.function_call_arguments.done\ndata: {json.dumps(done_args_event)}\n\n".encode('utf-8')
                    seq_num += 1

                    done_item_event = {
                        "type": "response.output_item.done",
                        "sequence_number": seq_num,
                        "output_index": 0,
                        "item": item
                    }
                    yield f"event: response.output_item.done\ndata: {json.dumps(done_item_event)}\n\n".encode('utf-8')
                    seq_num += 1

            # C. Resolve active text message
            if message_started and not message_done:
                message_done = True
                done_text_event = {
                    "type": "response.output_text.done",
                    "sequence_number": seq_num,
                    "output_index": 0,
                    "item_id": message_id,
                    "content_index": 0,
                    "text": text_content,
                    "logprobs": []
                }
                yield f"event: response.output_text.done\ndata: {json.dumps(done_text_event)}\n\n".encode('utf-8')
                seq_num += 1

                done_part_event = {
                    "type": "response.content_part.done",
                    "sequence_number": seq_num,
                    "output_index": 0,
                    "item_id": message_id,
                    "content_index": 0,
                    "part": {
                        "type": "output_text",
                        "text": text_content,
                        "annotations": [],
                        "logprobs": []
                    }
                }
                yield f"event: response.content_part.done\ndata: {json.dumps(done_part_event)}\n\n".encode('utf-8')
                seq_num += 1

                done_item_event = {
                    "type": "response.output_item.done",
                    "sequence_number": seq_num,
                    "output_index": 0,
                    "item": {
                        "id": message_id,
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": text_content,
                            "annotations": [],
                            "logprobs": []
                        }]
                    }
                }
                yield f"event: response.output_item.done\ndata: {json.dumps(done_item_event)}\n\n".encode('utf-8')
                seq_num += 1

            # D. Final response object
            final_output = []
            if reasoning_started:
                final_output.append({
                    "id": reasoning_item_id,
                    "type": "reasoning",
                    "status": "completed",
                    "summary": [{"type": "summary_text", "text": reasoning_content}],
                    "encrypted_content": reasoning_content
                })
            if tool_call_started:
                final_output.extend(tool_call_items)
            if message_started:
                final_output.append({
                    "id": message_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": text_content,
                        "annotations": [],
                        "logprobs": []
                    }]
                })

            response_obj["status"] = "completed"
            response_obj["completed_at"] = int(time.time())
            response_obj["output"] = final_output
            response_obj["usage"] = {
                "input_tokens": usage_input_tokens if usage_input_tokens > 0 else 100,
                "output_tokens": usage_output_tokens if usage_output_tokens > 0 else 100,
                "total_tokens": (usage_input_tokens + usage_output_tokens) if (usage_input_tokens + usage_output_tokens) > 0 else 200,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0}
            }

            completed_event = {
                "type": "response.completed",
                "sequence_number": seq_num,
                "response": response_obj
            }
            logger.info(f"[Codex] Emitting response.completed (seq={seq_num})")
            yield f"event: response.completed\ndata: {json.dumps(completed_event)}\n\n".encode('utf-8')
        except Exception as ex:
            logger.error(f"Error in responses_stream_converter generator: {ex}", exc_info=True)
            raise

    return StreamingResponse(responses_stream_converter(), media_type="text/event-stream")


async def codex_chat_completions(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    Codex Desktop App Chat Completions endpoint.

    Mirrors /v1/chat/completions but with source="Codex-Desktop" for logging.
    """
    return await openai_chat_completions(request, username)


@app.post("/codex/completions", tags=["Codex Desktop App"])
async def codex_completions(
    request: CompletionRequest,
    username: str = Depends(get_current_user)
):
    """
    Codex Desktop App Completions endpoint.

    Mirrors /v1/completions but with source="Codex-Desktop" for logging.
    """
    logger.info(f"User {username} requesting Codex completion - model: {request.model}")
    return await openai_completions(request, username)


@app.post("/codex/embeddings", tags=["Codex Desktop App"])
async def codex_embeddings(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    Codex Desktop App Embeddings endpoint.

    Mirrors /v1/embeddings but with source="Codex-Desktop" for logging.
    """
    body = await request.body()
    data = json.loads(body.decode('utf-8')) if body else {}
    model_name = data.get('model', '')
    logger.info(f"User {username} requesting Codex embeddings - model: {model_name}")

    # Check model access
    has_access = await check_model_access(username, model_name)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {model_name}"
        )

    # Check user limits
    within_limits = await ollama_proxy.check_user_limits(username, "embeddings")
    if not within_limits:
        raise HTTPException(
            status_code=429,
            detail="User has exceeded their request or token limit"
        )

    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/embeddings",
        data=data,
        username=username,
        source="Codex-Desktop",
        url_path="/codex/embeddings"
    )


# =============================================================================
# OPENAI COMPLETIONS ENDPOINT
# =============================================================================

@app.post("/v1/completions", tags=["OpenAI Compatible API"])
async def openai_completions(
    request: CompletionRequest,
    username: str = Depends(get_current_user)
):
    """
    OpenAI compatible text completions endpoint.

    Supports streaming and non-streaming responses.
    """
    logger.info(f"User {username} requesting OpenAI completion - model: {request.model}")

    # Check model access
    has_access = await check_model_access(username, request.model)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Bu modele erişim yetkiniz yok: {request.model}"
        )

    # Check user limits
    within_limits = await ollama_proxy.check_user_limits(username, "completions")
    if not within_limits:
        raise HTTPException(
            status_code=429,
            detail="User has exceeded their request or token limit"
        )

    data = request.model_dump(exclude_none=True)

    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/completions",
        data=data,
        stream=data.get("stream", False),
        username=username,
        source="OpenAI-Compatible",
        url_path="/v1/completions"
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
        username=username,
        source="Cursor",
        url_path="/cursor/chat/completions"
    )



# ============================================================================
# Grafana Assistant Router
# ============================================================================
from app.grafana_assistant import router as grafana_router

app.include_router(grafana_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
