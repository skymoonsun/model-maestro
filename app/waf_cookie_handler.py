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

        # Always add a realistic User-Agent — some WAFs reject requests without one
        if "user-agent" not in {k.lower() for k in request_headers.keys()}:
            request_headers["User-Agent"] = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        # Additional browser-like headers that many WAFs require
        if "accept" not in {k.lower() for k in request_headers.keys()}:
            request_headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            )
        if "accept-language" not in {k.lower() for k in request_headers.keys()}:
            request_headers["Accept-Language"] = "en-US,en;q=0.9"
        if "accept-encoding" not in {k.lower() for k in request_headers.keys()}:
            request_headers["Accept-Encoding"] = "gzip, deflate, br"
        if "connection" not in {k.lower() for k in request_headers.keys()}:
            request_headers["Connection"] = "keep-alive"
        if "upgrade-insecure-requests" not in {k.lower() for k in request_headers.keys()}:
            request_headers["Upgrade-Insecure-Requests"] = "1"

        # Determine probe URLs
        root_url = f"{base_url.rstrip('/')}/"
        if health_check_url:
            api_url = health_check_url
        elif node_type == "vllm":
            api_url = f"{base_url.rstrip('/')}/v1/models"
        else:
            api_url = f"{base_url.rstrip('/')}/api/tags"

        # If there is an existing stale Cookie, the WAF may reject outright
        # without issuing a new challenge. Try without Cookie first.
        headers_without_cookie = {k: v for k, v in request_headers.items() if k.lower() != "cookie"}

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=40,
                keepalive_expiry=120,
            ),
            follow_redirects=False,  # WAF often returns 302; we want to inspect headers
        ) as client:
            probes = [
                # (method, url, headers_variant, description)
                ("GET", root_url, headers_without_cookie, "root (no cookie)"),
                ("GET", root_url, request_headers, "root (with headers)"),
                ("HEAD", root_url, headers_without_cookie, "root HEAD (no cookie)"),
                ("GET", api_url, headers_without_cookie, "api (no cookie)"),
                ("GET", api_url, request_headers, "api (with headers)"),
                ("POST", root_url, headers_without_cookie, "root POST (no cookie)"),
            ]

            last_status = None
            for method, target_url, hdrs, desc in probes:
                if target_url == api_url and api_url == root_url:
                    continue
                logger.info(f"[WAF] Probing [{method}] {target_url} ({desc}) ...")
                try:
                    response = await client.request(method, target_url, headers=hdrs, timeout=timeout)
                except Exception as probe_err:
                    logger.warning(f"[WAF] Probe [{method}] {target_url} failed: {probe_err}")
                    continue

                last_status = response.status_code
                logger.info(f"[WAF] Probe [{method}] status: {last_status}")

                if response.status_code not in WAF_RETRYABLE_STATUS_CODES:
                    continue

                set_cookie = response.headers.get("set-cookie")
                if not set_cookie:
                    logger.warning(f"[WAF] Challenge status {last_status} but no Set-Cookie header on [{method}] {target_url}")
                    continue

                cookie = parse_set_cookie(set_cookie)
                if not cookie:
                    logger.warning(f"[WAF] Could not parse Set-Cookie: {set_cookie}")
                    continue

                updated_headers = merge_cookie_into_headers(existing_headers, cookie)
                logger.info(f"[WAF] Captured new cookie for {base_url} from [{method}] {target_url}")
                return True, updated_headers, None

            return False, None, f"No WAF challenge cookie found after {len(probes)} attempts (last status: {last_status})"

    except httpx.TimeoutException:
        return False, None, "WAF cookie refresh timed out"
    except httpx.ConnectError as e:
        return False, None, f"WAF cookie refresh connection error: {e}"
    except Exception as e:
        logger.error(f"[WAF] Unexpected error during cookie refresh: {e}")
        return False, None, str(e)
