r"""
Claude Desktop (Cowork 3P) opaque model ids.

Desktop rejects model ids containing third-party substrings (kimi, qwen, gemma, …)
even when prefixed with ``claude-``. We expose stable opaque ids:

  id: ``claude-route-{sha256(routing_name)[:12]}`` (legacy: ``claude-maestro-…``)
  display_name: picker-safe label (routing still uses the real catalog name)

When Desktop rejects ``display_name``, it does **not** use the API value and instead
shows ``Route {leading digits}`` parsed from the opaque id hash (e.g.
``claude-maestro-27561387dfe8`` → ``Route 27561387``). Synthetic labels like ``g3-``
or ``cd-op`` are rejected more often than raw ``kimi`` / ``qwen`` names.

Routing names are registered in memory (same request) and Redis (cross-request).
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Bump when picker label logic changes; exposed as ``X-Maestro-Desktop-Labels`` for deploy checks.
DESKTOP_PICKER_LABEL_VERSION = "picker-v2"

# Public id prefix — must include ``claude`` for Desktop gateway validation.
MAESTRO_ROUTE_PREFIX = "claude-route-"
_LEGACY_MAESTRO_ROUTE_PREFIX = "claude-maestro-"

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


def normalize_routing_name(internal_name: str) -> str:
    """Canonical routing key used for hashing and proxy dispatch."""
    return (internal_name or "").strip().removeprefix("claude-")


def route_hash(routing_name: str) -> str:
    normalized = normalize_routing_name(routing_name)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def opaque_route_hash_suffix(model_id: str) -> Optional[str]:
    """12-char hex suffix from ``claude-route-…`` or legacy ``claude-maestro-…``."""
    raw = model_id or ""
    for prefix in (MAESTRO_ROUTE_PREFIX, _LEGACY_MAESTRO_ROUTE_PREFIX):
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return None


def is_maestro_desktop_route_id(model_id: str) -> bool:
    suffix = opaque_route_hash_suffix(model_id)
    return bool(suffix and re.fullmatch(r"[a-f0-9]{12}", suffix))


def _remember_route(routing_name: str) -> str:
    core = normalize_routing_name(routing_name)
    digest = route_hash(core)
    _memory_routes[digest] = core
    return f"{MAESTRO_ROUTE_PREFIX}{digest}"


def to_desktop_public_id(internal_name: str) -> str:
    """Build opaque Desktop id and register in-memory mapping for this process."""
    return _remember_route(internal_name)


def peek_routing_name_from_public_id(public_id: str) -> Optional[str]:
    """Sync resolve for same-request logic (e.g. user model ACL filter)."""
    raw = (public_id or "").strip()
    if not is_maestro_desktop_route_id(raw):
        return None
    digest = opaque_route_hash_suffix(raw)
    return _memory_routes.get(digest) if digest else None


def _picker_label_is_rejected(label: str) -> bool:
    """
    Heuristic for labels that fall back to ``Route {hash digits}`` in Desktop.

    Calibrated against production picker vs ``/v1/models`` responses (2026-05).
    """
    if not label:
        return True
    lower = label.lower()
    if " · " in label:
        return True
    if lower.startswith(("g3-", "cd-op", "oai ", "oai-", "oai·")):
        return True
    if any(
        token in lower
        for token in (
            "gemini",
            "google",
            "openai",
            "anthropic",
            "minimax",
            "nomic-embed",
            "prime-coding",
            "moonshotai",
            "deepseek-ai",
            "qwen3-vl",
        )
    ):
        return True
    if lower.startswith(("op-", "opus-")) and not lower.startswith("sn-"):
        return True
    if lower.startswith("claude-"):
        return True
    if lower in ("deepseek-v4", "gt-oss:120b"):
        return True
    if "deepseek-v4-pro" in lower:
        return True
    if lower == "qwen3.5:397b":
        return True
    return False


def _rewrite_picker_label(core: str) -> str:
    """Build a picker label when the raw catalog name would be rejected."""
    label = core.replace(" · ", "-").replace("/", "-")

    label = re.sub(r"^gemini[- ]?", "flash-", label, flags=re.IGNORECASE)
    label = re.sub(r"gemini", "flash", label, flags=re.IGNORECASE)
    label = re.sub(r"^g3-", "flash-", label, flags=re.IGNORECASE)
    label = re.sub(r"^google[-/]?", "", label, flags=re.IGNORECASE)

    label = re.sub(r"^openai[-/]?", "", label, flags=re.IGNORECASE)
    label = re.sub(r"gpt-oss", "oss", label, flags=re.IGNORECASE)
    if label.lower() == "gt-oss:120b":
        label = "oss-120b-medium"

    label = re.sub(r"^moonshotai[- ]?", "", label, flags=re.IGNORECASE)
    label = re.sub(r"^deepseek-ai[- ]?", "deepseek-", label, flags=re.IGNORECASE)
    label = re.sub(r"^minimaxai[- ]?", "", label, flags=re.IGNORECASE)
    label = re.sub(r"^minimax", "mm", label, flags=re.IGNORECASE)
    label = re.sub(r"^z-ai[- ]?", "", label, flags=re.IGNORECASE)

    label = re.sub(r"^claude-opus[- ]?", "sn-", label, flags=re.IGNORECASE)
    label = re.sub(r"^claude-sonnet[- ]?", "sn-", label, flags=re.IGNORECASE)
    label = re.sub(r"^opus[- ]?", "sn-", label, flags=re.IGNORECASE)
    label = re.sub(r"^cd-op[- ]?", "sn-", label, flags=re.IGNORECASE)
    label = re.sub(r"^op-", "sn-", label, flags=re.IGNORECASE)

    if "deepseek-v4-pro" in label.lower():
        label = label.lower().replace("deepseek-v4-pro", "deepseek-pro-v4")
    if label.lower() == "deepseek-v4":
        label = "deepseek-v4-latest"
    if label.lower() == "qwen3.5:397b":
        label = "qwen3.5-397b"
    if "nomic-embed-text" in label.lower():
        label = "nomic-embed"
    if "prime-coding" in label.lower():
        label = f"prime-coding-{route_hash(core)[:4]}"

    label = re.sub(r"-+", "-", label).strip("-")
    if _picker_label_is_rejected(label):
        base = label.split(":")[0] or "model"
        label = f"{base}-{route_hash(core)[:4]}"
    return label


def to_desktop_display_name(routing_name: str) -> str:
    """
    Picker label for Claude Desktop.

    Prefer the real catalog-style name when Desktop accepts it (``kimi``, ``qwen``,
    ``glm``, many ``deepseek`` variants). Only rewrite names that empirically fall
    back to ``Route {digits}`` — do **not** use ``g3-`` / ``cd-op`` / `` · ``.
    """
    core = normalize_routing_name(routing_name)
    if not core:
        return core
    label = core.replace(" · ", "-").replace("/", "-")
    if not _picker_label_is_rejected(label):
        return label
    rewritten = _rewrite_picker_label(core)
    if rewritten != label:
        logger.debug(
            "[Claude][Desktop] display_name '%s' -> '%s' (picker-safe)",
            label,
            rewritten,
        )
    return rewritten


def desktop_display_name_passes_validation(label: str) -> bool:
    """Approximate whether Desktop will show ``display_name`` as-is."""
    return not _picker_label_is_rejected(label)


def desktop_name_passes_client_validation(model_id: str) -> bool:
    """Approximate Desktop gateway validation (for logging / sanity checks)."""
    if not model_id:
        return False
    if is_maestro_desktop_route_id(model_id):
        return True
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
        digest = opaque_route_hash_suffix(raw) or ""
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
        return normalize_routing_name(raw)

    return normalize_routing_name(raw)
