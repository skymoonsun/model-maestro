"""
OpenClaw-compatible Ollama API endpoints.

OpenClaw communicates with Ollama using:
1. GET  /api/tags — Model discovery (list installed models)
2. POST /api/show — Model details (context_length, capabilities)
3. POST /api/chat — Chat streaming (NDJSON, native Ollama format)

All endpoints are mounted under /openclaw prefix.
OpenClaw config example:
  models.providers.ollama.baseUrl = "http://your-proxy:8000/openclaw"
  models.providers.ollama.api = "ollama"
  models.providers.ollama.apiKey = "your-jwt-token"
"""

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import get_current_user, check_model_access
from app.proxy import ollama_proxy
from app.config import (
    get_settings,
    model_mapper,
    get_context_length_for_model,
    filter_tools_for_model,
    DEFAULT_CONTEXT_LENGTH,
)
from app.user_manager import user_manager

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/openclaw", tags=["OpenClaw API"])


# =============================================================================
# HELPER: Extract Bearer token from Authorization header
# =============================================================================

async def get_openclaw_user(request: Request) -> str:
    """
    OpenClaw sends Authorization: Bearer <token>.
    Extract and validate — reuse existing auth logic.
    """
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format. Use: Bearer <token>",
        )

    token = parts[1]

    # Import redis_manager at runtime
    from app.redis import redis_manager, CACHE_KEYS, CACHE_TTL

    # Try Redis cache first
    cache_key = f"token:{token}"
    if redis_manager:
        cached_username = await redis_manager.get(cache_key)
        if cached_username:
            return cached_username

    # Cache miss - verify token from DB
    username = await user_manager.verify_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Cache the token -> username mapping
    if redis_manager:
        await redis_manager.set(cache_key, username, expire=CACHE_TTL["TOKEN_USERNAME"])

    return username


# =============================================================================
# GET /openclaw/ — Root check (OpenClaw may probe this)
# =============================================================================

@router.get("/")
async def openclaw_root():
    """
    OpenClaw / Ollama CLI may probe the root to check if the server is alive.
    Native Ollama returns "Ollama is running" as plain text.
    """
    return "Ollama is running"


# =============================================================================
# GET /openclaw/api/tags — Model Discovery
# =============================================================================

@router.get("/api/tags")
async def openclaw_list_models(username: str = Depends(get_openclaw_user)):
    """
    List available models for OpenClaw.
    
    OpenClaw uses this to auto-discover models. It then calls /api/show
    for each model to get capabilities and context_length.
    
    Response format (native Ollama):
    {
      "models": [
        {
          "name": "model-name:tag",
          "model": "model-name:tag",
          "size": 1234567890,
          "digest": "sha256:...",
          "modified_at": "2024-...",
          "details": {
            "parent_model": "",
            "format": "gguf",
            "family": "llama",
            "families": ["llama"],
            "parameter_size": "7B",
            "quantization_level": "Q4_K_M"
          }
        }
      ]
    }
    """
    logger.info(f"[OpenClaw] User {username} requesting model list")

    # Get all models from Ollama
    all_models_response = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/api/tags",
        username=username,
    )

    # Get user's model access
    user_models_data = await user_manager.get_user_models(username)
    if not user_models_data:
        return {"models": []}

    # Ensure model mappings are loaded
    await model_mapper.ensure_loaded()
    all_mappings = model_mapper.get_all_mappings()

    if isinstance(all_models_response, dict) and "models" in all_models_response:
        models_dict = {}

        # Add all models from Ollama with display name mapping
        for model in all_models_response["models"]:
            model_name = model.get("name") or model.get("model")
            if model_name:
                display_names = model_mapper.get_all_display_names_for_real_name(model_name)
                for display_name in display_names:
                    if display_name not in models_dict:
                        model_copy = model.copy()
                        model_copy["name"] = display_name
                        model_copy["model"] = display_name
                        # Remove remote_model/remote_host to hide cloud provider details
                        model_copy.pop("remote_model", None)
                        model_copy.pop("remote_host", None)
                        models_dict[display_name] = model_copy

        # Add mappings that don't have a real Ollama model entry yet
        for display_name, real_name in all_mappings.items():
            if display_name not in models_dict:
                base = all_models_response["models"][0] if all_models_response["models"] else {}
                model_entry = base.copy() if base else {}
                model_entry["name"] = display_name
                model_entry["model"] = display_name
                model_entry.pop("remote_model", None)
                model_entry.pop("remote_host", None)
                models_dict[display_name] = model_entry

        mapped_models = list(models_dict.values())

        # Filter by user access
        if user_models_data["has_all_models"]:
            return {"models": mapped_models}

        allowed = set(user_models_data["models"])
        filtered = [
            m for m in mapped_models
            if m.get("name") in allowed or m.get("model") in allowed
        ]
        return {"models": filtered}

    return all_models_response


