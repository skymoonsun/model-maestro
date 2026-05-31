"""Cursor AI proxy for Model Maestro

Translates OpenAI-compatible requests to Cursor AI API.
Cursor exposes an OpenAI-compatible API at api2.cursor.sh/v1.

Authentication:
  - Bearer token with crsr_... prefix from cursor.com/dashboard
    Integrations -> API Keys

Endpoints used:
  - GET /v1/models -> model discovery (health check)
  - POST /v1/chat/completions -> chat completions
  - POST /v1/responses -> responses API (optional)
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Default Cursor API base (third-party proxy that supports /v1/models).
# Users may override this via their node config if they have a different proxy.
_CURSOR_API_BASE = "https://cursor-api.standardagents.ai"


def _cursor_base_url(node_base_url: Optional[str] = None) -> str:
    """Return the Cursor API base URL.

    If the node has a configured base_url, use it (e.g. a custom Cursor proxy).
    Otherwise fall back to the third-party default which exposes /v1/models.
    """
    if node_base_url and str(node_base_url).strip():
        return str(node_base_url).rstrip('/')
    return _CURSOR_API_BASE


def cursor_credentials_configured(api_key: Optional[str]) -> bool:
    """Check if Cursor API key is valid and present."""
    if not api_key or not str(api_key).strip():
        return False
    key = str(api_key).strip()
    return key.startswith("crsr_") or len(key) >= 20


async def health_check_cursor(
    api_key: str,
    base_url: str = _CURSOR_API_BASE,
    timeout: float = 10.0,
) -> tuple[bool, Optional[str]]:
    """Check if the Cursor API key is active by calling /v1/models."""
    if not cursor_credentials_configured(api_key):
        return False, "Missing or invalid Cursor API key (expected crsr_... prefix)"

    # Use the node's configured base_url (e.g. https://cursor-api.standardagents.ai/v1).
    # follow_redirects=True handles 308 Permanent Redirect responses gracefully.
    effective_base = _cursor_base_url(base_url)
    url = f"{effective_base.rstrip('/')}/models"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, follow_redirects=False)

            # Manual redirect handling — httpx strips Authorization on
            # cross-origin redirects, so we follow manually with headers intact.
            redirect_count = 0
            while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < 3:
                location = resp.headers.get("Location")
                if not location:
                    break
                resolved = str(httpx.URL(str(resp.url)).join(location))
                logger.info(f"[Cursor] Health check redirect {resp.status_code} -> {resolved}")
                resp = await client.get(resolved, headers=headers, follow_redirects=False)
                redirect_count += 1

            if resp.status_code == 200:
                return True, None
            if resp.status_code == 401:
                return False, "Cursor API key unauthorized (401)"
            if resp.status_code == 429:
                return False, "Cursor rate limit exceeded (429)"
            body = resp.text[:200]
            return False, f"Cursor health check failed: HTTP {resp.status_code} – {body}"
    except httpx.TimeoutException:
        return False, "Cursor health check timed out"
    except httpx.ConnectError as exc:
        return False, f"Cursor connection error: {exc}"
    except Exception as exc:
        logger.error(f"Cursor health check exception: {exc}")
        return False, f"Cursor health check exception: {exc}"


async def discover_cursor_models(
    api_key: str,
    base_url: str = _CURSOR_API_BASE,
    timeout: float = 30.0,
) -> tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Discover available models from Cursor AI.

    Cursor /v1/models returns a standard OpenAI-compatible list:
      {"object": "list", "data": [{"id": "composer-2.5", ...}]}
    """
    if not cursor_credentials_configured(api_key):
        return False, [], "Missing or invalid Cursor API key"

    # Use the node's configured base_url (respect user's Cursor proxy choice).
    effective_base = _cursor_base_url(base_url)
    url = f"{effective_base.rstrip('/')}/models"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, follow_redirects=False)

            # Manual redirect handling — httpx strips Authorization on
            # cross-origin redirects, so we follow manually with headers intact.
            redirect_count = 0
            while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < 3:
                location = resp.headers.get("Location")
                if not location:
                    break
                resolved = str(httpx.URL(str(resp.url)).join(location))
                logger.info(f"[Cursor] Discovery redirect {resp.status_code} -> {resolved}")
                resp = await client.get(resolved, headers=headers, follow_redirects=False)
                redirect_count += 1

            if resp.status_code != 200:
                body = resp.text[:200]
                return False, [], f"Cursor model discovery failed: HTTP {resp.status_code} – {body}"

            data = resp.json()
            models: List[Dict[str, Any]] = []
            for item in data.get("data", []):
                model_id = item.get("id") or item.get("name", "")
                if model_id:
                    models.append({
                        "name": model_id,
                        "size": None,
                        "digest": None,
                        "modified_at": None,
                        "details": {
                            "context_length": item.get("context_window"),
                        },
                        "family": "cursor",
                    })
            return True, models, None

    except httpx.TimeoutException:
        return False, [], "Cursor model discovery timed out"
    except httpx.ConnectError as exc:
        return False, [], f"Cursor connection error: {exc}"
    except Exception as exc:
        logger.error(f"Cursor model discovery exception: {exc}")
        return False, [], f"Cursor model discovery exception: {exc}"


