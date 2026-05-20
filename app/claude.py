"""
Claude Code compatible Anthropic API endpoints.

Claude Code connects via:
  export ANTHROPIC_API_KEY="<JWT_TOKEN>"
  export ANTHROPIC_BASE_URL="http://your-proxy:8000/claude"
  claude --model kimi-k2.6:cloud

Endpoints mounted under /claude prefix.
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
    model_group_manager,
    get_context_length_for_model,
    filter_tools_for_model,
)
from app.user_manager import user_manager
from app.claude_desktop_models import (
    desktop_name_passes_client_validation,
    is_maestro_desktop_route_id,
    peek_routing_name_from_public_id,
    persist_desktop_routes_to_redis,
    register_desktop_route_alias,
    resolve_desktop_public_id,
    to_desktop_public_id,
)

logger = logging.getLogger(__name__)
settings = get_settings()


# ISO 8601 timestamp for model listings (static so IDs remain stable across calls)
_MODEL_LIST_TIMESTAMP = "2024-01-01T00:00:00Z"

# Claude Desktop (Cowork 3P) sends these via inferenceCustomHeaders — see docs/IDE_INTEGRATION.md
MAESTRO_CLIENT_HEADER = "x-maestro-client"
DESKTOP_CLIENT_VALUES = frozenset({"claude-desktop", "cowork", "desktop"})

router = APIRouter(prefix="/claude", tags=["Claude API"])


def _cap(supported: bool) -> Dict[str, bool]:
    return {"supported": supported}


def _is_claude_desktop_client(request: Request) -> bool:
    """True when Claude Desktop/Cowork identifies itself via custom headers."""
    raw = (request.headers.get(MAESTRO_CLIENT_HEADER) or "").strip().lower()
    if raw in DESKTOP_CLIENT_VALUES:
        return True
    # Cowork inferenceCustomHeaders also supports "Name: Value" lines in some builds;
    # FastAPI normalizes to x-maestro-client when configured as JSON {"X-Maestro-Client":"..."}.
    return False


def _is_embedding_catalog_model(model_id: str) -> bool:
    n = (model_id or "").lower()
    return "embed" in n


def _model_list_capabilities(model_id: str, *, desktop: bool) -> Dict[str, Any]:
    """
    Capabilities for GET /v1/models.

    Claude Code is permissive; Claude Desktop (Cowork) filters the picker client-side
    using Anthropic-shaped capability flags (thinking, effort, tools, etc.).
    Maestro's default listing marks almost everything unsupported, so Desktop hides
    most gateway models even though the API returns them.
    """
    if not desktop:
        return {
            "batch": _cap(False),
            "citations": _cap(False),
            "code_execution": _cap(False),
            "context_management": _cap(False),
            "effort": _cap(False),
            "image_input": _cap(False),
            "pdf_input": _cap(False),
            "structured_outputs": _cap(True),
            "thinking": _cap(False),
        }

    if _is_embedding_catalog_model(model_id):
        return {
            "batch": _cap(False),
            "citations": _cap(False),
            "code_execution": _cap(False),
            "context_management": _cap(False),
            "effort": _cap(False),
            "image_input": _cap(False),
            "pdf_input": _cap(False),
            "structured_outputs": _cap(True),
            "thinking": _cap(False),
        }

    n = (model_id or "").lower()
    vision = any(k in n for k in ("vision", "vl", "multimodal", "image"))
    thinking = any(
        k in n
        for k in ("thinking", "reason", "r1", "opus", "sonnet", "haiku", "prime")
    ) or not _is_embedding_catalog_model(model_id)

    return {
        "batch": _cap(False),
        "citations": _cap(True),
        "code_execution": _cap(True),
        "context_management": {
            "supported": True,
            "clear_thinking_20251015": _cap(thinking),
            "clear_tool_uses_20250919": _cap(True),
            "compact_20260112": _cap(True),
        },
        "effort": {
            "supported": True,
            "low": _cap(True),
            "medium": _cap(True),
            "high": _cap(True),
            "max": _cap("opus" in n or "thinking" in n),
        },
        "image_input": _cap(vision),
        "pdf_input": _cap(True),
        "structured_outputs": _cap(True),
        "thinking": {
            "supported": thinking,
            "types": {
                "enabled": _cap(thinking),
                "adaptive": _cap(thinking),
            },
        },
    }


async def _resolve_claude_request_model(
    raw_model: str,
    *,
    desktop: bool,
    request_data: Optional[Dict[str, Any]] = None,
) -> tuple[str, Optional[List[int]]]:
    """
    Normalize model id from Claude clients to Maestro internal routing name.

    Opaque ``claude-maestro-{hash}`` ids are resolved only with the Desktop header;
    without it the model is treated as not found (no hash lookup).

    Returns:
        (routing_model_name, preferred_node_ids from model groups)
    """
    raw = (raw_model or "").strip()
    preferred_node_ids: Optional[List[int]] = None

    if is_maestro_desktop_route_id(raw):
        if not desktop:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{raw}' not found",
            )
        model_name = await resolve_desktop_public_id(raw)
    elif desktop:
        model_name = await resolve_desktop_public_id(raw)
    elif raw.startswith("claude-"):
        model_name = raw[7:]
    else:
        model_name = raw

    await model_mapper.ensure_loaded()
    mapped = model_mapper.get_real_model_name(model_name)
    if mapped != model_name:
        logger.info(f"[Claude] Model mapping: '{model_name}' -> '{mapped}'")
        model_name = mapped

    await model_group_manager.ensure_loaded()
    group_resolved, pids = await model_group_manager.resolve_model_with_metadata(
        model_name, request_data
    )
    if group_resolved != model_name:
        logger.info(
            f"[Claude] Model group: '{model_name}' -> '{group_resolved}' "
            f"(preferred_node_ids={pids})"
        )
        model_name = group_resolved
        preferred_node_ids = pids

    if raw != model_name:
        logger.info(
            f"[Claude] Resolved client model '{raw}' -> routing '{model_name}' "
            f"(desktop={desktop}, preferred_node_ids={preferred_node_ids})"
        )

    return model_name, preferred_node_ids


def _desktop_listing_entry(
    internal_name: str,
    *,
    desktop: bool,
    ctx_len: int,
    display_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one Anthropic ModelInfo object for GET /v1/models."""
    if desktop:
        public_id = to_desktop_public_id(internal_name)
        display = display_name if display_name is not None else internal_name
    else:
        public_id = (
            internal_name
            if internal_name.startswith("claude-")
            else f"claude-{internal_name}"
        )
        display = internal_name.removeprefix("claude-") or internal_name
    return {
        "type": "model",
        "id": public_id,
        "display_name": display,
        "created_at": _MODEL_LIST_TIMESTAMP,
        "max_input_tokens": ctx_len,
        "max_tokens": 8192,
        "capabilities": _model_list_capabilities(public_id, desktop=desktop),
    }


