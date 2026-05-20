"""Tests for Claude Desktop opaque model ids."""

import asyncio

import pytest
from fastapi import HTTPException

from app.claude import _resolve_claude_request_model
from app.claude_desktop_models import (
    _memory_routes,
    desktop_display_name_passes_validation,
    desktop_name_passes_client_validation,
    is_maestro_desktop_route_id,
    normalize_routing_name,
    peek_routing_name_from_public_id,
    resolve_desktop_public_id,
    route_hash,
    to_desktop_display_name,
    to_desktop_public_id,
)


@pytest.fixture(autouse=True)
def clear_memory_routes() -> None:
    _memory_routes.clear()
    yield
    _memory_routes.clear()


def test_opaque_id_avoids_blocked_substrings() -> None:
    internal = "google/codegemma-7b"
    public = to_desktop_public_id(internal)
    assert public.startswith("claude-route-")
    assert "gemma" not in public
    assert "google" not in public
    assert desktop_name_passes_client_validation(public)


def test_hash_stable_and_routing_roundtrip() -> None:
    internal = "kimi-k2.6:latest"
    h = route_hash(internal)
    assert h == route_hash("claude-kimi-k2.6:latest")
    public = to_desktop_public_id(internal)
    assert peek_routing_name_from_public_id(public) == normalize_routing_name(internal)


def test_resolve_from_memory() -> None:
    internal = "qwen-2.5-coder"
    public = to_desktop_public_id(internal)
    resolved = asyncio.run(resolve_desktop_public_id(public))
    assert resolved == normalize_routing_name(internal)


def test_opaque_id_not_found_without_desktop_header() -> None:
    public = to_desktop_public_id("google/codegemma-7b")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_resolve_claude_request_model(public, desktop=False))
    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail.lower()


def test_desktop_display_name_sanitizes_brand_tokens() -> None:
    assert to_desktop_display_name("gemini-3.5-flash-low") == "g3-3.5-flash-low"
    assert to_desktop_display_name("claude-opus-4-6-thinking") == "cd-op-4-6-thinking"
    assert desktop_display_name_passes_validation(to_desktop_display_name("gemini-3-flash"))


def test_desktop_display_name_normalizes_slashes() -> None:
    assert to_desktop_display_name("z-ai/glm-5.1") == "z-ai · glm-5.1"
    assert desktop_display_name_passes_validation("z-ai · glm-5.1")


def test_desktop_display_name_leaves_unblocked_names() -> None:
    assert to_desktop_display_name("kimi-k2.6:latest") == "kimi-k2.6:latest"
    assert to_desktop_display_name("qwen3.5:latest") == "qwen3.5:latest"


def test_legacy_maestro_prefix_still_resolves() -> None:
    internal = "gemini-3.5-flash-low"
    public = to_desktop_public_id(internal)
    legacy = public.replace("claude-route-", "claude-maestro-", 1)
    resolved = asyncio.run(resolve_desktop_public_id(legacy))
    assert resolved == normalize_routing_name(internal)


def test_opaque_id_resolves_with_desktop_header() -> None:
    internal = "google/codegemma-7b"
    public = to_desktop_public_id(internal)
    resolved = asyncio.run(_resolve_claude_request_model(public, desktop=True))
    assert resolved == normalize_routing_name(internal)
    assert not is_maestro_desktop_route_id(resolved)
