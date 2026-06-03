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

# Bedrock Converse API tool ID regex: ^[a-zA-Z0-9_-]+$
# Claude Code sends base64-like IDs; strip anything outside the allowed set.
_TOOL_ID_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _sanitize_tool_id(tool_id: str) -> str:
    """Strip characters Bedrock rejects from tool IDs, then truncate to 64 chars."""
    if not tool_id:
        return tool_id
    cleaned = "".join(c for c in tool_id if c in _TOOL_ID_ALLOWED)
    cleaned = cleaned[:64]
    if not cleaned:
        cleaned = f"call_{__import__('uuid').uuid4().hex[:12]}"
    return cleaned


# Serialize boto3 Bedrock API-key auth (uses process-wide AWS_BEARER_TOKEN_BEDROCK).
_BEDROCK_API_KEY_LOCK = asyncio.Lock()

# Prevent the default AWS credential chain from overriding Bedrock API keys.
_IAM_ENV_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
)


def resolve_bedrock_auth_mode(
    bedrock_auth_mode: Optional[str],
    secret_key: Optional[str],
) -> str:
    """Return 'iam' or 'api_key'."""
    if bedrock_auth_mode in ("iam", "api_key"):
        return bedrock_auth_mode
    if secret_key and str(secret_key).strip():
        return "iam"
    return "api_key"


def bedrock_credentials_configured(
    *,
    api_key: Optional[str],
    secret_key: Optional[str],
    region: Optional[str],
    bedrock_auth_mode: Optional[str] = None,
) -> bool:
    if not region or not str(region).strip():
        return False
    mode = resolve_bedrock_auth_mode(bedrock_auth_mode, secret_key)
    if mode == "iam":
        return bool(api_key and str(api_key).strip() and secret_key and str(secret_key).strip())
    return bool(api_key and str(api_key).strip())


def _bedrock_control_plane_url(region: str) -> str:
    return f"https://bedrock.{region}.amazonaws.com"


def _bedrock_runtime_url(region: str) -> str:
    return f"https://bedrock-runtime.{region}.amazonaws.com"


# Inference profile IDs (e.g. us.anthropic.claude-*) are required for many on-demand models.
_INFERENCE_PROFILE_ID_PREFIXES = ("us.", "eu.", "apac.", "global.", "us-gov.", "au.")


def is_bedrock_inference_profile_id(model_id: str) -> bool:
    """True if model_id is already an inference profile identifier."""
    mid = (model_id or "").strip()
    if not mid:
        return False
    if mid.startswith("arn:") and "inference-profile" in mid:
        return True
    return mid.startswith(_INFERENCE_PROFILE_ID_PREFIXES)


def bedrock_region_geo_prefix(region: str) -> str:
    """Geo prefix for cross-region inference profiles (us., eu., apac., …)."""
    r = (region or "us-east-1").strip().lower()
    if r.startswith("us-gov"):
        return "us-gov"
    if r.startswith("eu-"):
        return "eu"
    if r.startswith("ap-"):
        return "apac"
    if r.startswith("us-") or r.startswith("ca-") or r.startswith("sa-"):
        return "us"
    return "us"


def bedrock_heuristic_inference_profile_id(foundation_model_id: str, region: str) -> str:
    """Best-effort profile ID when ListInferenceProfiles is unavailable."""
    return f"{bedrock_region_geo_prefix(region)}.{foundation_model_id}"


def resolve_bedrock_converse_model_id(
    model_id: str,
    region: str,
    profile_map: Optional[Dict[str, str]] = None,
) -> str:
    """
    Map a foundation model ID to the inference profile ID required by Converse.

    Newer models (e.g. Claude Opus 4.6) reject raw foundation model IDs for on-demand throughput.
    """
    mid = (model_id or "").strip()
    if not mid:
        return mid
    if is_bedrock_inference_profile_id(mid):
        return mid
    if profile_map and mid in profile_map:
        return profile_map[mid]
    return bedrock_heuristic_inference_profile_id(mid, region)


def _foundation_model_id_from_arn(model_arn: str) -> Optional[str]:
    if not model_arn:
        return None
    marker = "/foundation-model/"
    if marker in model_arn:
        return model_arn.split(marker, 1)[1]
    return None


