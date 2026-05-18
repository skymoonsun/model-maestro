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
    get_context_length_for_model,
    filter_tools_for_model,
)
from app.user_manager import user_manager

logger = logging.getLogger(__name__)
settings = get_settings()


# ISO 8601 timestamp for model listings (static so IDs remain stable across calls)
_MODEL_LIST_TIMESTAMP = "2024-01-01T00:00:00Z"

router = APIRouter(prefix="/claude", tags=["Claude API"])


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
    username: str = Depends(get_claude_user),
    limit: int = 1000,
):
    """
    List available models in Anthropic format.
    Claude Code extension expects integer timestamps, max_tokens and provider fields.
    """
    logger.info(f"[Claude] User {username} requesting model list (limit={limit})")

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

    if isinstance(all_models_response, dict) and "models" in all_models_response:
        for model in all_models_response["models"]:
            model_id = model.get("name") or model.get("model")
            if model_id:
                display_names = model_mapper.get_all_display_names_for_real_name(model_id)
                ids_to_add = display_names if display_names else [model_id]
                for name in ids_to_add:
                    ctx_len = get_context_length_for_model(name) or 131072
                    models_list.append({
                        "type": "model",
                        "id": name if name.startswith("claude-") else f"claude-{name}",
                        "display_name": name,
                        "created_at": _MODEL_LIST_TIMESTAMP,
                        "max_input_tokens": ctx_len,
                        "max_tokens": 8192,
                        "capabilities": {
                            "batch": {"supported": False},
                            "citations": {"supported": False},
                            "code_execution": {"supported": False},
                            "context_management": {"supported": False},
                            "effort": {"supported": False},
                            "image_input": {"supported": False},
                            "pdf_input": {"supported": False},
                            "structured_outputs": {"supported": True},
                            "thinking": {"supported": False},
                        },
                    })

    # Filter by user access (strip claude- prefix for comparison)
    if not user_models_data["has_all_models"]:
        allowed = set(user_models_data["models"])
        models_list = [
            m for m in models_list
            if m["id"].removeprefix("claude-") in allowed
        ]

    from app.model_list import get_visible_catalog_group_names

    for group_name in await get_visible_catalog_group_names(user_models_data):
        if any(m["display_name"] == group_name for m in models_list):
            continue
        ctx_len = get_context_length_for_model(group_name) or 131072
        models_list.append({
            "type": "model",
            "id": group_name if group_name.startswith("claude-") else f"claude-{group_name}",
            "display_name": group_name,
            "created_at": _MODEL_LIST_TIMESTAMP,
            "max_input_tokens": ctx_len,
            "max_tokens": 8192,
            "capabilities": {
                "batch": {"supported": False},
                "citations": {"supported": False},
                "code_execution": {"supported": False},
                "context_management": {"supported": False},
                "effort": {"supported": False},
                "image_input": {"supported": False},
                "pdf_input": {"supported": False},
                "structured_outputs": {"supported": True},
                "thinking": {"supported": False},
            },
        })

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
    model_name = body.get("model", "")
    # Strip claude- prefix unconditionally — we add it artificially in the model list
    # (see GET /v1/models, line ~145). Claude Code sends it back, and the real
    # model name (Ollama-side) never starts with "claude-".
    if model_name.startswith("claude-"):
        model_name = model_name[7:]
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

    # Map display model name -> real Ollama model name
    real_model_name = model_mapper.get_real_model_name(model_name)

    # Build Ollama request body
    ollama_body: Dict[str, Any] = {
        "model": real_model_name,
        "stream": stream,
        "options": {
            "num_ctx": get_context_length_for_model(model_name),
        },
        "keep_alive": -1,
    }

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

    # Thinking / reasoning mapping: Anthropic -> Ollama
    thinking = body.get("thinking")
    if thinking and isinstance(thinking, dict):
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

    # Max tokens mapping
    if max_tokens:
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
                openai_tool_calls.append({
                    "id": tu.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tu.get("name", ""),
                        "arguments": json.dumps(tu.get("input", {})),
                    },
                })
            normalized.append({
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else "",
                "tool_calls": openai_tool_calls,
            })
        elif role == "user" and tool_results:
            if text_parts:
                normalized.append({"role": "user", "content": "\n".join(text_parts)})
            for tr in tool_results:
                tr_content = tr.get("content", "")
                if isinstance(tr_content, list):
                    tr_texts = [
                        b.get("text", "")
                        for b in tr_content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    tr_content = "\n".join(tr_texts) if tr_texts else ""
                normalized.append({
                    "role": "tool",
                    "tool_call_id": tr.get("tool_use_id", ""),
                    "content": tr_content,
                })
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
            content.append({
                "type": "tool_use",
                "id": f"tu_{uuid.uuid4().hex[:20]}",
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
    model_name = body.get("model", "")
    messages = body.get("messages", [])
    system = body.get("system")
    tools = body.get("tools", [])
    thinking = body.get("thinking")

    # Strip claude- prefix if artificially added by our model list
    if model_name.startswith("claude-"):
        stripped = model_name[7:]
        if model_mapper._mapping_lookup_key(stripped) is not None:
            model_name = stripped

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
# Non-streaming -> Anthropic SSE (for tool_calls)
# =============================================================================

async def _stream_from_non_streaming(
    ollama_body: Dict[str, Any],
    display_model_name: str,
    username: str,
) -> StreamingResponse:
    """Get non-streaming response and emit as Anthropic SSE events."""
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
        content.append({
            "type": "tool_use",
            "id": f"tu_{uuid.uuid4().hex[:20]}",
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
    Consume Ollama OpenAI-compatible SSE and produce Anthropic SSE.
    """
    # When tools are present, use non-streaming path and convert to SSE events
    # because OpenAI streaming tool_calls are complex to map to Anthropic format
    if ollama_body.get("tools"):
        return await _stream_from_non_streaming(
            ollama_body, display_model_name, username
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
        # Proxy returned non-streaming; return as single content
        return StreamingResponse(
            _fallback_claude_stream(proxy_response, display_model_name),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    msg_id = f"msg_{uuid.uuid4().hex[:20]}"

    async def claude_stream_generator():
        """Generator that consumes Ollama SSE and produces Anthropic SSE events."""
        buffer = b""
        text_so_far = ""
        usage_stats: Dict[str, int] = {}
        finish_reason = None

        # Block indices: 0 = thinking, 1 = text
        thinking_index = 0
        text_index = 1
        thinking_started = False
        text_started = False

        # message_start
        yield (
            f"event: message_start\ndata: "
            + json.dumps(
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
                }
            )
            + "\n\n"
        )

        try:
            # Consume Ollama SSE stream via body_iterator
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

                    delta = choice.get("delta", {})
                    delta_content = delta.get("content")
                    delta_thinking = delta.get("reasoning") or delta.get("thinking")

                    # Emit thinking block (separate content block)
                    if delta_thinking:
                        if not thinking_started:
                            thinking_started = True
                            yield (
                                f"event: content_block_start\ndata: "
                                + json.dumps(
                                    {
                                        "type": "content_block_start",
                                        "index": thinking_index,
                                        "content_block": {"type": "thinking", "thinking": ""},
                                    }
                                )
                                + "\n\n"
                            )
                        yield (
                            f"event: content_block_delta\ndata: "
                            + json.dumps(
                                {
                                    "type": "content_block_delta",
                                    "index": thinking_index,
                                    "delta": {
                                        "type": "thinking_delta",
                                        "thinking": delta_thinking,
                                    },
                                }
                            )
                            + "\n\n"
                        )

                    # Emit text block (separate content block)
                    if delta_content is not None:
                        if not text_started:
                            text_started = True
                            yield (
                                f"event: content_block_start\ndata: "
                                + json.dumps(
                                    {
                                        "type": "content_block_start",
                                        "index": text_index,
                                        "content_block": {"type": "text", "text": ""},
                                    }
                                )
                                + "\n\n"
                            )
                        text_so_far += str(delta_content)
                        yield (
                            f"event: content_block_delta\ndata: "
                            + json.dumps(
                                {
                                    "type": "content_block_delta",
                                    "index": text_index,
                                    "delta": {
                                        "type": "text_delta",
                                        "text": str(delta_content),
                                    },
                                }
                            )
                            + "\n\n"
                        )

        except Exception as e:
            logger.error(f"[Claude] Streaming error: {e}", exc_info=True)
            yield (
                f"event: error\ndata: "
                + json.dumps(
                    {"type": "error", "error": {"type": "overloaded_error", "message": str(e)}}
                )
                + "\n\n"
            )

        # Ensure at least an empty text block exists for empty responses
        if not text_started:
            text_started = True
            yield (
                f"event: content_block_start\ndata: "
                + json.dumps(
                    {
                        "type": "content_block_start",
                        "index": text_index,
                        "content_block": {"type": "text", "text": ""},
                    }
                )
                + "\n\n"
            )

        # content_block_stop for thinking
        if thinking_started:
            yield (
                f"event: content_block_stop\ndata: "
                + json.dumps({"type": "content_block_stop", "index": thinking_index})
                + "\n\n"
            )

        # content_block_stop for text
        if text_started:
            yield (
                f"event: content_block_stop\ndata: "
                + json.dumps({"type": "content_block_stop", "index": text_index})
                + "\n\n"
            )

        # message_delta with mapped finish_reason
        out_tokens = usage_stats.get("completion_tokens", len(text_so_far) // 4)
        anthropic_stop = _map_finish_reason(finish_reason)
        yield (
            f"event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": anthropic_stop},
                    "usage": {"output_tokens": out_tokens},
                }
            )
            + "\n\n"
        )

        # message_stop
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

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