# =============================================================================
# POST /openclaw/api/show — Model Details
# =============================================================================

@router.post("/api/show")
async def openclaw_show_model(
    request: Request,
    username: str = Depends(get_openclaw_user),
):
    """
    Show model details.
    
    OpenClaw uses this to discover:
    1. Context window: model_info["<arch>.context_length"]
    2. Tool support: model_info["general.capabilities"] contains "tools"
    3. Reasoning: model_info["general.capabilities"] contains "thinking"
    
    We forward to Ollama /api/show AND enrich the response with our DB data
    (capabilities, context_length) if available.
    """
    body = await request.json()
    model_name = body.get("name") or body.get("model", "")

    logger.info(f"[OpenClaw] User {username} requesting show for model: {model_name}")

    # Map display name to real name for Ollama
    real_model_name = model_mapper.get_real_model_name(model_name)

    # Forward to Ollama /api/show with the real model name
    try:
        ollama_body = {"name": real_model_name}
        response_data = await ollama_proxy.proxy_request(
            method="POST",
            endpoint="/api/show",
            data=ollama_body,
            username=username,
        )
    except HTTPException as e:
        # If Ollama doesn't have this model (e.g. cloud model), build a synthetic response
        logger.warning(
            f"[OpenClaw] /api/show failed for {real_model_name}: {e.detail}. "
            "Building synthetic response from DB."
        )
        response_data = _build_synthetic_show_response(model_name, real_model_name)

    # Ensure response is a dict
    if not isinstance(response_data, dict):
        try:
            response_data = json.loads(response_data) if isinstance(response_data, str) else {}
        except (json.JSONDecodeError, TypeError):
            response_data = {}

    # Map model name back to display name
    if "name" in response_data:
        response_data["name"] = model_name
    if "model" in response_data:
        response_data["model"] = model_name

    # Enrich model_info with capabilities and context_length from our DB
    response_data = _enrich_model_info(response_data, model_name)

    return response_data


def _build_synthetic_show_response(display_name: str, real_name: str) -> dict:
    """
    Build a synthetic /api/show response for models not available on Ollama
    (e.g. cloud models accessible via API but not installed locally).
    """
    ctx_length = get_context_length_for_model(display_name)

    return {
        "name": display_name,
        "model": display_name,
        "modified_at": "",
        "details": {
            "parent_model": "",
            "format": "gguf",
            "family": "unknown",
            "families": [],
            "parameter_size": "unknown",
            "quantization_level": "unknown",
        },
        "model_info": {
            "general.architecture": "unknown",
            "general.capabilities": ["completion", "tools"],
            "unknown.context_length": ctx_length,
        },
        "template": "",
        "capabilities": ["completion", "tools"],
    }


def _enrich_model_info(data: dict, display_name: str) -> dict:
    """
    Enrich the /api/show response with capabilities and context_length
    from our database. This ensures OpenClaw correctly detects:
    - Tool support (via model_info["general.capabilities"])
    - Context window (via model_info["<arch>.context_length"])
    """
    # Get capabilities from our DB mapping
    db_capabilities = model_mapper.get_capabilities(display_name)
    db_context_length = get_context_length_for_model(display_name)

    # Ensure model_info exists
    if "model_info" not in data:
        data["model_info"] = {}

    model_info = data["model_info"]

    # --- Capabilities Injection ---
    # OpenClaw looks for model_info["general.capabilities"] containing "tools"
    if db_capabilities:
        # Map our capability names to Ollama's format
        ollama_caps = []
        for cap in db_capabilities:
            if cap == "completion":
                ollama_caps.append("completion")
            elif cap == "tools":
                ollama_caps.append("tools")
            elif cap == "thinking":
                ollama_caps.append("thinking")
            elif cap == "vision":
                ollama_caps.append("vision")
            else:
                ollama_caps.append(cap)

        model_info["general.capabilities"] = ollama_caps
    elif "general.capabilities" not in model_info:
        # Default: assume tools support (OpenClaw filters models without tools)
        # Check the top-level "capabilities" field from Ollama
        top_level_caps = data.get("capabilities", [])
        if top_level_caps:
            model_info["general.capabilities"] = top_level_caps
        else:
            # Fallback: at minimum mark as completion + tools so OpenClaw shows it
            model_info["general.capabilities"] = ["completion", "tools"]

    # --- Context Length Injection ---
    # OpenClaw looks for model_info["<arch>.context_length"]
    # Find existing context_length key or inject one
    existing_ctx_key = None
    existing_ctx_value = None
    for key, value in model_info.items():
        if key.endswith(".context_length") and isinstance(value, (int, float)):
            existing_ctx_key = key
            existing_ctx_value = int(value)
            break

    if db_context_length and db_context_length > 0:
        # Override with our DB value
        if existing_ctx_key:
            model_info[existing_ctx_key] = db_context_length
        else:
            # Determine architecture key
            arch = model_info.get("general.architecture", "unknown")
            model_info[f"{arch}.context_length"] = db_context_length
    elif not existing_ctx_key:
        # No context_length found anywhere, inject default
        ctx = get_context_length_for_model(display_name)
        arch = model_info.get("general.architecture", "unknown")
        model_info[f"{arch}.context_length"] = ctx

    data["model_info"] = model_info
    return data