def _map_finish_reason(finish_reason: str | None) -> str:
    """Map OpenAI finish_reason to Anthropic stop_reason."""
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason in ("end_turn", "max_tokens", "stop_sequence"):
        return finish_reason
    return "end_turn"


# =============================================================================
# HEAD /claude/ — Connectivity check for Claude Code extension
# =============================================================================

@router.head("/")
async def claude_root_head():
    """Return 200 OK for Claude Code extension connectivity check."""
    from fastapi import Response
    return Response(status_code=200)


# =============================================================================
# HELPER: Extract Bearer token from Authorization header
# =============================================================================

async def get_claude_user(request: Request) -> str:
    """
    Claude Code sends Authorization: Bearer <token>.
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
    from app.redis import redis_manager, CACHE_TTL

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
# GET /claude/v1/models
# =============================================================================

@router.get("/v1/models")
async def claude_list_models(
    request: Request,
    username: str = Depends(get_claude_user),
    limit: int = 1000,
):
    """
    List available models in Anthropic format.
    Claude Code extension expects integer timestamps, max_tokens and provider fields.
    """
    desktop = _is_claude_desktop_client(request)
    logger.info(
        f"[Claude] User {username} requesting model list (limit={limit}, "
        f"client={'desktop' if desktop else 'default'})"
    )

    # Get all models from DB (includes antigravity, bedrock, vllm, ollama)
    from app.node_manager import node_manager
    all_models_response = await node_manager.get_all_models_from_nodes()

    # Get user's model access
    user_models_data = await user_manager.get_user_models(username)
    if not user_models_data:
        return {"data": [], "has_more": False, "first_id": None, "last_id": None}

    # Build Anthropic-format model list
    # Claude Code extension only accepts model IDs starting with "claude" or "anthropic"
    models_list = []

    await model_mapper.ensure_loaded()

    if desktop:
        for display, real in model_mapper.get_all_mappings().items():
            if display != real:
                register_desktop_route_alias(display, real)

    if isinstance(all_models_response, dict) and "models" in all_models_response:
        for model in all_models_response["models"]:
            model_id = model.get("name") or model.get("model")
            if model_id:
                display_names = model_mapper.get_all_display_names_for_real_name(model_id)
                ids_to_add = display_names if display_names else [model_id]
                for name in ids_to_add:
                    ctx_len = get_context_length_for_model(name) or 131072
                    entry = _desktop_listing_entry(
                        model_id,
                        desktop=desktop,
                        ctx_len=ctx_len,
                        display_name=name if name != model_id else None,
                    )
                    if desktop and name != model_id:
                        register_desktop_route_alias(name, model_id)
                    if desktop and not desktop_name_passes_client_validation(entry["id"]):
                        logger.warning(
                            f"[Claude][Desktop] Skipping model '{name}' — public id failed validation: "
                            f"{entry['id']}"
                        )
                        continue
                    models_list.append(entry)

    # Filter by user access (compare internal Maestro names, not Desktop public ids)
    if not user_models_data["has_all_models"]:
        allowed = set(user_models_data["models"])
        def _allowed(m: Dict[str, Any]) -> bool:
            routed = peek_routing_name_from_public_id(m["id"]) or m["display_name"]
            routed = routed.removeprefix("claude-")
            return routed in allowed or m["display_name"] in allowed

        models_list = [m for m in models_list if _allowed(m)]

    from app.model_list import get_visible_catalog_group_names

    for group_name in await get_visible_catalog_group_names(user_models_data):
        if any(m["display_name"] == group_name for m in models_list):
            continue
        ctx_len = get_context_length_for_model(group_name) or 131072
        entry = _desktop_listing_entry(group_name, desktop=desktop, ctx_len=ctx_len)
        if not desktop or desktop_name_passes_client_validation(entry["id"]):
            models_list.append(entry)

    if desktop:
        seen_ids: set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for m in models_list:
            mid = m["id"]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            deduped.append(m)
        models_list = deduped
        await persist_desktop_routes_to_redis()
        logger.info(
            f"[Claude][Desktop] Model list after opaque ids: {len(models_list)} models"
        )

    first_id = models_list[0]["id"] if models_list else None
    last_id = models_list[-1]["id"] if models_list else None

    return {
        "data": models_list,
        "has_more": False,
        "first_id": first_id,
        "last_id": last_id,
    }


# =============================================================================
# POST /claude/v1/messages
# =============================================================================

@router.post("/v1/messages")
async def claude_messages(
    request: Request,
    username: str = Depends(get_claude_user),
):
    """
    Anthropic Messages API compatible endpoint.
    POST /claude/v1/messages
    """
    body = await request.json()
    desktop = _is_claude_desktop_client(request)
    model_name, preferred_node_ids = await _resolve_claude_request_model(
        body.get("model", ""),
        desktop=desktop,
        request_data=body,
    )
    stream = body.get("stream", False)
    messages = body.get("messages", [])
    system = body.get("system")
    max_tokens = body.get("max_tokens", 4096)
    tools = body.get("tools", [])
    tool_choice = body.get("tool_choice")
    temperature = body.get("temperature")
    top_p = body.get("top_p")
    top_k = body.get("top_k")
    metadata = body.get("metadata")

    # Check Claude streaming toggle from system config
    from app.services import config_manager
    streaming_enabled = config_manager.get_bool("claude.streaming_enabled", False)
    if not streaming_enabled and stream:
        logger.info(f"[Claude] Streaming disabled by config; forcing stream=False for user={username}")
        stream = False

    logger.info(
        f"[Claude] User {username} messages: model={model_name}, stream={stream}, "
        f"msg_count={len(messages)}, max_tokens={max_tokens}"
    )

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

    # Build Ollama request body (model_name is already mapped / group-resolved)
    ollama_body: Dict[str, Any] = {
        "model": model_name,
        "stream": stream,
        "options": {
            "num_ctx": get_context_length_for_model(model_name),
        },
        "keep_alive": -1,
    }
    if preferred_node_ids:
        ollama_body["_preferred_node_ids"] = preferred_node_ids

    # Normalize Anthropic messages -> OpenAI/Ollama format
    normalized_messages = _normalize_anthropic_messages(messages)

    # System prompt mapping: Anthropic system -> Ollama system message
    if system:
        if isinstance(system, str):
            system_text = system
        elif isinstance(system, dict) and system.get("type") == "text":
            system_text = system.get("text", "")
        elif isinstance(system, list):
            # Multiple system prompts, merge them
            texts = []
            for s in system:
                if isinstance(s, dict) and s.get("type") == "text":
                    texts.append(s.get("text", ""))
            system_text = "\n\n".join(texts)
        else:
            system_text = ""

        # Claude Code expects system to be processed; inject as first system message
        if system_text:
            ollama_body["messages"] = [
                {"role": "system", "content": system_text},
                *normalized_messages,
            ]
    else:
        ollama_body["messages"] = normalized_messages

    # Thinking / reasoning mapping: Anthropic -> Ollama (+ pass-through for Antigravity)
    thinking = body.get("thinking")
    if thinking and isinstance(thinking, dict):
        ollama_body["thinking"] = thinking
        if thinking.get("type") == "enabled":
            budget_tokens = thinking.get("budget_tokens", 16000)
            # Ollama expects reasoning_effort for kimi / thinking models
            if budget_tokens >= 12000:
                reasoning_effort = "high"
            elif budget_tokens >= 4000:
                reasoning_effort = "medium"
            else:
                reasoning_effort = "low"
            ollama_body["reasoning_effort"] = reasoning_effort
            logger.info(f"[Claude] Mapped thinking to reasoning_effort={reasoning_effort}")

    # Tools mapping: Anthropic -> OpenAI
    if tools:
        openai_tools = _convert_anthropic_tools_to_openai(tools)
        if openai_tools:
            # Also inject system tool instructions if needed
            ollama_body["tools"] = openai_tools
            ollama_body["tool_choice"] = _convert_tool_choice(tool_choice)
            logger.info(f"[Claude] Converted {len(openai_tools)} Anthropic tools to OpenAI format")

    # Max tokens mapping (top-level for Antigravity/google_proxy; options for Ollama)
    if max_tokens:
        ollama_body["max_tokens"] = max_tokens
        ollama_body["options"]["num_predict"] = max_tokens

    # Temperature / top_p / top_k
    if temperature is not None:
        ollama_body["options"]["temperature"] = temperature
    if top_p is not None:
        ollama_body["options"]["top_p"] = top_p
    if top_k is not None:
        ollama_body["options"]["top_k"] = top_k

    if stream:
        # -----------------------------------------------------------------
        # Streaming: Ollama NDJSON -> Anthropic SSE
        # -----------------------------------------------------------------
        return await _handle_claude_streaming(
            ollama_body, model_name, username
        )

    else:
        # -----------------------------------------------------------------
        # Non-streaming: Ollama -> Anthropic
        # -----------------------------------------------------------------
        return await _handle_claude_non_streaming(
            ollama_body, model_name, username
        )


def _normalize_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Anthropic message format to OpenAI/Ollama message format."""
    normalized: List[Dict[str, Any]] = []
    tool_use_names: Dict[str, str] = {}
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if isinstance(content, str):
            normalized.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            normalized.append({"role": role, "content": str(content) if content is not None else ""})
            continue

        text_parts: List[str] = []
        tool_uses: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_uses.append(block)
            elif block_type == "tool_result":
                tool_results.append(block)
            # Skip image, thinking, etc.

        if role == "assistant" and tool_uses:
            openai_tool_calls = []
            for tu in tool_uses:
                tu_id = tu.get("id", "")
                tu_name = tu.get("name", "")
                if tu_id:
                    tool_use_names[tu_id] = tu_name
                openai_tool_calls.append({
                    "id": tu_id,
                    "type": "function",
                    "function": {
                        "name": tu_name,
                        "arguments": json.dumps(tu.get("input", {})),
                    },
                })
            normalized.append({
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else "",
                "tool_calls": openai_tool_calls,
            })
        elif role == "user" and tool_results:
            # Tool results first so Google/Anthropic sees them immediately after assistant tool_use.
            for tr in tool_results:
                tr_content = tr.get("content", "")
                if isinstance(tr_content, list):
                    tr_texts = [
                        b.get("text", "")
                        for b in tr_content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    tr_content = "\n".join(tr_texts) if tr_texts else ""
                tool_use_id = tr.get("tool_use_id", "")
                normalized.append({
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "name": tool_use_names.get(tool_use_id, ""),
                    "content": tr_content,
                })
            if text_parts:
                normalized.append({"role": "user", "content": "\n".join(text_parts)})
        else:
            normalized.append({
                "role": role,
                "content": "\n".join(text_parts) if text_parts else "",
            })

    return normalized


# =============================================================================
# Non-streaming handler
# =============================================================================

async def _handle_claude_non_streaming(
    ollama_body: Dict[str, Any],
    display_model_name: str,
    username: str,
) -> Dict[str, Any]:
    """
    Handle non-streaming Claude request via Ollama OpenAI-compatible endpoint.
    """
    response = await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=ollama_body,
        stream=False,
        username=username,
        source="Claude",
        url_path="/claude/v1/messages",
    )

    # Parse response
    if isinstance(response, dict):
        response_data = response
    elif isinstance(response, str):
        response_data = json.loads(response)
    else:
        response_data = {}

    # Build Anthropic message
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    choices = response_data.get("choices", [])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    text = message.get("content", "")

    content: List[Dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})

    # Tool calls mapping Ollama -> Claude
    tool_calls = message.get("tool_calls", [])
    if tool_calls:
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "")
            tool_args = tc.get("function", {}).get("arguments", "{}")
            try:
                tool_input = json.loads(tool_args)
            except json.JSONDecodeError:
                tool_input = {}
            tu_id = tc.get("id") or f"tu_{uuid.uuid4().hex[:20]}"
            content.append({
                "type": "tool_use",
                "id": tu_id,
                "name": tool_name,
                "input": tool_input,
            })

    usage = response_data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    anthropic_response: Dict[str, Any] = {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": display_model_name,
        "content": content,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
        },
    }

    return anthropic_response


