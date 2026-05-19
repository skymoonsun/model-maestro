"""Antigravity account quota via Google v1internal:fetchAvailableModels.

Mirrors Antigravity Manager (lbjlaq/Antigravity-Manager) quota module:
- loadCodeAssist for project_id and subscription tier
- fetchAvailableModels with Sandbox → Daily → Prod fallback
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.google_auth import build_v1internal_headers, ensure_fresh_token, get_user_agent

logger = logging.getLogger(__name__)

QUOTA_FETCH_URLS = [
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:fetchAvailableModels",
    "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
    "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
]

LOAD_CODE_ASSIST_URL = (
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:loadCodeAssist"
)

_MODEL_PREFIXES = ("gemini", "claude", "gpt", "image", "imagen")
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_quota_model(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _MODEL_PREFIXES)


def _empty_quota(
    *,
    is_forbidden: bool = False,
    forbidden_reason: Optional[str] = None,
    subscription_tier: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "models": [],
        "last_updated": int(time.time()),
        "is_forbidden": is_forbidden,
        "forbidden_reason": forbidden_reason,
        "subscription_tier": subscription_tier,
        "model_forwarding_rules": {},
    }


def _parse_quota_response(
    resp_json: Dict[str, Any],
    subscription_tier: Optional[str],
) -> Dict[str, Any]:
    data = resp_json.get("response", resp_json)
    models_raw = data.get("models") or {}
    models: List[Dict[str, Any]] = []

    if isinstance(models_raw, dict):
        for name, info in models_raw.items():
            if not isinstance(info, dict) or not _is_quota_model(name):
                continue
            quota_info = info.get("quotaInfo") or {}
            remaining = quota_info.get("remainingFraction")
            percentage = int(remaining * 100) if remaining is not None else 0
            models.append(
                {
                    "name": name,
                    "percentage": max(0, min(100, percentage)),
                    "reset_time": quota_info.get("resetTime") or "",
                    "display_name": info.get("displayName"),
                    "supports_images": info.get("supportsImages"),
                    "supports_thinking": info.get("supportsThinking"),
                    "recommended": info.get("recommended"),
                }
            )

    models.sort(key=lambda item: item["name"])

    forwarding: Dict[str, str] = {}
    deprecated = data.get("deprecatedModelIds") or {}
    if isinstance(deprecated, dict):
        for old_id, dep_info in deprecated.items():
            if isinstance(dep_info, dict) and dep_info.get("newModelId"):
                forwarding[str(old_id)] = str(dep_info["newModelId"])

    return {
        "models": models,
        "last_updated": int(time.time()),
        "is_forbidden": False,
        "forbidden_reason": None,
        "subscription_tier": subscription_tier,
        "model_forwarding_rules": forwarding,
    }


async def load_code_assist_info(access_token: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (project_id, subscription_tier) from loadCodeAssist."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": get_user_agent(),
    }
    body = {"metadata": {"ideType": "ANTIGRAVITY"}}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(LOAD_CODE_ASSIST_URL, headers=headers, json=body)
    except httpx.RequestError as exc:
        logger.warning("[Quota] loadCodeAssist network error: %s", exc)
        return None, None

    if response.status_code != 200:
        logger.warning(
            "[Quota] loadCodeAssist failed: %s %s",
            response.status_code,
            response.text[:200],
        )
        return None, None

    data = response.json()
    project_id = data.get("cloudaicompanionProject")

    paid_tier = data.get("paidTier") or {}
    current_tier = data.get("currentTier") or {}
    ineligible = data.get("ineligibleTiers") or []

    subscription_tier = paid_tier.get("name") or paid_tier.get("id")
    if not subscription_tier and not ineligible:
        subscription_tier = current_tier.get("name") or current_tier.get("id")
    if not subscription_tier and ineligible:
        allowed = data.get("allowedTiers") or []
        for tier in allowed:
            if tier.get("isDefault"):
                name = tier.get("name") or tier.get("id")
                if name:
                    subscription_tier = f"{name} (Restricted)"
                break

    return (
        str(project_id) if project_id else None,
        str(subscription_tier) if subscription_tier else None,
    )


async def fetch_antigravity_quota(
    oauth_tokens: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Fetch quota for an Antigravity Google account.

    Returns:
        (quota_dict, project_id) — oauth_tokens may be updated in-place after refresh.
    """
    access_token = await ensure_fresh_token(oauth_tokens)

    resolved_project_id = project_id
    subscription_tier: Optional[str] = None

    load_pid, load_tier = await load_code_assist_info(access_token)
    if load_pid and not resolved_project_id:
        resolved_project_id = load_pid
    if load_tier:
        subscription_tier = load_tier

    payload: Dict[str, Any] = (
        {"project": resolved_project_id} if resolved_project_id else {}
    )
    headers = build_v1internal_headers(access_token)

    last_error: Optional[str] = None

    for ep_idx, url in enumerate(QUOTA_FETCH_URLS):
        has_next = ep_idx + 1 < len(QUOTA_FETCH_URLS)
        current_payload = dict(payload)
        retry_without_project = False

        while True:
            try:
                async with httpx.AsyncClient(timeout=20.0, http2=True) as client:
                    response = await client.post(
                        url, headers=headers, json=current_payload
                    )
            except httpx.RequestError as exc:
                last_error = str(exc)
                logger.warning("[Quota] request failed at %s: %s", url, exc)
                break

            if response.status_code == 403:
                if current_payload.get("project") and not retry_without_project:
                    logger.warning(
                        "[Quota] 403 with project id; retrying without project"
                    )
                    current_payload = {}
                    retry_without_project = True
                    continue
                return (
                    _empty_quota(
                        is_forbidden=True,
                        forbidden_reason=response.text[:500] or "403 Forbidden",
                        subscription_tier=subscription_tier,
                    ),
                    resolved_project_id,
                )

            if response.status_code != 200:
                if has_next and response.status_code in _RETRYABLE_STATUS:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning("[Quota] %s at %s, trying next endpoint", last_error, url)
                    break
                raise RuntimeError(
                    f"Quota fetch failed: HTTP {response.status_code} - {response.text[:300]}"
                )

            try:
                resp_json = response.json()
            except ValueError as exc:
                raise RuntimeError("Quota fetch returned invalid JSON") from exc

            if ep_idx > 0:
                logger.info("[Quota] fallback succeeded at endpoint #%s", ep_idx + 1)

            return (
                _parse_quota_response(resp_json, subscription_tier),
                resolved_project_id,
            )

    raise RuntimeError(last_error or "Quota fetch failed: all endpoints exhausted")