# =============================================================================
# POST /openclaw/api/chat — Chat Streaming (NDJSON)
# =============================================================================

@router.post("/api/chat")
async def openclaw_chat(
    request: Request,
    username: str = Depends(get_openclaw_user),
):
    """
    Chat completion (native Ollama /api/chat format).
    
    OpenClaw sends:
    - model, messages, stream: true, tools, options (num_ctx, temperature, num_predict)
    - messages have roles: system, user, assistant, tool
    - tool results use role: "tool" with tool_name field
    
    Response: NDJSON streaming with:
    - message.content for text
    - message.thinking / message.reasoning for reasoning (if model supports)
    - message.tool_calls for tool usage
    - done: true in final chunk with usage stats
    """
    body = await request.json()
    model_name = body.get("model", "")
    stream = body.get("stream", True)  # OpenClaw always uses streaming

    logger.info(f"[OpenClaw] User {username} chat request: model={model_name}, stream={stream}")

    # Check model access
    has_access = await check_model_access(username, model_name)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model_name}' is not available for your account",
        )

    # Check user limits
    within_limits = await ollama_proxy.check_user_limits(username, "chat")
    if not within_limits:
        raise HTTPException(status_code=429, detail="Daily request limit exceeded")

    # Map display model name -> real Ollama model name
    real_model_name = model_mapper.get_real_model_name(model_name)
    body["model"] = real_model_name

    # Inject num_ctx if not provided by OpenClaw
    if "options" not in body:
        body["options"] = {}
    if isinstance(body["options"], dict) and "num_ctx" not in body["options"]:
        ctx_length = get_context_length_for_model(model_name)
        body["options"]["num_ctx"] = ctx_length
        logger.info(f"[OpenClaw] Injected num_ctx={ctx_length} for {model_name}")

    # Keep model loaded indefinitely after first use to prevent cold-start timeouts.
    # Ollama's default keep_alive is 5 minutes; set to -1 (forever) so the model
    # stays in VRAM and subsequent requests don't have to wait for reload.
    if "keep_alive" not in body:
        body["keep_alive"] = -1

    # Filter tools for model if applicable
    if "tools" in body and body["tools"]:
        filtered_tools = filter_tools_for_model(model_name, body["tools"])
        if len(filtered_tools) != len(body["tools"]):
            body["tools"] = filtered_tools
            logger.info(
                f"[OpenClaw] Filtered tools for {model_name}: "
                f"{len(body['tools'])} tools (reduced set)"
            )

    # Forward to Ollama via proxy
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/chat",
        data=body,
        stream=stream,
        username=username,
    )


# =============================================================================
# POST /openclaw/api/embed — Embeddings (new format)
# =============================================================================

@router.post("/api/embed")
async def openclaw_embed(
    request: Request,
    username: str = Depends(get_openclaw_user),
):
    """
    Generate embeddings (new Ollama /api/embed format with 'input' field).
    """
    body = await request.json()
    model_name = body.get("model", "")

    # Check model access
    has_access = await check_model_access(username, model_name)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model_name}' is not available for your account",
        )

    # Check user limits
    within_limits = await ollama_proxy.check_user_limits(username, "embeddings")
    if not within_limits:
        raise HTTPException(status_code=429, detail="Daily request limit exceeded")

    # Map model name
    real_model_name = model_mapper.get_real_model_name(model_name)
    body["model"] = real_model_name

    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/embed",
        data=body,
        stream=False,
        username=username,
    )