def _parse_inference_profile_summaries(
    payload: Dict[str, Any],
    region: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Parse ListInferenceProfiles response into catalog models + foundation→profile map."""
    models: List[Dict[str, Any]] = []
    foundation_to_profile: Dict[str, str] = {}
    geo = bedrock_region_geo_prefix(region)

    for summary in payload.get("inferenceProfileSummaries", []):
        profile_id = summary.get("inferenceProfileId", "")
        if not profile_id:
            continue

        foundation_ids: List[str] = []
        for entry in summary.get("models", []):
            fid = _foundation_model_id_from_arn(entry.get("modelArn", ""))
            if not fid:
                continue
            foundation_ids.append(fid)
            existing = foundation_to_profile.get(fid)
            if not existing:
                foundation_to_profile[fid] = profile_id
            elif profile_id.startswith(f"{geo}.") and not existing.startswith(f"{geo}."):
                foundation_to_profile[fid] = profile_id

        provider = "anthropic"
        if "amazon" in profile_id or "nova" in profile_id:
            provider = "amazon"
        elif "meta" in profile_id:
            provider = "meta"

        models.append({
            "name": profile_id,
            "size": None,
            "digest": None,
            "modified_at": None,
            "details": {
                **summary,
                "foundation_model_ids": foundation_ids,
                "inference_profile_id": profile_id,
            },
            "family": provider,
        })

    return models, foundation_to_profile


def _parse_foundation_model_summaries(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    models = []
    for summary in payload.get("modelSummaries", []):
        model_id = summary.get("modelId", "")
        if not model_id:
            continue
        models.append({
            "name": model_id,
            "size": None,
            "digest": None,
            "modified_at": None,
            "details": summary,
            "family": (summary.get("providerName") or "").lower(),
        })
    return models


async def _list_inference_profiles_http(
    api_key: str,
    region: str,
    timeout: float,
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, str], Optional[str]]:
    """List SYSTEM_DEFINED inference profiles (Bearer auth)."""
    import httpx

    url = f"{_bedrock_control_plane_url(region)}/inference-profiles"
    all_summaries: List[Dict[str, Any]] = []
    next_token: Optional[str] = None

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                params: Dict[str, Any] = {
                    "typeEquals": "SYSTEM_DEFINED",
                    "maxResults": 1000,
                }
                if next_token:
                    params["nextToken"] = next_token
                response = await client.get(
                    url,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                    },
                )
                if response.status_code != 200:
                    body = (response.text or "")[:500]
                    return False, [], {}, f"HTTP {response.status_code}: {body}"
                data = response.json()
                all_summaries.extend(data.get("inferenceProfileSummaries", []))
                next_token = data.get("nextToken")
                if not next_token:
                    break

        merged = {"inferenceProfileSummaries": all_summaries}
        models, profile_map = _parse_inference_profile_summaries(merged, region)
        return True, models, profile_map, None
    except Exception as e:
        return False, [], {}, str(e)


async def _list_foundation_models_http(
    api_key: str,
    region: str,
    timeout: float,
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """List models via Bedrock control plane HTTP + Bearer."""
    import httpx

    url = f"{_bedrock_control_plane_url(region)}/foundation-models"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                params={"byOutputModality": "TEXT"},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
        if response.status_code != 200:
            body = (response.text or "")[:500]
            return False, [], f"HTTP {response.status_code}: {body}"
        data = response.json()
        return True, _parse_foundation_model_summaries(data), None
    except Exception as e:
        return False, [], str(e)


async def _probe_converse_http(
    api_key: str,
    region: str,
    model_id: str,
    timeout: float,
) -> Tuple[bool, Optional[str]]:
    """Minimal Converse call"""
    import httpx

    url = f"{_bedrock_runtime_url(region)}/model/{model_id}/converse"
    payload = {
        "messages": [{"role": "user", "content": [{"text": "ping"}]}],
        "inferenceConfig": {"maxTokens": 1},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        if response.status_code == 200:
            return True, None
        body = (response.text or "")[:500]
        return False, f"HTTP {response.status_code}: {body}"
    except Exception as e:
        return False, str(e)


# Models commonly enabled; first successful probe wins.
_BEDROCK_API_KEY_PROBE_MODELS = (
    "us.amazon.nova-micro-v1:0",
    "amazon.nova-lite-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "us.anthropic.claude-3-5-haiku-20241022-v1:0",
)


async def _health_check_bedrock_api_key_http(
    api_key: str,
    region: str,
    timeout: float,
) -> Tuple[bool, Optional[str]]:
    """Verify API key: list models, or minimal Converse if list is denied."""
    ok, _models, error = await _list_foundation_models_http(api_key, region, timeout)
    if ok:
        return True, None

    list_err = error or ""
    if "403" not in list_err and "AccessDenied" not in list_err and "Unauthorized" not in list_err:
        return False, list_err or "Bedrock API key health check failed"

    per_probe = max(timeout / max(len(_BEDROCK_API_KEY_PROBE_MODELS), 1), 3.0)
    errors: List[str] = [f"list_models: {list_err}"]
    for model_id in _BEDROCK_API_KEY_PROBE_MODELS:
        probe_ok, probe_err = await _probe_converse_http(api_key, region, model_id, per_probe)
        if probe_ok:
            logger.info(f"[Bedrock] API key health OK via converse probe ({model_id})")
            return True, None
        if probe_err:
            errors.append(f"{model_id}: {probe_err}")

    return False, "; ".join(errors)


async def _run_boto3_with_bedrock_api_key(api_key: str, region: str, fn):
    """Run sync boto3 callable under Bedrock API key (global env + lock)."""
    import os

    async with _BEDROCK_API_KEY_LOCK:

        def _sync():
            saved: Dict[str, Optional[str]] = {}
            for key in _IAM_ENV_KEYS:
                if key in os.environ:
                    saved[key] = os.environ.pop(key)
            prev_bearer = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key
            try:
                return fn(region)
            finally:
                if prev_bearer is None:
                    os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
                else:
                    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = prev_bearer
                for key, value in saved.items():
                    if value is not None:
                        os.environ[key] = value

        return await asyncio.to_thread(_sync)


def _boto3_iam_clients(
    access_key: str,
    secret_key: str,
    region: str,
    session_token: Optional[str],
):
    import boto3

    creds = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region,
    }
    if session_token:
        creds["aws_session_token"] = session_token
    runtime = boto3.client("bedrock-runtime", **creds)
    control = boto3.client("bedrock", **creds)
    return runtime, control


async def _get_bedrock_clients(
    *,
    access_key: str,
    secret_key: Optional[str],
    region: str,
    session_token: Optional[str],
    bedrock_auth_mode: Optional[str] = None,
):
    """Return (bedrock-runtime client, bedrock control client)."""
    mode = resolve_bedrock_auth_mode(bedrock_auth_mode, secret_key)
    if mode == "api_key":

        def _make(region_name: str):
            import os
            import boto3
            from botocore.tokens import FrozenAuthToken
            from botocore.config import Config

            session = boto3.Session()

            class StaticTokenProvider:
                def __init__(self, token):
                    self.token = token
                def load_token(self):
                    from datetime import datetime, timezone, timedelta
                    from botocore.tokens import DeferredRefreshableToken
                    expiration = datetime.now(timezone.utc) + timedelta(days=365)
                    return DeferredRefreshableToken(
                        method="static",
                        refresh_using=lambda: FrozenAuthToken(
                            token=self.token,
                            expiration=expiration,
                        ),
                    )

            token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or access_key
            session._session.register_component(
                "token_provider",
                StaticTokenProvider(token)
            )

            config = Config(signature_version="bearer")
            runtime = session.client("bedrock-runtime", region_name=region_name, config=config)
            control = session.client("bedrock", region_name=region_name, config=config)
            return runtime, control

        return await _run_boto3_with_bedrock_api_key(access_key, region, _make)

    return _boto3_iam_clients(access_key, secret_key or "", region, session_token)


# ---------------------------------------------------------------------------
# Request / response translation helpers
# ---------------------------------------------------------------------------

def _openai_messages_to_bedrock(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI message format to Bedrock Converse content-blocks.

    Supports text, images, tool_use, tool_result, tool_calls, and tool responses.
    Filters out empty text blocks (Bedrock rejects blank text fields).
    """
    bedrock_messages: List[Dict[str, Any]] = []
    tool_call_names: Dict[str, str] = {}

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        # Bedrock only accepts "user" and "assistant" roles
        if role in ("system", "developer"):
            continue
        if role in ("tool", "function"):
            role = "user"

        bedrock_content: List[Dict[str, Any]] = []

        # ---- string content ----
        if isinstance(content, str) and content.strip():
            bedrock_content.append({"text": content})

        # ---- list content (OpenAI blocks) ----
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type", "")

                if ptype == "text":
                    txt = part.get("text", "")
                    if txt.strip():
                        bedrock_content.append({"text": txt})

                elif ptype == "image_url":
                    image_url = part.get("image_url", {})
                    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                    if url.startswith("data:"):
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

                elif ptype == "tool_use" and role == "assistant":
                    tu_name = part.get("name", "")
                    tu_id = _sanitize_tool_id(part.get("id", ""))
                    if tu_id:
                        tool_call_names[tu_id] = tu_name
                    if tu_name == "local_shell_call":
                        tu_name = "shell"
                    bedrock_content.append({
                        "toolUse": {
                            "toolUseId": tu_id,
                            "name": tu_name,
                            "input": part.get("input", {}) or {},
                        }
                    })

                elif ptype == "tool_result" and role == "user":
                    tr_id = _sanitize_tool_id(part.get("tool_use_id", ""))
                    tr_name = part.get("name") or tool_call_names.get(tr_id, "")
                    if tr_name == "local_shell_call":
                        tr_name = "shell"
                    tr_content = part.get("content", "")
                    if isinstance(tr_content, list):
                        tr_texts = [
                            b.get("text", "")
                            for b in tr_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        tr_content = "\n".join(tr_texts) if tr_texts else ""
                    elif not isinstance(tr_content, str):
                        tr_content = str(tr_content) if tr_content is not None else ""
                    bedrock_content.append({
                        "toolResult": {
                            "toolUseId": tr_id,
                            "content": [{"text": tr_content}],
                        }
                    })

        # ---- tool_calls (OpenAI API format on assistant messages) ----
        tool_calls = msg.get("tool_calls")
        if tool_calls and role == "assistant":
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function", {})
                if not isinstance(func, dict):
                    continue
                name = func.get("name", "")
                arguments = func.get("arguments", "{}")
                call_id = _sanitize_tool_id(tc.get("id", ""))
                if name == "local_shell_call":
                    name = "shell"
                try:
                    args_parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (json.JSONDecodeError, TypeError):
                    args_parsed = {}
                if call_id:
                    tool_call_names[call_id] = name
                bedrock_content.append({
                    "toolUse": {
                        "toolUseId": call_id,
                        "name": name,
                        "input": args_parsed,
                    }
                })

        # ---- tool response (OpenAI API format on tool/function messages) ----
        if msg.get("role") in ("tool", "function"):
            tool_call_id = _sanitize_tool_id(msg.get("tool_call_id", ""))
            name = msg.get("name") or tool_call_names.get(tool_call_id, "")
            if name == "local_shell_call":
                name = "shell"
            content_val = ""
            if isinstance(content, str):
                content_val = content
            elif isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content_val = "\n".join(texts)
            bedrock_content.append({
                "toolResult": {
                    "toolUseId": tool_call_id,
                    "content": [{"text": content_val}],
                }
            })

        # If nothing valid was extracted, add a tiny placeholder so Bedrock doesn't
        # choke on an empty message — but *only* when there are no tool blocks.
        if not bedrock_content:
            bedrock_content.append({"text": "..."})

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
                "id": _sanitize_tool_id(tool_use.get("toolUseId", "call_1")),
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
    secret_key: Optional[str],
    region: str,
    session_token: Optional[str],
    model_name: str,
    username: Optional[str],
    node_headers: Optional[Dict[str, Any]] = None,
    bedrock_auth_mode: Optional[str] = None,
) -> Any:
    """
    Proxy an OpenAI-compatible request to AWS Bedrock Converse API.

    Auth modes:
    - iam: access_key + secret_key (+ optional session_token)
    - api_key: Bedrock API key in access_key + region
    """
    if endpoint not in ("/v1/chat/completions", "/cursor/chat/completions"):
        raise HTTPException(
            status_code=400,
            detail=f"Bedrock nodes only support chat completions endpoint, got: {endpoint}"
        )

    if not bedrock_credentials_configured(
        api_key=access_key,
        secret_key=secret_key,
        region=region,
        bedrock_auth_mode=bedrock_auth_mode,
    ):
        raise HTTPException(status_code=500, detail="Bedrock node missing credentials or region")

    auth_mode = resolve_bedrock_auth_mode(bedrock_auth_mode, secret_key)
    iam_runtime_client = None
    if auth_mode == "iam":
        iam_runtime_client, _ = await _get_bedrock_clients(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            session_token=session_token,
            bedrock_auth_mode=bedrock_auth_mode,
        )

    bedrock_request = _build_bedrock_request(data)

    converse_model_id = resolve_bedrock_converse_model_id(model_name, region)
    if converse_model_id != model_name:
        logger.info(
            f"[Bedrock] Resolved converse modelId {model_name!r} -> {converse_model_id!r}"
        )

    logger.info(
        f"[Bedrock] Sending Converse request to model={converse_model_id}, "
        f"region={region}, stream={stream}"
    )

    def _converse_kwargs(target_model_id: str) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "modelId": target_model_id,
            "messages": bedrock_request.get("messages", []),
        }
        if "inferenceConfig" in bedrock_request:
            kwargs["inferenceConfig"] = bedrock_request["inferenceConfig"]
        if "system" in bedrock_request:
            kwargs["system"] = bedrock_request["system"]
        if "toolConfig" in bedrock_request:
            kwargs["toolConfig"] = bedrock_request["toolConfig"]
        return kwargs

    async def _run_converse(target_model_id: str, streaming: bool) -> Any:
        kwargs = _converse_kwargs(target_model_id)

        # API key auth uses AWS_BEARER_TOKEN_BEDROCK at request time — must run Converse
        # inside _run_boto3_with_bedrock_api_key (client created after env is cleared otherwise).
        if auth_mode == "api_key":

            def _sync_converse(region_name: str) -> Any:
                import os
                import boto3
                from botocore.tokens import FrozenAuthToken
                from botocore.config import Config

                session = boto3.Session()

                class StaticTokenProvider:
                    def __init__(self, token):
                        self.token = token
                    def load_token(self):
                        from datetime import datetime, timezone, timedelta
                        from botocore.tokens import DeferredRefreshableToken
                        expiration = datetime.now(timezone.utc) + timedelta(days=365)
                        return DeferredRefreshableToken(
                            method="static",
                            refresh_using=lambda: FrozenAuthToken(
                                token=self.token,
                                expiration=expiration,
                            ),
                        )

                token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or access_key
                session._session.register_component(
                    "token_provider",
                    StaticTokenProvider(token)
                )

                config = Config(signature_version="bearer")
                runtime = session.client("bedrock-runtime", region_name=region_name, config=config)
                if streaming:
                    return runtime.converse_stream(**kwargs)
                return runtime.converse(**kwargs)

            return await _run_boto3_with_bedrock_api_key(access_key, region, _sync_converse)

        if iam_runtime_client is None:
            raise HTTPException(status_code=500, detail="Bedrock IAM client not initialized")

        if streaming:
            return await asyncio.to_thread(iam_runtime_client.converse_stream, **kwargs)
        return await asyncio.to_thread(iam_runtime_client.converse, **kwargs)

    if stream:
        try:
            response = await _run_converse(converse_model_id, streaming=True)
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
            if (
                not is_bedrock_inference_profile_id(model_name)
                and "inference profile" in str(e).lower()
            ):
                retry_id = bedrock_heuristic_inference_profile_id(model_name, region)
                if retry_id != converse_model_id:
                    logger.info(f"[Bedrock] Retrying stream with inference profile {retry_id!r}")
                    try:
                        response = await _run_converse(retry_id, streaming=True)
                        stream_events = response.get("stream", [])
                        request_id = f"chatcmpl-bedrock-{asyncio.get_event_loop().time():.0f}"

                        async def _stream_generator_retry():
                            for chunk in _bedrock_stream_to_sse(stream_events, model_name, request_id):
                                yield chunk

                        return StreamingResponse(
                            _stream_generator_retry(),
                            media_type="text/event-stream; charset=utf-8",
                            headers={
                                "Cache-Control": "no-cache, no-transform",
                                "X-Accel-Buffering": "no",
                                "Connection": "keep-alive",
                            },
                        )
                    except Exception as retry_e:
                        e = retry_e
            logger.error(f"[Bedrock] Streaming error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Bedrock streaming error: {str(e)}")
    else:
        try:
            response = await _run_converse(converse_model_id, streaming=False)
            openai_response = _bedrock_response_to_openai(
                response,
                model_name,
                request_id=f"chatcmpl-bedrock-{asyncio.get_event_loop().time():.0f}",
            )
            return openai_response
        except Exception as e:
            if (
                not is_bedrock_inference_profile_id(model_name)
                and "inference profile" in str(e).lower()
            ):
                retry_id = bedrock_heuristic_inference_profile_id(model_name, region)
                if retry_id != converse_model_id:
                    logger.info(f"[Bedrock] Retrying converse with inference profile {retry_id!r}")
                    try:
                        response = await _run_converse(retry_id, streaming=False)
                        return _bedrock_response_to_openai(
                            response,
                            model_name,
                            request_id=f"chatcmpl-bedrock-{asyncio.get_event_loop().time():.0f}",
                        )
                    except Exception as retry_e:
                        e = retry_e
            logger.error(f"[Bedrock] Converse error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Bedrock error: {str(e)}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def health_check_bedrock(
    access_key: str,
    secret_key: Optional[str],
    region: str,
    session_token: Optional[str] = None,
    timeout: float = 5.0,
    bedrock_auth_mode: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Health check a Bedrock node by listing foundation models."""
    mode = resolve_bedrock_auth_mode(bedrock_auth_mode, secret_key)
    try:
        if mode == "api_key":
            return await _health_check_bedrock_api_key_http(access_key, region, timeout)

        _, bedrock_client = await _get_bedrock_clients(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            session_token=session_token,
            bedrock_auth_mode=bedrock_auth_mode,
        )
        await asyncio.wait_for(
            asyncio.to_thread(
                bedrock_client.list_foundation_models,
                byOutputModality="TEXT",
            ),
            timeout=timeout,
        )
        return True, None
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"[Bedrock] Health check failed (mode={mode}): {error_msg}")
        return False, error_msg


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------

async def discover_bedrock_models(
    access_key: str,
    secret_key: Optional[str],
    region: str,
    session_token: Optional[str] = None,
    bedrock_auth_mode: Optional[str] = None,
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Discover Bedrock models (inference profile IDs preferred for Converse)."""
    mode = resolve_bedrock_auth_mode(bedrock_auth_mode, secret_key)
    try:
        if mode == "api_key":
            ok, profile_models, _profile_map, profile_err = await _list_inference_profiles_http(
                access_key, region, timeout=30.0
            )
            if ok and profile_models:
                logger.info(
                    f"[Bedrock] Discovered {len(profile_models)} inference profiles (api_key)"
                )
                return True, profile_models, None

            ok, models, error = await _list_foundation_models_http(access_key, region, timeout=30.0)
            if ok:
                return True, models, None
            if error and ("403" in error or "AccessDenied" in error):
                logger.warning(
                    "[Bedrock] API key cannot list models (%s). "
                    "Node can still serve chat if model IDs are known.",
                    error or profile_err,
                )
                return True, [], None
            return False, [], error or profile_err

        _, bedrock_client = await _get_bedrock_clients(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            session_token=session_token,
            bedrock_auth_mode=bedrock_auth_mode,
        )
        try:
            profile_resp = await asyncio.to_thread(
                bedrock_client.list_inference_profiles,
                typeEquals="SYSTEM_DEFINED",
                maxResults=1000,
            )
            profile_models, _profile_map = _parse_inference_profile_summaries(profile_resp, region)
            if profile_models:
                logger.info(
                    f"[Bedrock] Discovered {len(profile_models)} inference profiles (iam)"
                )
                return True, profile_models, None
        except Exception as profile_e:
            logger.warning(f"[Bedrock] list_inference_profiles failed: {profile_e}")

        response = await asyncio.to_thread(
            bedrock_client.list_foundation_models,
            byOutputModality="TEXT",
        )
        return True, _parse_foundation_model_summaries(response), None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Bedrock] Model discovery failed (mode={mode}): {error_msg}")
        return False, [], error_msg