# =============================================================================
# POST /claude/v1/messages/count_tokens
# =============================================================================

@router.post("/v1/messages/count_tokens")
async def claude_count_tokens(
    request: Request,
    username: str = Depends(get_claude_user),
):
    """
    Anthropic count_tokens API compatible endpoint.
    Claude Code calls this after compacting to check token usage.

    Request body mirrors the Messages API:
      {
        "model": "claude-...",
        "messages": [...],
        "system": "..." | {"type":"text", "text":"..."} | [...],
        "tools": [...],
        "tool_choice": "auto" | "any" | "none" | {"type":"tool", "name":"..."},
        "thinking": {"type": "enabled", "budget_tokens": 16000}
      }

    Response: {"input_tokens": <int>}
    """
    body = await request.json()
    desktop = _is_claude_desktop_client(request)
    model_name, preferred_node_ids = await _resolve_claude_request_model(
        body.get("model", ""),
        desktop=desktop,
        request_data=body,
    )
    if preferred_node_ids:
        body = {**body, "_preferred_node_ids": preferred_node_ids}
    messages = body.get("messages", [])
    system = body.get("system")
    tools = body.get("tools", [])
    thinking = body.get("thinking")

    total_chars = 0

    # --- system --------------------------------------------------------------
    if system:
        if isinstance(system, str):
            total_chars += len(system)
        elif isinstance(system, dict) and system.get("type") == "text":
            total_chars += len(system.get("text", ""))
        elif isinstance(system, list):
            for s in system:
                if isinstance(s, dict) and s.get("type") == "text":
                    total_chars += len(s.get("text", ""))

    # --- messages ------------------------------------------------------------
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type", "")
                if bt == "text":
                    total_chars += len(block.get("text", ""))
                elif bt == "image":
                    src = block.get("source", {})
                    if src.get("type") == "base64":
                        total_chars += len(src.get("data", "")) // 4
                elif bt == "tool_use":
                    total_chars += len(block.get("name", ""))
                    total_chars += len(json.dumps(block.get("input", {})))
                elif bt == "tool_result":
                    tr_content = block.get("content", "")
                    if isinstance(tr_content, str):
                        total_chars += len(tr_content)
                    elif isinstance(tr_content, list):
                        for tc in tr_content:
                            if isinstance(tc, dict) and tc.get("type") == "text":
                                total_chars += len(tc.get("text", ""))

    # --- tools ---------------------------------------------------------------
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        total_chars += len(tool.get("name", ""))
        total_chars += len(tool.get("description", ""))
        schema = tool.get("input_schema", tool.get("parameters", {}))
        total_chars += len(json.dumps(schema))

    # --- thinking ------------------------------------------------------------
    if thinking and isinstance(thinking, dict):
        if thinking.get("type") == "enabled":
            # Anthropic charges the budget_tokens as part of the context
            budget = thinking.get("budget_tokens", 0)
            total_chars += budget // 4  # rough estimate

    # Anthropic tokenizer ~3.5–4 chars per token; use 4 as conservative default
    input_tokens = max(total_chars // 4, 1)

    return {"input_tokens": input_tokens}


# =============================================================================
# Anthropic SSE helpers (streaming)
# =============================================================================

def _anthropic_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _merge_streaming_tool_call_deltas(
    tool_calls_by_index: Dict[int, Dict[str, Any]],
    delta_tool_calls: Any,
) -> None:
    """Accumulate OpenAI-style streaming tool_call fragments by index."""
    if not delta_tool_calls or not isinstance(delta_tool_calls, list):
        return

    for tc in delta_tool_calls:
        if not isinstance(tc, dict):
            continue
        idx = int(tc.get("index", 0))
        if idx not in tool_calls_by_index:
            tool_calls_by_index[idx] = {
                "id": "",
                "name": "",
                "arguments": "",
                "arguments_emitted_len": 0,
                "block_index": None,
                "started": False,
                "stopped": False,
            }
        acc = tool_calls_by_index[idx]
        if tc.get("id"):
            acc["id"] = str(tc["id"])
        func = tc.get("function") or {}
        if isinstance(func, dict):
            if func.get("name"):
                acc["name"] = str(func["name"])
            arg_part = func.get("arguments")
            if arg_part:
                acc["arguments"] += str(arg_part)


def _tool_stream_start_events(
    acc: Dict[str, Any],
    block_index: int,
) -> List[str]:
    tool_id = acc["id"] or f"tu_{uuid.uuid4().hex[:20]}"
    acc["id"] = tool_id
    acc["block_index"] = block_index
    acc["started"] = True
    return [
        _anthropic_sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": acc["name"],
                    "input": {},
                },
            },
        )
    ]


