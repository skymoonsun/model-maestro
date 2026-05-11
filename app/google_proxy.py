"""Google v1internal API proxy for Antigravity nodes.

Handles:
- OpenAI format -> Google v1internal format transformation
- Google v1internal -> OpenAI format response transformation
- SSE streaming for streamGenerateContent?alt=sse
- Endpoint fallback (Sandbox -> Daily -> Prod)
- Model discovery via fetchAvailableModels
- Token health validation
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple, AsyncGenerator
from datetime import datetime

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.google_auth import (
    V1_INTERNAL_BASE_URLS,
    build_v1internal_headers,
    ensure_fresh_token,
    get_current_version,
    get_user_agent,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Request Transformation: OpenAI -> Google v1internal
# =============================================================================

def _convert_messages_to_contents(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Convert OpenAI messages to Google contents format.

    Returns:
        Tuple of (contents, system_instructions)
    """
    system_instructions: List[str] = []
    contents: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role in ("system", "developer"):
            if isinstance(content, str):
                system_instructions.append(content)
            elif isinstance(content, list):
                texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
                if texts:
                    system_instructions.append("\n".join(texts))
            continue

        # Map roles: assistant -> model, tool/function -> user
        google_role = {"assistant": "model", "tool": "user", "function": "user"}.get(role, role)
        parts: List[Dict[str, Any]] = []

        # Handle reasoning_content (thinking blocks)
        reasoning = msg.get("reasoning_content")
        if reasoning and isinstance(reasoning, str) and reasoning != "[undefined]":
            parts.append({"text": reasoning, "thought": True})

        # Handle content
        if isinstance(content, str):
            if content:
                parts.append({"text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append({"text": text})
                elif block_type == "image_url":
                    image_url = block.get("image_url", {})
                    url = image_url.get("url", "")
                    if url.startswith("data:"):
                        comma_pos = url.find(",")
                        if comma_pos != -1:
                            mime_part = url[5:comma_pos]
                            mime_type = mime_part.split(";")[0] if ";" in mime_part else mime_part
                            data = url[comma_pos + 1:]
                            parts.append({"inlineData": {"mimeType": mime_type or "image/jpeg", "data": data}})
                    elif url.startswith("http"):
                        parts.append({"fileData": {"fileUri": url, "mimeType": "image/jpeg"}})

        # Handle tool calls (assistant message)
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function", {})
                if not isinstance(func, dict):
                    continue
                name = func.get("name", "")
                arguments = func.get("arguments", "{}")
                call_id = tc.get("id", "")

                # Normalize shell tool name
                if name == "local_shell_call":
                    name = "shell"

                try:
                    args_parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (json.JSONDecodeError, TypeError):
                    args_parsed = {}

                func_call_part = {
                    "functionCall": {
                        "name": name,
                        "args": args_parsed,
                        "id": call_id,
                    }
                }
                parts.append(func_call_part)

        # Handle tool response
        if role in ("tool", "function"):
            name = msg.get("name", "unknown")
            if name == "local_shell_call":
                name = "shell"
            tool_call_id = msg.get("tool_call_id", "")

            content_val = ""
            if isinstance(content, str):
                content_val = content
            elif isinstance(content, list):
                texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
                content_val = "\n".join(texts)

            parts.append({
                "functionResponse": {
                    "name": name,
                    "response": {"result": content_val},
                    "id": tool_call_id,
                }
            })

        if parts:
            contents.append({"role": google_role, "parts": parts})

    # Merge consecutive same-role messages (Gemini requires alternating user/model)
    merged: List[Dict[str, Any]] = []
    for msg in contents:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["parts"].extend(msg["parts"])
        else:
            merged.append(msg)

    return merged, system_instructions


def _convert_tools_to_gemini(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tools to Gemini functionDeclarations format."""
    result: List[Dict[str, Any]] = []

    for tool in tools:
        if not isinstance(tool, dict):
            continue

        func = tool.get("function", {})
        if not isinstance(func, dict):
            # Handle nested format
            func_name = tool.get("name", "")
            func_params = tool.get("parameters", {})
            if func_name:
                func = {"name": func_name, "parameters": func_params, "description": tool.get("description", "")}
            else:
                continue

        name = func.get("name", "")
        parameters = func.get("parameters", {})
        description = func.get("description", "")

        if not name:
            continue

        # Clean forbidden schema fields
        if isinstance(parameters, dict):
            parameters = _clean_json_schema(parameters.copy())

        result.append({
            "name": name,
            "description": description,
            "parameters": parameters,
        })

    if result:
        return [{"functionDeclarations": result}]
    return []


def _clean_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Remove Gemini-incompatible JSON Schema fields."""
    if not isinstance(schema, dict):
        return schema

    forbidden = {"multipleOf", "pattern", "exclusiveMinimum", "exclusiveMaximum"}
    cleaned = {}
    for k, v in schema.items():
        if k in forbidden:
            continue
        if isinstance(v, dict):
            cleaned[k] = _clean_json_schema(v)
        elif isinstance(v, list):
            cleaned[k] = [_clean_json_schema(i) if isinstance(i, dict) else i for i in v]
        else:
            cleaned[k] = v
    return cleaned


def transform_openai_to_google(data: Dict[str, Any]) -> Dict[str, Any]:
    """Transform OpenAI chat completion request to Google v1internal body format.

    Returns the inner request body (contents, generationConfig, systemInstruction, tools).
    This must be wrapped by wrap_v1internal_request before sending.
    """
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        messages = []

    contents, system_instructions = _convert_messages_to_contents(messages)

    # Build generationConfig
    gen_config: Dict[str, Any] = {
        "temperature": data.get("temperature", 1.0),
        "topP": data.get("top_p", 1.0),
        "topK": 40,
    }

    max_tokens = data.get("max_tokens") or data.get("max_completion_tokens")
    if max_tokens:
        gen_config["maxOutputTokens"] = max_tokens

    # Handle thinking models
    model_name = (data.get("model") or "").lower()
    is_thinking_model = (
        "-thinking" in model_name
        or "gemini-2.0-pro" in model_name
        or "gemini-3-pro" in model_name
        or "gemini-3.1-pro" in model_name
        or "gemini-3-flash" in model_name
        or "gemini-3.1-flash" in model_name
    ) and "claude" not in model_name

    is_claude_thinking = model_name.endswith("-thinking")

    if is_thinking_model or is_claude_thinking:
        thinking_budget = 24576
        gen_config["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": thinking_budget,
        }
        # Claude thinking requires max_tokens > thinking.budget_tokens
        current_max = gen_config.get("maxOutputTokens", 0)
        if not current_max or current_max <= thinking_budget:
            gen_config["maxOutputTokens"] = thinking_budget + 4096

    # Handle n -> candidateCount
    n = data.get("n")
    if n:
        gen_config["candidateCount"] = n

    result: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": gen_config,
    }

    # System instruction
    if system_instructions:
        result["systemInstruction"] = {
            "role": "user",
            "parts": [{"text": "\n\n".join(system_instructions)}],
        }

    # Tools
    tools = data.get("tools")
    if tools and isinstance(tools, list):
        gemini_tools = _convert_tools_to_gemini(tools)
        if gemini_tools:
            result["tools"] = gemini_tools

    # Tool choice
    tool_choice = data.get("tool_choice")
    if tool_choice == "auto":
        pass  # Default
    elif tool_choice == "none":
        result.pop("tools", None)
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        func_name = tool_choice.get("function", {}).get("name", "")
        if func_name:
            result["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [func_name],
                }
            }

    return result


def wrap_v1internal_request(
    body: Dict[str, Any],
    project_id: str,
    mapped_model: str,
) -> Dict[str, Any]:
    """Wrap the inner Google request body with v1internal envelope fields.

    The Rust code wraps with:
    - project: project_id
    - requestId: UUID
    - request: {contents, generationConfig, systemInstruction, tools, ...}
    - model: mapped_model
    - userAgent: user agent string
    - requestType: "generate_content" | "stream_generate_content"
    """
    request_type = "stream_generate_content" if "stream" in str(body) else "generate_content"
    # Actually we should determine this from the caller, but default to generate_content
    # The caller will override requestType if needed

    return {
        "project": project_id,
        "requestId": str(uuid.uuid4()),
        "request": body,
        "model": mapped_model,
        "userAgent": get_user_agent(),
        "requestType": request_type,
    }


# =============================================================================
# Response Transformation: Google -> OpenAI
# =============================================================================

def _extract_usage_metadata(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract usage metadata from Google response."""
    usage = raw.get("usageMetadata")
    if not usage:
        return None

    prompt_tokens = usage.get("promptTokenCount", 0)
    completion_tokens = usage.get("candidatesTokenCount", 0)
    total_tokens = usage.get("totalTokenCount", 0)
    cached_tokens = usage.get("cachedContentTokenCount")

    result: Dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

    if cached_tokens is not None:
        result["prompt_tokens_details"] = {"cached_tokens": cached_tokens}

    return result


def transform_google_to_openai(
    gemini_response: Dict[str, Any],
    model_name: str,
) -> Dict[str, Any]:
    """Transform Google v1internal response to OpenAI chat.completion format."""
    # Unwrap the response envelope if present
    raw = gemini_response.get("response", gemini_response)

    choices: List[Dict[str, Any]] = []
    candidates = raw.get("candidates", [])

    for idx, candidate in enumerate(candidates):
        content_out = ""
        thought_out = ""
        tool_calls: List[Dict[str, Any]] = []

        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            is_thought = part.get("thought", False)

            text = part.get("text", "")
            if text:
                if is_thought:
                    thought_out += text
                else:
                    content_out += text

            # Image inline data
            inline_data = part.get("inlineData")
            if inline_data:
                mime = inline_data.get("mimeType", "image/png")
                data = inline_data.get("data", "")
                if data:
                    content_out += f"![image](data:{mime};base64,{data})"

            # Function call
            func_call = part.get("functionCall")
            if func_call:
                name = func_call.get("name", "unknown")
                args = func_call.get("args", {})
                call_id = func_call.get("id", f"{name}-{uuid.uuid4().hex[:8]}")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                    },
                })

        # Grounding metadata
        grounding = candidate.get("groundingMetadata")
        if grounding:
            queries = grounding.get("webSearchQueries", [])
            if queries:
                content_out += "\n\n---\n**Searched:** " + ", ".join(queries)

            chunks = grounding.get("groundingChunks", [])
            if chunks:
                links = []
                for i, chunk in enumerate(chunks):
                    web = chunk.get("web", {})
                    title = web.get("title", "Source")
                    uri = web.get("uri", "#")
                    links.append(f"[{i + 1}] [{title}]({uri})")
                if links:
                    content_out += "\n\n**Sources:**\n" + "\n".join(links)

        # Finish reason mapping
        finish_reason = candidate.get("finishReason", "STOP")
        finish_map = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }
        mapped_finish = finish_map.get(finish_reason, "stop")

        # If tool calls exist, finish_reason should be tool_calls
        if tool_calls and mapped_finish == "stop":
            mapped_finish = "tool_calls"

        message: Dict[str, Any] = {
            "role": "assistant",
            "content": content_out if content_out else None,
        }
        if thought_out:
            message["reasoning_content"] = thought_out
        if tool_calls:
            message["tool_calls"] = tool_calls

        choices.append({
            "index": idx,
            "message": message,
            "finish_reason": mapped_finish,
        })

    response: Dict[str, Any] = {
        "id": raw.get("responseId", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
        "object": "chat.completion",
        "created": int(datetime.utcnow().timestamp()),
        "model": raw.get("modelVersion", model_name),
        "choices": choices,
    }

    usage = _extract_usage_metadata(raw)
    if usage:
        response["usage"] = usage

    return response


# =============================================================================
# v1internal API Client with Fallback
# =============================================================================

async def call_v1internal(
    method: str,
    access_token: str,
    body: Dict[str, Any],
    query_string: Optional[str] = None,
    project_id: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any], str]:
    """Call Google v1internal API with automatic endpoint fallback.

    Returns:
        Tuple of (status_code, response_json_or_error, final_url_used)
    """
    headers = build_v1internal_headers(access_token, extra_headers)

    # NOTE: x-goog-user-project header causes 403 PERMISSION_DENIED on v1internal.
    # The project_id is already sent in the request body envelope under "project".
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

    last_err = ""
    has_triggered_downgrade = False
    header_removed_for_403 = False

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0),
        http2=True,
    ) as client:
        while True:
            for idx, base_url in enumerate(V1_INTERNAL_BASE_URLS):
                if query_string:
                    url = f"{base_url}:{method}?{query_string}"
                else:
                    url = f"{base_url}:{method}"

                has_next = idx + 1 < len(V1_INTERNAL_BASE_URLS)

                try:
                    # For streamGenerateContent, use chunked body to avoid Content-Length issues
                    if method == "streamGenerateContent":
                        response = await client.post(
                            url,
                            headers=headers,
                            content=body_bytes,
                        )
                    else:
                        response = await client.post(
                            url,
                            headers=headers,
                            json=body,
                        )

                    if response.status_code == 200:
                        try:
                            resp_json = response.json()
                        except (json.JSONDecodeError, ValueError):
                            resp_json = {"raw_text": response.text}
                        return response.status_code, resp_json, url

                    # Log every non-200 response for debugging
                    logger.warning(
                        f"[v1internal] {url} returned {response.status_code}: {response.text[:500]}"
                    )

                    # Handle 403 with project header -> retry without it once
                    if (
                        response.status_code == 403
                        and not has_triggered_downgrade
                        and "x-goog-user-project" in headers
                    ):
                        logger.warning(
                            "Detected 403 with project header, retrying WITHOUT x-goog-user-project"
                        )
                        headers.pop("x-goog-user-project", None)
                        has_triggered_downgrade = True
                        header_removed_for_403 = True
                        break  # Restart from first endpoint

                    # Retryable status codes
                    if has_next and response.status_code in (429, 408, 404, 500, 502, 503, 504):
                        err_msg = f"Upstream {url} returned {response.status_code}"
                        logger.warning(err_msg)
                        last_err = err_msg
                        continue

                    # Non-retryable or last endpoint
                    try:
                        resp_json = response.json()
                    except (json.JSONDecodeError, ValueError):
                        resp_json = {"error": response.text}
                    return response.status_code, resp_json, url

                except httpx.RequestError as e:
                    err_msg = f"HTTP request failed at {url}: {e}"
                    logger.warning(err_msg)
                    last_err = err_msg
                    if not has_next:
                        break
                    continue

            # If we broke out due to 403 downgrade, retry all endpoints once more
            if header_removed_for_403:
                header_removed_for_403 = False
                continue
            else:
                break

    return 503, {"error": last_err or "All endpoints failed"}, ""


# =============================================================================
# Streaming: Google SSE -> OpenAI SSE
# =============================================================================

async def stream_google_to_openai(
    gemini_stream: AsyncGenerator[bytes, None],
    model_name: str,
) -> AsyncGenerator[bytes, None]:
    """Transform Google SSE stream to OpenAI SSE format."""
    stream_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(datetime.utcnow().timestamp())

    buffer = b""
    final_usage: Optional[Dict[str, Any]] = None
    error_occurred = False
    emitted_tool_calls: set = set()
    tool_call_index = 0

    async for chunk in gemini_stream:
        if not chunk:
            continue

        buffer += chunk

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line:
                continue

            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str.startswith("data: "):
                continue

            json_part = line_str[6:].strip()
            if json_part == "[DONE]":
                continue

            try:
                data = json.loads(json_part)
            except (json.JSONDecodeError, ValueError):
                continue

            # Unwrap response envelope if present
            actual_data = data.get("response", data)

            # Extract usage metadata
            usage_meta = actual_data.get("usageMetadata")
            if usage_meta:
                final_usage = _extract_usage_metadata(actual_data)

            candidates = actual_data.get("candidates", [])
            if not candidates:
                continue

            for idx, candidate in enumerate(candidates):
                parts = candidate.get("content", {}).get("parts", [])
                content_out = ""
                thought_out = ""
                tool_calls: List[Dict[str, Any]] = []

                for part in parts:
                    is_thought = part.get("thought", False)

                    text = part.get("text", "")
                    if text:
                        if is_thought:
                            thought_out += text
                        else:
                            content_out += text

                    # Image
                    inline_data = part.get("inlineData")
                    if inline_data:
                        mime = inline_data.get("mimeType", "image/png")
                        data_b64 = inline_data.get("data", "")
                        if data_b64:
                            content_out += f"![image](data:{mime};base64,{data_b64})"

                    # Function call
                    func_call = part.get("functionCall")
                    if func_call:
                        call_key = json.dumps(func_call, sort_keys=True)
                        if call_key not in emitted_tool_calls:
                            emitted_tool_calls.add(call_key)
                            name = func_call.get("name", "unknown")
                            args = func_call.get("args", {})
                            args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                            call_id = func_call.get("id", f"call_{name}_{tool_call_index}")

                            tool_calls.append({
                                "index": tool_call_index,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": args_str,
                                },
                            })
                            tool_call_index += 1

                # Grounding metadata
                grounding = candidate.get("groundingMetadata")
                if grounding:
                    queries = grounding.get("webSearchQueries", [])
                    if queries:
                        content_out += "\n\n---\n**Searched:** " + ", ".join(queries)

                    chunks = grounding.get("groundingChunks", [])
                    if chunks:
                        links = []
                        for i, chunk in enumerate(chunks):
                            web = chunk.get("web", {})
                            title = web.get("title", "Source")
                            uri = web.get("uri", "#")
                            links.append(f"[{i + 1}] [{title}]({uri})")
                        if links:
                            content_out += "\n\n**Sources:**\n" + "\n".join(links)

                # Finish reason
                finish_reason = candidate.get("finishReason", "STOP")
                finish_map = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "content_filter", "RECITATION": "content_filter"}
                mapped_finish = finish_map.get(finish_reason, "stop")

                if tool_calls and mapped_finish == "stop":
                    mapped_finish = "tool_calls"

                # Yield reasoning first
                if thought_out:
                    reasoning_chunk = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [{
                            "index": idx,
                            "delta": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": thought_out,
                            },
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(reasoning_chunk)}\n\n".encode("utf-8")

                # Yield content / tool calls
                delta: Dict[str, Any] = {}
                if content_out:
                    delta["content"] = content_out
                if tool_calls:
                    delta["tool_calls"] = tool_calls

                if delta or mapped_finish != "stop":
                    openai_chunk: Dict[str, Any] = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [{
                            "index": idx,
                            "delta": delta if delta else {},
                            "finish_reason": mapped_finish if mapped_finish != "stop" else None,
                        }],
                    }

                    if mapped_finish != "stop" and final_usage:
                        openai_chunk["usage"] = final_usage
                        final_usage = None

                    yield f"data: {json.dumps(openai_chunk)}\n\n".encode("utf-8")

    if not error_occurred:
        yield b"data: [DONE]\n\n"


# =============================================================================
# Main Proxy Entry Point
# =============================================================================

async def proxy_antigravity_request(
    data: Dict[str, Any],
    stream: bool,
    endpoint: str,
    base_url: str,
    oauth_tokens: Dict[str, Any],
    project_id: Optional[str],
    node_headers: Optional[Dict[str, Any]],
    model_name: str,
    username: Optional[str],
) -> Any:
    """Proxy a request to Google v1internal API (Antigravity node).

    Returns:
        StreamingResponse for streaming, or JSON dict for non-streaming.
    """
    from app.google_auth import ensure_fresh_token

    # Ensure token is fresh
    try:
        access_token = await ensure_fresh_token(
            oauth_tokens,
            client_id="",  # Not needed for refresh if refresh_token exists
            client_secret="",
        )
    except Exception as e:
        logger.error(f"[Antigravity] Token refresh failed: {e}")
        raise HTTPException(status_code=401, detail=f"Google OAuth token expired or invalid: {e}")

    if not project_id:
        logger.warning("[Antigravity] No project_id configured for node; will attempt without x-goog-user-project header")

    # Transform OpenAI -> Google
    google_body = transform_openai_to_google(data)

    # Determine method and query string
    if stream:
        method = "streamGenerateContent"
        query_string = "alt=sse"
    else:
        method = "generateContent"
        query_string = None

    # Wrap with v1internal envelope
    wrapped = wrap_v1internal_request(google_body, project_id, model_name)
    wrapped["requestType"] = "stream_generate_content" if stream else "generate_content"

    logger.info(f"[Antigravity] Forwarding to Google v1internal ({method}), model={model_name}, stream={stream}")

    if stream:
        # Streaming response
        headers = build_v1internal_headers(access_token)
        if node_headers:
            headers.update(node_headers)
        # NOTE: x-goog-user-project omitted — it causes 403; project is in body envelope

        body_bytes = json.dumps(wrapped, ensure_ascii=False).encode("utf-8")

        async def antigravity_stream_generator() -> AsyncGenerator[bytes, None]:
            last_err = ""
            has_triggered_downgrade = False
            header_removed_for_403 = False

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=30.0),
                http2=True,
            ) as client:
                while True:
                    for idx, base_url in enumerate(V1_INTERNAL_BASE_URLS):
                        if query_string:
                            url = f"{base_url}:{method}?{query_string}"
                        else:
                            url = f"{base_url}:{method}"

                        has_next = idx + 1 < len(V1_INTERNAL_BASE_URLS)

                        try:
                            async with client.stream(
                                "POST",
                                url,
                                headers=headers,
                                content=body_bytes,
                            ) as resp:
                                if resp.status_code == 200:
                                    async for chunk in stream_google_to_openai(
                                        resp.aiter_raw(),
                                        model_name,
                                    ):
                                        yield chunk
                                    return

                                if (
                                    resp.status_code == 403
                                    and not has_triggered_downgrade
                                    and "x-goog-user-project" in headers
                                ):
                                    logger.warning("[Antigravity] 403 with project header, retrying without")
                                    headers.pop("x-goog-user-project", None)
                                    has_triggered_downgrade = True
                                    header_removed_for_403 = True
                                    break

                                if has_next and resp.status_code in (429, 408, 404, 500, 502, 503, 504):
                                    err_msg = f"Upstream {url} returned {resp.status_code}"
                                    logger.warning(f"[Antigravity] {err_msg}")
                                    last_err = err_msg
                                    continue

                                # Non-retryable error
                                error_text = await resp.aread()
                                try:
                                    error_json = json.loads(error_text.decode("utf-8", errors="replace"))
                                    error_detail = error_json.get("error", error_text.decode("utf-8", errors="replace"))
                                except (json.JSONDecodeError, ValueError):
                                    error_detail = error_text.decode("utf-8", errors="replace")

                                logger.error(f"[Antigravity] Upstream error {resp.status_code}: {error_detail}")
                                error_chunk = {
                                    "error": {
                                        "message": f"Google v1internal error: {error_detail}",
                                        "type": "api_error",
                                        "code": resp.status_code,
                                    }
                                }
                                yield f"data: {json.dumps(error_chunk)}\n\n".encode("utf-8")
                                yield b"data: [DONE]\n\n"
                                return

                        except httpx.RequestError as e:
                            err_msg = f"HTTP request failed at {url}: {e}"
                            logger.warning(f"[Antigravity] {err_msg}")
                            last_err = err_msg
                            if not has_next:
                                break
                            continue

                    if header_removed_for_403:
                        header_removed_for_403 = False
                        continue
                    else:
                        break

            # All endpoints failed
            logger.error(f"[Antigravity] All endpoints failed: {last_err}")
            error_chunk = {
                "error": {
                    "message": f"All Google v1internal endpoints failed: {last_err}",
                    "type": "api_error",
                    "code": 503,
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            antigravity_stream_generator(),
            media_type="text/event-stream",
        )

    else:
        # Non-streaming response
        status_code, resp_json, url = await call_v1internal(
            method="generateContent",
            access_token=access_token,
            body=wrapped,
            query_string=None,
            project_id=project_id,
            extra_headers=node_headers,
        )

        if status_code != 200:
            error_detail = resp_json.get("error", str(resp_json))
            if isinstance(error_detail, dict):
                error_msg = error_detail.get("message", str(error_detail))
            else:
                error_msg = str(error_detail)
            logger.error(f"[Antigravity] Upstream error {status_code}: {error_msg}")
            raise HTTPException(
                status_code=status_code if status_code < 500 else 502,
                detail=f"Google v1internal error: {error_msg}",
            )

        openai_response = transform_google_to_openai(resp_json, model_name)
        return openai_response


# =============================================================================
# Model Discovery
# =============================================================================

async def discover_antigravity_models(
    oauth_tokens: Dict[str, Any],
    project_id: Optional[str],
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Discover models from Google v1internal fetchAvailableModels.

    Returns:
        Tuple of (success, models_list, error_message)
    """
    try:
        access_token = await ensure_fresh_token(
            oauth_tokens,
            client_id="",
            client_secret="",
        )
    except Exception as e:
        return False, [], f"Token refresh failed: {e}"

    status_code, resp_json, url = await call_v1internal(
        method="fetchAvailableModels",
        access_token=access_token,
        body={},
        query_string=None,
        project_id=project_id,
    )

    if status_code != 200:
        return False, [], f"fetchAvailableModels failed: HTTP {status_code} - {resp_json}"

    # Parse models from response
    models: List[Dict[str, Any]] = []
    data = resp_json.get("response", resp_json)

    # The response format is not fully documented; try common patterns
    model_list = data.get("models", []) or data.get("data", [])
    if not model_list and isinstance(data, list):
        model_list = data

    for model in model_list:
        if isinstance(model, str):
            models.append({
                "name": model,
                "size": None,
                "digest": None,
                "modified_at": None,
                "details": {},
                "family": None,
            })
        elif isinstance(model, dict):
            name = model.get("name") or model.get("id") or model.get("model")
            if name:
                models.append({
                    "name": name,
                    "size": None,
                    "digest": None,
                    "modified_at": None,
                    "details": model,
                    "family": None,
                })

    return True, models, None


# =============================================================================
# Health Check
# =============================================================================

async def health_check_antigravity(
    oauth_tokens: Dict[str, Any],
    timeout: float = 15.0,
) -> Tuple[bool, Optional[str]]:
    """Check if an antigravity node is healthy by verifying token validity.

    Returns:
        Tuple of (is_healthy, error_message)
    """
    try:
        access_token = await ensure_fresh_token(
            oauth_tokens,
            client_id="",
            client_secret="",
        )
    except Exception as e:
        return False, f"Token refresh failed: {e}"

    # Verify token works with a lightweight Google API call
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            http2=True,
        ) as client:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=timeout,
            )

            if response.status_code == 200:
                return True, None
            else:
                return False, f"Token validation failed: HTTP {response.status_code}"

    except httpx.TimeoutException:
        return False, "Request timeout"
    except httpx.ConnectError as e:
        return False, f"Connection error: {str(e)}"
    except Exception as e:
        return False, str(e)