async def proxy_cursor_request(
    data: Dict[str, Any],
    stream: bool,
    endpoint: str,
    base_url: str,
    api_key: str,
    model_name: Optional[str] = None,
    username: Optional[str] = None,
    node_id: Optional[int] = None,
) -> Any:
    """Proxy an OpenAI-compatible request to Cursor AI.

    Cursor speaks native OpenAI format, so we forward almost verbatim.
    The only special handling is auth header and minor request sanitization.
    """
    if not cursor_credentials_configured(api_key):
        raise HTTPException(
            status_code=500,
            detail="Cursor node is missing a valid API key (expected crsr_... prefix)",
        )

    # Use the node's configured base_url, falling back to the default Cursor proxy.
    effective_base = _cursor_base_url(base_url)
    url = f"{effective_base.rstrip('/')}{endpoint}"

    # Sanitize request body for Cursor if necessary.
    # Cursor supports standard OpenAI chat completions, but some fields may differ.
    # We strip fields known to cause issues while keeping everything else.
    request_body = _sanitize_cursor_body(data)

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(1200.0, connect=30.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
    )

    try:
        if stream:
            logger.info(
                f"[Cursor] Streaming request to {url} model={request_body.get('model')}"
            )
            resp = await client.post(url, json=request_body, headers=headers, follow_redirects=False)

            # Manual redirect handling for streaming — httpx strips Authorization
            # on cross-origin redirects. Resolve Location and retry once.
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if location:
                    resolved = str(httpx.URL(url).join(location))
                    logger.info(f"[Cursor] Streaming redirect {resp.status_code} -> {resolved}")
                    await client.aclose()
                    client = httpx.AsyncClient(
                        timeout=httpx.Timeout(1200.0, connect=30.0),
                        limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
                    )
                    resp = await client.post(resolved, json=request_body, headers=headers, follow_redirects=False)

            if resp.status_code >= 400:
                body = await resp.aread()
                text = body.decode("utf-8", errors="replace")[:400]
                logger.warning(f"[Cursor] HTTP {resp.status_code}: {text}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Cursor upstream error: {text}",
                )

            # Build a streaming generator that yields raw SSE chunks.
            async def sse_generator():
                try:
                    async for line in resp.aiter_lines():
                        if line:
                            yield line
                finally:
                    await client.aclose()

            return StreamingResponse(
                sse_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        # Non-streaming.
        logger.info(
            f"[Cursor] Non-streaming request to {url} model={request_body.get('model')}"
        )
        resp = await client.post(url, json=request_body, headers=headers, follow_redirects=False)

        # Manual redirect handling — httpx strips Authorization on cross-origin redirects.
        redirect_count = 0
        while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < 3:
            location = resp.headers.get("Location")
            if not location:
                break
            resolved = str(httpx.URL(url).join(location))
            logger.info(f"[Cursor] Non-streaming redirect {resp.status_code} -> {resolved}")
            resp = await client.post(resolved, json=request_body, headers=headers, follow_redirects=False)
            redirect_count += 1

        body = await resp.aread()

        if resp.status_code >= 400:
            text = body.decode("utf-8", errors="replace")[:400]
            logger.warning(f"[Cursor] HTTP {resp.status_code}: {text}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Cursor upstream error: {text}",
            )

        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body.decode("utf-8", errors="replace")}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Cursor] Proxy error: {exc}")
        raise HTTPException(status_code=502, detail=f"Cursor proxy error: {exc}")
    finally:
        if not stream:
            await client.aclose()


def _sanitize_cursor_body(data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize request body for Cursor AI.

    Cursor supports mostly standard OpenAI fields. As of now, the
    documented models live at api2.cursor.sh/v1 and accept standard
    chat completions / responses shapes.  We only strip fields Cursor
    may choke on or that are Ollama-specific.
    """
    if not isinstance(data, dict):
        return data

    body = dict(data)

    # Remove Ollama-specific parameters that Cursor won't understand.
    ollama_only = {
        "keep_alive",
        "options",
        "format",
        "template",
        "system",
        "context",
        "raw",
        "suffix",
        "images",
    }
    for key in ollama_only:
        body.pop(key, None)

    # Remove unsupported top-level OpenAI fields that Cursor may reject.
    # Verified against Cursor API docs at cursor-api.standardagents.ai.
    unsupported = {
        "modalities",
        "audio",
        "store",
        "metadata",
        "prediction",
        "service_tier",
    }
    for key in unsupported:
        if key in body:
            logger.debug(f"[Cursor] Removing unsupported field {key}")
            body.pop(key, None)

    # Ensure model is a string
    model = body.get("model")
    if model and not isinstance(model, str):
        body["model"] = str(model)

    return body