def _tool_stream_argument_delta_events(acc: Dict[str, Any]) -> List[str]:
    if acc.get("block_index") is None:
        return []
    args = acc.get("arguments", "")
    emitted = int(acc.get("arguments_emitted_len", 0))
    if emitted >= len(args):
        return []
    new_part = args[emitted:]
    acc["arguments_emitted_len"] = len(args)
    return [
        _anthropic_sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": acc["block_index"],
                "delta": {"type": "input_json_delta", "partial_json": new_part},
            },
        )
    ]


def _tool_stream_stop_event(acc: Dict[str, Any]) -> Optional[str]:
    if not acc.get("started") or acc.get("stopped") or acc.get("block_index") is None:
        return None
    acc["stopped"] = True
    return _anthropic_sse(
        "content_block_stop",
        {"type": "content_block_stop", "index": acc["block_index"]},
    )


# =============================================================================
# Non-streaming -> Anthropic SSE (fallback when upstream is not SSE)
# =============================================================================

async def _stream_from_non_streaming(
    ollama_body: Dict[str, Any],
    display_model_name: str,
    username: str,
    prefetched_response: Any = None,
) -> StreamingResponse:
    """Get non-streaming response and emit as Anthropic SSE events."""
    response = prefetched_response
    if response is None:
        ns_body = {**ollama_body, "stream": False}
        response = await ollama_proxy.proxy_request(
            method="POST",
            endpoint="/v1/chat/completions",
            data=ns_body,
            stream=False,
            username=username,
            source="Claude",
            url_path="/claude/v1/messages",
        )
    if isinstance(response, dict):
        response_data = response
    elif isinstance(response, str) and response.strip():
        try:
            response_data = json.loads(response)
        except json.JSONDecodeError:
            response_data = {}
    else:
        response_data = {}

    choices = response_data.get("choices", [])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    text = message.get("content", "") or ""

    content: List[Dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})

    for tc in message.get("tool_calls", []):
        fn = tc.get("function", {})
        tool_name = fn.get("name", "")
        tool_args = fn.get("arguments", "{}")
        try:
            tool_input = json.loads(tool_args)
        except json.JSONDecodeError:
            tool_input = {}
        tu_id = tc.get("id") or f"tu_{uuid.uuid4().hex[:20]}"
        content.append({
            "type": "tool_use",
            "id": tu_id,
            "name": tool_name,
            "input": tool_input,
        })

    usage = response_data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    stop_reason = _map_finish_reason(choice.get("finish_reason"))

    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    anthropic_msg = {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": display_model_name,
        "content": content,
        "stop_reason": None,
        "usage": {"input_tokens": prompt_tokens, "output_tokens": completion_tokens},
    }

    async def event_generator():
        yield (
            f"event: message_start\ndata: "
            + json.dumps({"type": "message_start", "message": anthropic_msg})
            + "\n\n"
        )
        for i, block in enumerate(content):
            if block["type"] == "text":
                yield (
                    f"event: content_block_start\ndata: "
                    + json.dumps({
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {"type": "text", "text": ""},
                    })
                    + "\n\n"
                )
                txt = block.get("text", "")
                for j in range(0, len(txt), 50):
                    yield (
                        f"event: content_block_delta\ndata: "
                        + json.dumps({
                            "type": "content_block_delta",
                            "index": i,
                            "delta": {"type": "text_delta", "text": txt[j:j + 50]},
                        })
                        + "\n\n"
                    )
                yield (
                    f"event: content_block_stop\ndata: "
                    + json.dumps({"type": "content_block_stop", "index": i})
                    + "\n\n"
                )
            elif block["type"] == "tool_use":
                yield (
                    f"event: content_block_start\ndata: "
                    + json.dumps({
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    })
                    + "\n\n"
                )
                inp_json = json.dumps(block.get("input", {}))
                for j in range(0, len(inp_json), 50):
                    yield (
                        f"event: content_block_delta\ndata: "
                        + json.dumps({
                            "type": "content_block_delta",
                            "index": i,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": inp_json[j:j + 50],
                            },
                        })
                        + "\n\n"
                    )
                yield (
                    f"event: content_block_stop\ndata: "
                    + json.dumps({"type": "content_block_stop", "index": i})
                    + "\n\n"
                )
        yield (
            f"event: message_delta\ndata: "
            + json.dumps({
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason},
                "usage": {"output_tokens": completion_tokens},
            })
            + "\n\n"
        )
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# Streaming handler: Ollama NDJSON -> Anthropic SSE
# =============================================================================

