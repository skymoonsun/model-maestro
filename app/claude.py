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

    # Get all models from Ollama
    all_models_response = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/api/tags",
        username=username,
    )

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
                for display_name in display_names:
                    ctx_len = get_context_length_for_model(display_name) or 131072
                    models_list.append({
                        "type": "model",
                        "id": f"claude-{display_name}",
                        "display_name": display_name,
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
    # Strip claude- prefix added by the model list for Claude Code compatibility
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
                *messages,
            ]
    else:
        ollama_body["messages"] = messages

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
    proxy_response = await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=ollama_body,
        stream=True,
        username=username,
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
        has_started_content = False

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

        # content_block_start (text)
        yield (
            f"event: content_block_start\ndata: "
            + json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            + "\n\n"
        )

        try:
            # Consume Ollama SSE stream via body_iterator
            async for chunk in proxy_response.body_iterator:
                buffer += chunk
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

                    delta = choices[0].get("delta", {})
                    # OpenAI delta may contain content, reasoning, or tool_calls
                    delta_content = delta.get("content", "")
                    delta_thinking = delta.get("reasoning") or delta.get("thinking")

                    if delta_thinking and not has_started_content:
                        # Claude thinking block
                        yield (
                            f"event: content_block_delta\ndata: "
                            + json.dumps(
                                {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "thinking_delta", "thinking": delta_thinking},
                                }
                            )
                            + "\n\n"
                        )

                    if delta_content is not None:
                        has_started_content = True
                        text_so_far += str(delta_content)
                        yield (
                            f"event: content_block_delta\ndata: "
                            + json.dumps(
                                {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": str(delta_content)},
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

        # content_block_stop
        yield (
            f"event: content_block_stop\ndata: "
            + json.dumps({"type": "content_block_stop", "index": 0})
            + "\n\n"
        )

        # message_delta
        out_tokens = usage_stats.get("completion_tokens", len(text_so_far) // 4)
        yield (
            f"event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
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
