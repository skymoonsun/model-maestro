"""AWS Bedrock proxy for Model Maestro

Translates OpenAI-compatible requests to AWS Bedrock Converse API
and vice versa for responses.
"""

import json
import logging
import asyncio
from typing import Dict, Any, Optional, List, Tuple, AsyncGenerator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / response translation helpers
# ---------------------------------------------------------------------------

def _openai_messages_to_bedrock(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI message format to Bedrock Converse content-blocks."""
    bedrock_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        if role not in ("user", "assistant"):
            role = "user"

        bedrock_content = []
        if isinstance(content, str):
            bedrock_content.append({"text": content})
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text" or "text" in part:
                        bedrock_content.append({"text": part.get("text", "")})
                    elif part.get("type") == "image_url" or "image_url" in part:
                        # Bedrock supports base64 images via image block
                        image_url = part.get("image_url", {})
                        url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                        if url.startswith("data:"):
                            # Parse data:image/jpeg;base64,<data>
                            header, b64data = url.split(",", 1)
                            media_type = header.split(";")[0].split(":")[1]
                            bedrock_content.append({
                                "image": {
                                    "format": media_type.split("/")[-1],
                                    "source": {"bytes": b64data}
                                }
                            })
                        else:
                            bedrock_content.append({"text": f"[Image: {url}]"})
        if not bedrock_content:
            bedrock_content.append({"text": ""})

        bedrock_messages.append({"role": role, "content": bedrock_content})
    return bedrock_messages


def _build_bedrock_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build Bedrock Converse request from OpenAI-compatible request data."""
    messages = data.get("messages", [])
    bedrock_messages = _openai_messages_to_bedrock(messages)

    request_body: Dict[str, Any] = {
        "messages": bedrock_messages,
    }

    # System prompt
    system_text = None
    for msg in messages:
        if msg.get("role") == "system":
            system_text = msg.get("content", "")
            if isinstance(system_text, list):
                system_text = " ".join(p.get("text", "") for p in system_text if isinstance(p, dict))
    if system_text:
        request_body["system"] = [{"text": system_text}]

    # Inference config
    inference_config: Dict[str, Any] = {}
    if "max_tokens" in data and data["max_tokens"] is not None:
        inference_config["maxTokens"] = int(data["max_tokens"])
    if "temperature" in data and data["temperature"] is not None:
        inference_config["temperature"] = float(data["temperature"])
    if "top_p" in data and data["top_p"] is not None:
        inference_config["topP"] = float(data["top_p"])
    if "stop" in data and data["stop"] is not None:
        stop = data["stop"]
        if isinstance(stop, str):
            inference_config["stopSequences"] = [stop]
        elif isinstance(stop, list):
            inference_config["stopSequences"] = stop
    if inference_config:
        request_body["inferenceConfig"] = inference_config

    # Tool config
    tools = data.get("tools")
    if tools:
        bedrock_tools = []
        for tool in tools:
            func = tool.get("function", {})
            if func:
                bedrock_tools.append({
                    "toolSpec": {
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "inputSchema": {"json": func.get("parameters", {})}
                    }
                })
        if bedrock_tools:
            request_body["toolConfig"] = {
                "tools": bedrock_tools,
                "toolChoice": {"auto": {}}
            }

    return request_body


def _bedrock_response_to_openai(
    response: Dict[str, Any],
    model_name: str,
    request_id: Optional[str] = None
) -> Dict[str, Any]:
    """Convert Bedrock Converse response to OpenAI-compatible response."""
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])

    text_parts = []
    tool_calls = []
    for block in content_blocks:
        if "text" in block:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            tool_use = block["toolUse"]
            tool_calls.append({
                "id": tool_use.get("toolUseId", "call_1"),
                "type": "function",
                "function": {
                    "name": tool_use.get("name", ""),
                    "arguments": json.dumps(tool_use.get("input", {}))
                }
            })

    choice = {
        "index": 0,
        "message": {
            "role": "assistant",
            "content": "\n".join(text_parts) if text_parts else None,
        },
        "finish_reason": "stop"
    }
    if tool_calls:
        choice["message"]["tool_calls"] = tool_calls
        choice["finish_reason"] = "tool_calls"

    usage = response.get("usage", {})
    openai_usage = {
        "prompt_tokens": usage.get("inputTokens", 0),
        "completion_tokens": usage.get("outputTokens", 0),
        "total_tokens": usage.get("totalTokens", 0)
    }

    return {
        "id": request_id or f"chatcmpl-bedrock-{id(response)}",
        "object": "chat.completion",
        "created": int(asyncio.get_event_loop().time()),
        "model": model_name,
        "choices": [choice],
        "usage": openai_usage
    }


# ---------------------------------------------------------------------------
# Streaming translation
# ---------------------------------------------------------------------------

