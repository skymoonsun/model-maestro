r"""
Claude Desktop (Cowork 3P) opaque model ids.

Desktop rejects model ids containing third-party substrings (kimi, qwen, gemma, …)
even when prefixed with ``claude-``. We expose stable opaque ids:

  id: ``claude-maestro-{sha256(routing_name)[:12]}``
  display_name: real catalog name (unchanged)

Routing names are registered in memory (same request) and Redis (cross-request).
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Public id prefix — must include ``claude`` for Desktop gateway validation.
MAESTRO_ROUTE_PREFIX = "claude-maestro-"

# Legacy obfuscation / gw ids (resolve only, no longer emitted).
_LEGACY_GW_PREFIX = "claude-gw-"

_SHORT_ALIAS_RE = re.compile(r"^(sonnet|opus|haiku)(-[\d.]+)?$", re.IGNORECASE)
_KEYWORDS = frozenset({"claude", "sonnet", "opus", "haiku", "anthropic"})

# Competitor substrings Desktop rejects inside the full model id string.
DESKTOP_BLOCKED_SUBSTRINGS = (
    "kimi",
    "qwen",
    "gemma",
    "deepseek",
    "gemini",
    "openai",
    "gpt-",
    "gpt4",
    "gpt3",
    "llama",
    "mistral",
    "mimo",
    "grok",
    "cohere",
    "command-r",
    "meta-llama",
    "google",
    "codegemma",
)

_REDIS_ROUTE_KEY = "maestro:claude_desktop_route:{hash}"
_REDIS_ROUTE_TTL_SEC = 60 * 60 * 24 * 30  # 30 days

# hash12 -> Maestro routing name (no claude- prefix)
_memory_routes: Dict[str, str] = {}

# Client-visible alias (display name, legacy claude- id) -> canonical routing name
_alias_to_routing: Dict[str, str] = {}


def normalize_routing_name(internal_name: str) -> str:
    """Canonical routing key used for hashing and proxy dispatch."""
    return (internal_name or "").strip().removeprefix("claude-")


def route_hash(routing_name: str) -> str:
    normalized = normalize_routing_name(routing_name)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def is_maestro_desktop_route_id(model_id: str) -> bool:
    return (model_id or "").startswith(MAESTRO_ROUTE_PREFIX)


def _remember_route(routing_name: str) -> str:
    core = normalize_routing_name(routing_name)
    digest = route_hash(core)
    _memory_routes[digest] = core
    return f"{MAESTRO_ROUTE_PREFIX}{digest}"


def register_desktop_route_alias(alias: str, canonical_routing_name: str) -> None:
    """Map picker label / legacy id to the node catalog name used for proxy routing."""
    alias_key = (alias or "").strip()
    canonical = normalize_routing_name(canonical_routing_name)
    if not alias_key or not canonical or alias_key == canonical:
        return
    _alias_to_routing[alias_key] = canonical
    if alias_key.startswith("claude-"):
        _alias_to_routing[normalize_routing_name(alias_key)] = canonical


def to_desktop_public_id(internal_name: str) -> str:
    """Build opaque Desktop id and register in-memory mapping for this process."""
    return _remember_route(internal_name)


def peek_routing_name_from_public_id(public_id: str) -> Optional[str]:
    """Sync resolve for same-request logic (e.g. user model ACL filter)."""
    raw = (public_id or "").strip()
    if not is_maestro_desktop_route_id(raw):
        return None
    digest = raw[len(MAESTRO_ROUTE_PREFIX) :]
    return _memory_routes.get(digest)


def desktop_name_passes_client_validation(model_id: str) -> bool:
    """Approximate Desktop gateway validation (for logging / sanity checks)."""
    if not model_id:
        return False
    if is_maestro_desktop_route_id(model_id):
        suffix = model_id[len(MAESTRO_ROUTE_PREFIX) :]
        return bool(re.fullmatch(r"[a-f0-9]{12}", suffix))
    lower = model_id.lower()
    if any(block in lower for block in DESKTOP_BLOCKED_SUBSTRINGS):
        return False
    tail = lower.removeprefix("anthropic/").removeprefix("claude-")
    if _SHORT_ALIAS_RE.match(tail):
        return True
    return any(kw in lower for kw in _KEYWORDS)


async def persist_desktop_routes_to_redis() -> None:
    """Flush in-memory route table after GET /v1/models (Desktop clients)."""
    if not _memory_routes:
        return
    try:
        from app.redis import redis_manager
    except Exception:
        return
    if not redis_manager:
        return
    for digest, routing_name in _memory_routes.items():
        key = _REDIS_ROUTE_KEY.format(hash=digest)
        await redis_manager.set(key, routing_name, expire=_REDIS_ROUTE_TTL_SEC)


async def resolve_desktop_public_id(public_id: str) -> str:
    """Map Desktop opaque id back to Maestro routing name."""
    raw = (public_id or "").strip()
    if not raw:
        return raw

    if is_maestro_desktop_route_id(raw):
        digest = raw[len(MAESTRO_ROUTE_PREFIX) :]
        if digest in _memory_routes:
            return _memory_routes[digest]
        try:
            from app.redis import redis_manager
            if redis_manager:
                cached = await redis_manager.get(_REDIS_ROUTE_KEY.format(hash=digest))
                if isinstance(cached, str) and cached:
                    _memory_routes[digest] = cached
                    return cached
        except Exception as e:
            logger.warning(f"[Claude][Desktop] Redis route lookup failed for {digest}: {e}")
        logger.warning(
            f"[Claude][Desktop] Unknown opaque route id '{raw}' — "
            "re-run model discovery or check Redis"
        )
        return raw

    # Legacy gw base64 routes (older builds)
    if raw.startswith(_LEGACY_GW_PREFIX) or raw.startswith("anthropic/"):
        import base64
        pid = raw.removeprefix("anthropic/")
        if pid.startswith(_LEGACY_GW_PREFIX):
            token = pid[len(_LEGACY_GW_PREFIX) :]
            pad = "=" * (-len(token) % 4)
            try:
                return base64.urlsafe_b64decode((token + pad).encode("ascii")).decode("utf-8")
            except Exception:
                pass

    if raw.startswith("claude-"):
        stripped = normalize_routing_name(raw)
        if stripped in _alias_to_routing:
            return _alias_to_routing[stripped]
        return stripped

    normalized = normalize_routing_name(raw)
    if normalized in _alias_to_routing:
        return _alias_to_routing[normalized]
    if raw in _alias_to_routing:
        return _alias_to_routing[raw]
    return normalized
