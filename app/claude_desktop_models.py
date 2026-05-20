r"""
Claude Desktop (Cowork 3P) opaque model ids.

Desktop rejects ids containing competitor substrings (kimi, qwen, gemini, …).
We expose:

  id: ``anthropic/claude-{12 lowercase letters}``  (letter-only slug, no digits)
  or: ``anthropic/claude-prime-coding`` when the routing name is already Desktop-safe
  display_name: real catalog name (unchanged)

Legacy ``claude-route-{hex}`` / ``claude-maestro-{hex}`` still resolve via Redis.

Routing names are registered in memory (same request) and Redis (cross-request).
Lookup key for opaque slugs is the 12-letter slug; Redis key
``maestro:claude_desktop_route:{slug}``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Bump when id / picker logic changes; exposed as ``X-Maestro-Desktop-Labels``.
DESKTOP_PICKER_LABEL_VERSION = "picker-v3"

# Primary public id prefix (Anthropic-style).
ANTHROPIC_CLAUDE_PREFIX = "anthropic/claude-"

# Legacy opaque prefixes (resolve only).
_LEGACY_ROUTE_PREFIX = "claude-route-"
_LEGACY_MAESTRO_ROUTE_PREFIX = "claude-maestro-"
_LEGACY_GW_PREFIX = "claude-gw-"

_ALPHABETIC_SLUG_LEN = 12
_ALPHABETIC_SLUG_RE = re.compile(r"^[a-z]{12}$")
_LEGACY_HEX_SUFFIX_RE = re.compile(r"^[a-f0-9]{12}$")

_SHORT_ALIAS_RE = re.compile(r"^(sonnet|opus|haiku)(-[\d.]+)?$", re.IGNORECASE)
_KEYWORDS = frozenset({"claude", "sonnet", "opus", "haiku", "anthropic"})

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

# lookup_key (12-letter slug or legacy hex12) -> routing name
_memory_routes: Dict[str, str] = {}
# full public id -> routing name (same-request peek / readable ids)
_public_id_to_core: Dict[str, str] = {}


def normalize_routing_name(internal_name: str) -> str:
    """Canonical routing key used for hashing and proxy dispatch."""
    return (internal_name or "").strip().removeprefix("claude-")


def route_alphabetic_slug(routing_name: str, length: int = _ALPHABETIC_SLUG_LEN) -> str:
    """
    Deterministic lowercase a–z slug from routing name (no digits).

    SHA-256 digest bytes are mapped to ``a``–``z`` so Desktop does not parse
    leading digits from hex ids (``Route 3``, ``Maestro 27561387``, …).
    """
    normalized = normalize_routing_name(routing_name)
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    return "".join(chr(ord("a") + (digest[i] % 26)) for i in range(length))


def route_hash(routing_name: str) -> str:
    """Legacy 12-char hex digest (old ``claude-route-`` / ``claude-maestro-`` ids)."""
    normalized = normalize_routing_name(routing_name)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _id_contains_blocked_substrings(model_id: str) -> bool:
    lower = (model_id or "").lower()
    return any(block in lower for block in DESKTOP_BLOCKED_SUBSTRINGS)


def _slug_for_readable_id(core: str) -> str:
    return core.replace(" · ", "-").replace("/", "-").replace(":", "-")


def _readable_anthropic_id(core: str) -> str:
    return f"{ANTHROPIC_CLAUDE_PREFIX}{_slug_for_readable_id(core)}"


def opaque_route_hash_suffix(model_id: str) -> Optional[str]:
    """Legacy 12-char hex suffix from ``claude-route-…`` / ``claude-maestro-…``."""
    raw = model_id or ""
    for prefix in (_LEGACY_ROUTE_PREFIX, _LEGACY_MAESTRO_ROUTE_PREFIX):
        if raw.startswith(prefix):
            suffix = raw[len(prefix) :]
            if _LEGACY_HEX_SUFFIX_RE.fullmatch(suffix):
                return suffix
    return None


def anthropic_claude_tail(model_id: str) -> Optional[str]:
    """Part after ``anthropic/claude-``."""
    raw = (model_id or "").strip()
    if raw.startswith(ANTHROPIC_CLAUDE_PREFIX):
        return raw[len(ANTHROPIC_CLAUDE_PREFIX) :]
    return None


def is_maestro_desktop_opaque_id(model_id: str) -> bool:
    """
    True when the id needs Desktop header + Redis/memory lookup.

    Readable ``anthropic/claude-prime-coding`` is **not** opaque.
    """
    raw = (model_id or "").strip()
    if opaque_route_hash_suffix(raw):
        return True
    tail = anthropic_claude_tail(raw)
    if tail and _ALPHABETIC_SLUG_RE.fullmatch(tail):
        return True
    return False


# Back-compat alias used across the codebase
is_maestro_desktop_route_id = is_maestro_desktop_opaque_id


def _remember_route(core: str, public_id: str, lookup_key: str) -> str:
    _memory_routes[lookup_key] = core
    _public_id_to_core[public_id] = core
    return public_id


def to_desktop_public_id(internal_name: str) -> str:
    """Build Desktop public id and register mappings for this process."""
    core = normalize_routing_name(internal_name)
    readable = _readable_anthropic_id(core)
    if not _id_contains_blocked_substrings(readable):
        lookup_key = route_alphabetic_slug(core)
        return _remember_route(core, readable, lookup_key)

    slug = route_alphabetic_slug(core)
    public_id = f"{ANTHROPIC_CLAUDE_PREFIX}{slug}"
    return _remember_route(core, public_id, slug)


def peek_routing_name_from_public_id(public_id: str) -> Optional[str]:
    """Sync resolve for same-request logic (e.g. user model ACL filter)."""
    raw = (public_id or "").strip()
    if raw in _public_id_to_core:
        return _public_id_to_core[raw]
    tail = anthropic_claude_tail(raw)
    if tail and _ALPHABETIC_SLUG_RE.fullmatch(tail):
        return _memory_routes.get(tail)
    legacy = opaque_route_hash_suffix(raw)
    if legacy:
        return _memory_routes.get(legacy)
    return None


def to_desktop_display_name(routing_name: str) -> str:
    """Picker label — real catalog name (Desktop uses this when id is acceptable)."""
    core = normalize_routing_name(routing_name)
    if not core:
        return core
    return core.replace(" · ", "-").replace("/", "-")


def desktop_display_name_passes_validation(label: str) -> bool:
    """Always pass through catalog names in picker-v3."""
    return bool(label)


def desktop_name_passes_client_validation(model_id: str) -> bool:
    """Approximate Desktop gateway validation (for logging / sanity checks)."""
    if not model_id:
        return False
    if is_maestro_desktop_opaque_id(model_id):
        return True
    lower = model_id.lower()
    if _id_contains_blocked_substrings(lower):
        return False
    tail = lower.removeprefix("anthropic/").removeprefix("claude-")
    if _SHORT_ALIAS_RE.match(tail):
        return True
    return any(kw in lower for kw in _KEYWORDS)


async def _redis_lookup(lookup_key: str) -> Optional[str]:
    try:
        from app.redis import redis_manager

        if redis_manager:
            cached = await redis_manager.get(_REDIS_ROUTE_KEY.format(hash=lookup_key))
            if isinstance(cached, str) and cached:
                return cached
    except Exception as e:
        logger.warning(f"[Claude][Desktop] Redis route lookup failed for {lookup_key}: {e}")
    return None


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
    for lookup_key, routing_name in _memory_routes.items():
        key = _REDIS_ROUTE_KEY.format(hash=lookup_key)
        await redis_manager.set(key, routing_name, expire=_REDIS_ROUTE_TTL_SEC)


async def resolve_desktop_public_id(public_id: str) -> str:
    """Map Desktop public id back to Maestro routing name."""
    raw = (public_id or "").strip()
    if not raw:
        return raw

    if raw in _public_id_to_core:
        return _public_id_to_core[raw]

    tail = anthropic_claude_tail(raw)
    if tail:
        if _ALPHABETIC_SLUG_RE.fullmatch(tail):
            if tail in _memory_routes:
                return _memory_routes[tail]
            cached = await _redis_lookup(tail)
            if cached:
                _memory_routes[tail] = cached
                _public_id_to_core[raw] = cached
                return cached
            logger.warning(
                f"[Claude][Desktop] Unknown alphabetic id '{raw}' — re-run model discovery"
            )
            return raw
        # Readable anthropic/claude-prime-coding → prime-coding
        return normalize_routing_name(tail)

    legacy_hex = opaque_route_hash_suffix(raw)
    if legacy_hex:
        if legacy_hex in _memory_routes:
            return _memory_routes[legacy_hex]
        cached = await _redis_lookup(legacy_hex)
        if cached:
            _memory_routes[legacy_hex] = cached
            return cached
        logger.warning(
            f"[Claude][Desktop] Unknown legacy opaque id '{raw}' — re-run model discovery"
        )
        return raw

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
        return normalize_routing_name(raw)

    return normalize_routing_name(raw)