def _bedrock_stream_to_sse(
    stream_events: Any,
    model_name: str,
    request_id: str
) -> AsyncGenerator[bytes, None]:
    """Convert Bedrock ConverseStream events to OpenAI SSE format."""
    import uuid

    accumulated_text = ""
    role: Optional[str] = None
    finish_reason: Optional[str] = None
    input_tokens = 0
    output_tokens = 0

    for event in stream_events:
        event_type = list(event.keys())[0] if event else None
        payload = event.get(event_type, {}) if event else {}

        if event_type == "messageStart":
            role = payload.get("role", "assistant")
            continue

        if event_type == "contentBlockDelta":
            delta = payload.get("delta", {})
            if "text" in delta:
                text = delta["text"]
                accumulated_text += text
                chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(asyncio.get_event_loop().time()),
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": text},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            continue

        if event_type == "contentBlockStop":
            continue

        if event_type == "messageStop":
            stop_reason = payload.get("stopReason")
            if stop_reason == "end_turn":
                finish_reason = "stop"
            elif stop_reason == "tool_use":
                finish_reason = "tool_calls"
            elif stop_reason == "max_tokens":
                finish_reason = "length"
            else:
                finish_reason = "stop"
            continue

        if event_type == "metadata":
            usage = payload.get("usage", {})
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)
            continue

    # Final chunk with finish_reason
    if finish_reason:
        final_chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(asyncio.get_event_loop().time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason
            }]
        }
        yield f"data: {json.dumps(final_chunk)}\n\n".encode("utf-8")

    # Usage chunk
    if input_tokens or output_tokens:
        usage_chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(asyncio.get_event_loop().time()),
            "model": model_name,
            "choices": [],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens
            }
        }
        yield f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8")

    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Proxy entry point
# ---------------------------------------------------------------------------

async def proxy_bedrock_request(
    data: Dict[str, Any],
    stream: bool,
    endpoint: str,
    base_url: str,
    access_key: str,
    secret_key: str,
    region: str,
    session_token: Optional[str],
    model_name: str,
    username: Optional[str],
    node_headers: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Proxy an OpenAI-compatible request to AWS Bedrock Converse API.
    """
    import boto3

    if endpoint not in ("/v1/chat/completions", "/cursor/chat/completions"):
        raise HTTPException(
            status_code=400,
            detail=f"Bedrock nodes only support chat completions endpoint, got: {endpoint}"
        )

    # Build credentials
    creds = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region,
    }
    if session_token:
        creds["aws_session_token"] = session_token

    client = boto3.client("bedrock-runtime", **creds)

    bedrock_request = _build_bedrock_request(data)

    logger.info(f"[Bedrock] Sending Converse request to model={model_name}, region={region}, stream={stream}")

    if stream:
        # Streaming path
        try:
            response = await asyncio.to_thread(
                client.converse_stream,
                modelId=model_name,
                messages=bedrock_request.get("messages", []),
                inferenceConfig=bedrock_request.get("inferenceConfig"),
                system=bedrock_request.get("system"),
                toolConfig=bedrock_request.get("toolConfig"),
            )
            stream_events = response.get("stream", [])

            request_id = f"chatcmpl-bedrock-{asyncio.get_event_loop().time():.0f}"

            async def _stream_generator():
                for chunk in _bedrock_stream_to_sse(stream_events, model_name, request_id):
                    yield chunk

            return StreamingResponse(
                _stream_generator(),
                media_type="text/event-stream; charset=utf-8",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                }
            )
        except Exception as e:
            logger.error(f"[Bedrock] Streaming error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Bedrock streaming error: {str(e)}")
    else:
        # Non-streaming path
        try:
            response = await asyncio.to_thread(
                client.converse,
                modelId=model_name,
                messages=bedrock_request.get("messages", []),
                inferenceConfig=bedrock_request.get("inferenceConfig"),
                system=bedrock_request.get("system"),
                toolConfig=bedrock_request.get("toolConfig"),
            )

            openai_response = _bedrock_response_to_openai(
                response, model_name, request_id=f"chatcmpl-bedrock-{asyncio.get_event_loop().time():.0f}"
            )
            return openai_response
        except Exception as e:
            logger.error(f"[Bedrock] Converse error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Bedrock error: {str(e)}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def health_check_bedrock(
    access_key: str,
    secret_key: str,
    region: str,
    session_token: Optional[str] = None,
    timeout: float = 5.0
) -> Tuple[bool, Optional[str]]:
    """Health check a Bedrock node by listing foundation models."""
    import boto3

    try:
        creds = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
        }
        if session_token:
            creds["aws_session_token"] = session_token

        bedrock_client = boto3.client("bedrock", **creds)
        await asyncio.wait_for(
            asyncio.to_thread(
                bedrock_client.list_foundation_models,
                byOutputModality="TEXT"
            ),
            timeout=timeout
        )
        return True, None
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"[Bedrock] Health check failed: {error_msg}")
        return False, error_msg


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------

async def discover_bedrock_models(
    access_key: str,
    secret_key: str,
    region: str,
    session_token: Optional[str] = None
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Discover available Bedrock foundation models."""
    import boto3

    try:
        creds = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
        }
        if session_token:
            creds["aws_session_token"] = session_token

        bedrock_client = boto3.client("bedrock", **creds)
        response = await asyncio.to_thread(
            bedrock_client.list_foundation_models,
            byOutputModality="TEXT"
        )

        models = []
        for summary in response.get("modelSummaries", []):
            model_id = summary.get("modelId", "")
            models.append({
                "name": model_id,
                "size": None,
                "digest": None,
                "modified_at": None,
                "details": summary,
                "family": summary.get("providerName", "").lower(),
            })

        return True, models, None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Bedrock] Model discovery failed: {error_msg}")
        return False, [], error_msg