# ============================================================================
# OpenAI Compatible V1 Endpoints
# ============================================================================

@router.get("/models")
async def openclaw_v1_models(username: str = Depends(get_openclaw_user)):
    """
    OpenAI /models compatible endpoint.
    """
    logger.info(f"[OpenClaw] User {username} requesting v1/models")

    ollama_resp = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/api/tags",
        username=username,
    )

    user_models_data = await user_manager.get_user_models(username)
    if not user_models_data:
        return {"object": "list", "data": []}

    openai_models = []
    if isinstance(ollama_resp, dict) and "models" in ollama_resp:
        for model in ollama_resp["models"]:
            model_id = model.get("name") or model.get("model")
            if model_id:
                display_names = model_mapper.get_all_display_names_for_real_name(model_id)
                for display_name in display_names:
                    openai_models.append({
                        "id": display_name,
                        "object": "model",
                        "created": 0,
                        "owned_by": "ollama",
                    })

    if user_models_data["has_all_models"]:
        return {"object": "list", "data": openai_models}

    allowed = set(user_models_data["models"])
    filtered = [m for m in openai_models if m["id"] in allowed]
    return {"object": "list", "data": filtered}


@router.post("/chat/completions")
async def openclaw_v1_chat_completions(
    request: Request,
    username: str = Depends(get_openclaw_user),
):
    """
    OpenAI /chat/completions compatible endpoint.
    """
    body = await request.json()
    model_name = body.get("model", "")
    stream = body.get("stream", False)

    logger.info(f"[OpenClaw] User {username} v1/chat/completions: model={model_name}, stream={stream}")

    has_access = await check_model_access(username, model_name)
    if not has_access:
        raise HTTPException(status_code=403, detail=f"Model '{model_name}' is not available for your account")

    within_limits = await ollama_proxy.check_user_limits(username, "chat")
    if not within_limits:
        raise HTTPException(status_code=429, detail="Daily request limit exceeded")

    real_model_name = model_mapper.get_real_model_name(model_name)
    body["model"] = real_model_name

    # Normalize reasoning values: Ollama rejects 'minimal', map to 'low'
    for key in ("reasoning",):
        for container in (body, body.get("options", {})):
            if isinstance(container, dict) and container.get(key) == "minimal":
                container[key] = "low"
                logger.info(f"[OpenClaw] Normalized reasoning 'minimal' -> 'low' for model {model_name}")

    if "options" not in body:
        body["options"] = {}
    if isinstance(body["options"], dict) and "num_ctx" not in body["options"]:
        ctx_length = get_context_length_for_model(model_name)
        body["options"]["num_ctx"] = ctx_length
        logger.info(f"[OpenClaw] Injected num_ctx={ctx_length} for v1/chat {model_name}")

    if "keep_alive" not in body:
        body["keep_alive"] = -1

    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=body,
        stream=stream,
        username=username,
    )


@router.post("/embeddings")
async def openclaw_v1_embeddings(
    request: Request,
    username: str = Depends(get_openclaw_user),
):
    """
    OpenAI /embeddings compatible endpoint.
    """
    body = await request.json()
    model_name = body.get("model", "")
    input_text = body.get("input", "")

    logger.info(f"[OpenClaw] User {username} v1/embeddings: model={model_name}")

    has_access = await check_model_access(username, model_name)
    if not has_access:
        raise HTTPException(status_code=403, detail=f"Model '{model_name}' is not available for your account")

    within_limits = await ollama_proxy.check_user_limits(username, "embeddings")
    if not within_limits:
        raise HTTPException(status_code=429, detail="Daily request limit exceeded")

    real_model_name = model_mapper.get_real_model_name(model_name)

    ollama_body = {
        "model": real_model_name,
        "input": input_text,
        "truncate": True,
    }

    response = await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/embed",
        data=ollama_body,
        stream=False,
        username=username,
    )

    if isinstance(response, dict):
        response_data = response
    elif isinstance(response, str):
        response_data = json.loads(response)
    else:
        response_data = {}

    embeddings = response_data.get("embeddings", [])
    data_list = []
    total_tokens = 0
    for idx, emb in enumerate(embeddings):
        data_list.append({"embedding": emb, "index": idx})
        if isinstance(input_text, list):
            text = input_text[idx] if idx < len(input_text) else ""
        else:
            text = input_text
        total_tokens += max(1, len(text) // 4)

    return {
        "object": "list",
        "data": data_list,
        "model": model_name,
        "usage": {
            "prompt_tokens": total_tokens,
            "total_tokens": total_tokens,
        }
    }



