"""WAF Cookie Auto-Refresh Helper

Handles Aliyun (and similar) WAF challenge cookies by:
1. Detecting WAF challenge responses (302, 401, 403, 405, 407).
2. Extracting Set-Cookie headers.
3. Merging the captured cookie into node headers.

This module is intentionally DB-agnostic — callers persist updated headers.
"""

import logging
from typing import Dict, Any, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)

WAF_RETRYABLE_STATUS_CODES = frozenset({302, 401, 403, 405, 407})


def parse_set_cookie(set_cookie_header: str) -> Optional[str]:
    """
    Extract the raw cookie string from a Set-Cookie header value.

    Aliyun WAF typically returns something like:
        acw_tc=0a0f...; Path=/; HttpOnly
    We keep only the name=value part(s) and join with '; ' for the Cookie header.
    """
    if not set_cookie_header:
        return None
    cookies = []
    for part in set_cookie_header.split(","):
        # Each part may contain attrs separated by ;
        cookie_kv = part.strip().split(";")[0].strip()
        if "=" in cookie_kv:
            cookies.append(cookie_kv)
    return "; ".join(cookies) if cookies else None


def merge_cookie_into_headers(
    headers: Optional[Dict[str, str]], cookie: str
) -> Dict[str, str]:
    """Merge a cookie string into existing headers (overwrites existing Cookie key)."""
    merged = dict(headers) if headers else {}
    merged["Cookie"] = cookie
    return merged


async def refresh_waf_cookie(
    base_url: str,
    api_key: Optional[str] = None,
    node_type: str = "ollama",
    existing_headers: Optional[Dict[str, str]] = None,
    health_check_url: Optional[str] = None,
    timeout: float = 5.0,
) -> Tuple[bool, Optional[Dict[str, str]], Optional[str]]:
    """
    Attempt a lightweight GET to the node to trigger a WAF challenge and capture Set-Cookie.

    Returns:
        (success, updated_headers_or_None, error_message)
        success=True means a new cookie was captured from the WAF challenge response.
    """
    try:
        # Build request headers (same logic as health_check_node)
        request_headers: Dict[str, str] = {}
        if existing_headers:
            request_headers.update(existing_headers)
        if api_key and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {api_key}"

        # Determine health-check URL
        if health_check_url:
            target_url = health_check_url
        elif node_type == "vllm":
            target_url = f"{base_url.rstrip('/')}/v1/models"
        else:
            target_url = f"{base_url.rstrip('/')}/api/tags"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=40,
                keepalive_expiry=120,
            ),
            follow_redirects=False,  # WAF often returns 302; we want to inspect headers
        ) as client:
            logger.info(f"[WAF] Probing {target_url} for challenge cookie ...")
            response = await client.get(target_url, headers=request_headers, timeout=timeout)

            logger.info(f"[WAF] Probe status: {response.status_code}")

            if response.status_code not in WAF_RETRYABLE_STATUS_CODES:
                # Not a challenge response — either healthy or some other error
                return False, None, f"Unexpected status {response.status_code} (not a WAF challenge)"

            set_cookie = response.headers.get("set-cookie")
            if not set_cookie:
                logger.warning(f"[WAF] Challenge status {response.status_code} but no Set-Cookie header")
                return False, None, f"Challenge status {response.status_code} without Set-Cookie"

            cookie = parse_set_cookie(set_cookie)
            if not cookie:
                logger.warning(f"[WAF] Could not parse Set-Cookie: {set_cookie}")
                return False, None, "Failed to parse Set-Cookie header"

            updated_headers = merge_cookie_into_headers(existing_headers, cookie)
            logger.info(f"[WAF] Captured new cookie for {base_url}")
            return True, updated_headers, None

    except httpx.TimeoutException:
        return False, None, "WAF cookie refresh timed out"
    except httpx.ConnectError as e:
        return False, None, f"WAF cookie refresh connection error: {e}"
    except Exception as e:
        logger.error(f"[WAF] Unexpected error during cookie refresh: {e}")
        return False, None, str(e)