async def _handle_claude_streaming(
    ollama_body: Dict[str, Any],
    display_model_name: str,
    username: str,
) -> StreamingResponse:
    """
    Handle streaming Claude request.
    Consume OpenAI-compatible SSE and produce Anthropic SSE (text, thinking, tool_use).
    """
    if ollama_body.get("tools"):
        logger.info(
            f"[Claude] Streaming with {len(ollama_body['tools'])} tools via upstream SSE"
        )

    proxy_response = await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=ollama_body,
        stream=True,
        username=username,
        source="Claude",
        url_path="/claude/v1/messages",
    )

    if not isinstance(proxy_response, StreamingResponse):
        logger.info("[Claude] Upstream returned non-streaming body; synthesizing Anthropic SSE")
        return await _stream_from_non_streaming(
            {**ollama_body, "stream": False},
            display_model_name,
            username,
            prefetched_response=proxy_response,
        )

    msg_id = f"msg_{uuid.uuid4().hex[:20]}"

    async def claude_stream_generator():
        """Generator that consumes OpenAI SSE and produces Anthropic SSE events."""
        buffer = b""
        text_so_far = ""
        usage_stats: Dict[str, int] = {}
        finish_reason = None

        thinking_index: Optional[int] = None
        text_index: Optional[int] = None
        thinking_started = False
        text_started = False
        next_block_index = 0
        tool_calls_by_index: Dict[int, Dict[str, Any]] = {}

        def emit_tool_updates() -> List[str]:
            nonlocal next_block_index
            events: List[str] = []
            for tc_idx in sorted(tool_calls_by_index.keys()):
                acc = tool_calls_by_index[tc_idx]
                if not acc["started"] and acc["id"] and acc["name"]:
                    for ev in _tool_stream_start_events(acc, next_block_index):
                        events.append(ev)
                    next_block_index = int(acc["block_index"]) + 1
                for ev in _tool_stream_argument_delta_events(acc):
                    events.append(ev)
            return events

        yield _anthropic_sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": display_model_name,
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 0},
                },
            },
        )

        try:
            async for chunk in proxy_response.body_iterator:
                buffer += chunk
                # Normalize \r\n -> \n for consistent SSE parsing
                buffer = buffer.replace(b"\r\n", b"\n")
                while b"\n\n" in buffer:
                    line, buffer = buffer.split(b"\n\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data: "):
                        continue
                    data_str = line[6:].decode("utf-8").strip()
                    if data_str == "[DONE]":
                        continue

                    try:
                        chunk_data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Accumulate token usage if present in stream chunk
                    if "usage" in chunk_data and chunk_data["usage"]:
                        usage_stats = chunk_data["usage"]

                    choices = chunk_data.get("choices", [])
                    if not choices:
                        continue

                    choice = choices[0]

                    # Capture finish_reason from the final chunk
                    fr = choice.get("finish_reason")
                    if fr is not None:
                        finish_reason = fr

                    delta = choice.get("delta", {}) or {}
                    delta_content = delta.get("content")
                    delta_thinking = (
                        delta.get("reasoning")
                        or delta.get("thinking")
                        or delta.get("reasoning_content")
                    )

                    if delta_thinking:
                        if not thinking_started:
                            thinking_started = True
                            thinking_index = next_block_index
                            next_block_index += 1
                            yield _anthropic_sse(
                                "content_block_start",
                                {
                                    "type": "content_block_start",
                                    "index": thinking_index,
                                    "content_block": {"type": "thinking", "thinking": ""},
                                },
                            )
                        yield _anthropic_sse(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": thinking_index,
                                "delta": {
                                    "type": "thinking_delta",
                                    "thinking": str(delta_thinking),
                                },
                            },
                        )

                    if delta_content is not None and str(delta_content):
                        if not text_started:
                            text_started = True
                            text_index = next_block_index
                            next_block_index += 1
                            yield _anthropic_sse(
                                "content_block_start",
                                {
                                    "type": "content_block_start",
                                    "index": text_index,
                                    "content_block": {"type": "text", "text": ""},
                                },
                            )
                        text_so_far += str(delta_content)
                        yield _anthropic_sse(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": text_index,
                                "delta": {
                                    "type": "text_delta",
                                    "text": str(delta_content),
                                },
                            },
                        )

                    if delta.get("tool_calls"):
                        _merge_streaming_tool_call_deltas(
                            tool_calls_by_index, delta["tool_calls"]
                        )
                        for ev in emit_tool_updates():
                            yield ev

        except Exception as e:
            logger.error(f"[Claude] Streaming error: {e}", exc_info=True)
            yield _anthropic_sse(
                "error",
                {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": str(e)},
                },
            )

        for ev in emit_tool_updates():
            yield ev

        if not text_started and not any(
            acc.get("started") for acc in tool_calls_by_index.values()
        ):
            text_started = True
            text_index = next_block_index
            next_block_index += 1
            yield _anthropic_sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": text_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )

        if thinking_started and thinking_index is not None:
            yield _anthropic_sse(
                "content_block_stop",
                {"type": "content_block_stop", "index": thinking_index},
            )

        if text_started and text_index is not None:
            yield _anthropic_sse(
                "content_block_stop",
                {"type": "content_block_stop", "index": text_index},
            )

        for tc_idx in sorted(tool_calls_by_index.keys()):
            stop_ev = _tool_stream_stop_event(tool_calls_by_index[tc_idx])
            if stop_ev:
                yield stop_ev

        out_tokens = usage_stats.get("completion_tokens", len(text_so_far) // 4)
        anthropic_stop = _map_finish_reason(finish_reason)
        yield _anthropic_sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": anthropic_stop},
                "usage": {"output_tokens": out_tokens},
            },
        )
        yield _anthropic_sse("message_stop", {"type": "message_stop"})

    return StreamingResponse(
        claude_stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


async def _fallback_claude_stream(data, display_model_name: str):
    """Fallback generator when proxy returns non-streaming."""
    if isinstance(data, dict):
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    elif isinstance(data, str):
        try:
            parsed = json.loads(data)
            text = parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
        except json.JSONDecodeError:
            text = data
    else:
        text = ""

    msg_id = f"msg_{uuid.uuid4().hex[:20]}"
    events = [
        f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': display_model_name, 'content': [], 'stop_reason': None}})}\n\n",
        f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n",
    ]
    # Yield text in chunks
    chunk_size = 50
    for i in range(0, len(text), chunk_size):
        chunk_text = text[i:i + chunk_size]
        events.append(
            f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': chunk_text}})}\n\n"
        )
    events.extend([
        f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n",
        f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': len(text) // 4}})}\n\n",
        f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n",
    ])
    for event in events:
        yield event


# =============================================================================
# Tool conversion helpers
# =============================================================================

def _convert_anthropic_tools_to_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Anthropic tool definitions to OpenAI tool format for Ollama."""
    openai_tools: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "custom":
            # Already custom type, skip or adapt
            continue
        # Anthropic tools look like:
        # {"name": "calc", "description": "...", "input_schema": {...}}
        # "input_schema" corresponds to OpenAI "parameters"
        tool_name = tool.get("name", "")
        description = tool.get("description", "")
        input_schema = tool.get("input_schema", tool.get("parameters", {}))
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": description,
                    "parameters": input_schema,
                },
            }
        )
    return openai_tools


def _convert_tool_choice(tool_choice: Any) -> Any:
    """Map Anthropic tool_choice to OpenAI/Ollama tool_choice."""
    if not tool_choice:
        return "auto"
    if isinstance(tool_choice, dict):
        tc_type = tool_choice.get("type", "auto")
        if tc_type == "auto":
            return "auto"
        elif tc_type == "any":
            return {"type": "function", "function": {"name": None}}
        elif tc_type == "tool":
            tool_name = tool_choice.get("name", "")
            return {"type": "function", "function": {"name": tool_name}}
        elif tc_type == "none":
            return "none"
    if isinstance(tool_choice, str):
        return tool_choice
    return "auto"
