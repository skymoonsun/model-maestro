"""Tests for Claude Desktop opaque model ids."""

import asyncio
import re

import pytest
from fastapi import HTTPException

from app.claude import _resolve_claude_request_model
from app.claude_desktop_models import (
    ANTHROPIC_CLAUDE_PREFIX,
    _memory_routes,
    _public_id_to_core,
    desktop_name_passes_client_validation,
    is_maestro_desktop_opaque_id,
    normalize_routing_name,
    peek_routing_name_from_public_id,
    resolve_desktop_public_id,
    route_alphabetic_slug,
    route_hash,
    to_desktop_display_name,
    to_desktop_public_id,
)


@pytest.fixture(autouse=True)
def clear_memory_routes() -> None:
    _memory_routes.clear()
    _public_id_to_core.clear()
    yield
    _memory_routes.clear()
    _public_id_to_core.clear()


def test_opaque_id_uses_anthropic_prefix_and_letters_only() -> None:
    internal = "google/codegemma-7b"
    public = to_desktop_public_id(internal)
    assert public.startswith(ANTHROPIC_CLAUDE_PREFIX)
    tail = public[len(ANTHROPIC_CLAUDE_PREFIX) :]
    assert re.fullmatch(r"[a-z]{12}", tail)
    assert not re.search(r"\d", tail)
    assert "gemma" not in public
    assert "google" not in public
    assert desktop_name_passes_client_validation(public)


def test_alphabetic_slug_stable() -> None:
    internal = "kimi-k2.6:latest"
    assert route_alphabetic_slug(internal) == route_alphabetic_slug("claude-kimi-k2.6:latest")
    assert route_hash(internal) == route_hash("claude-kimi-k2.6:latest")


def test_hash_stable_and_routing_roundtrip() -> None:
    internal = "kimi-k2.6:latest"
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


def test_desktop_display_name_is_catalog_name() -> None:
    assert to_desktop_display_name("kimi-k2.6:latest") == "kimi-k2.6:latest"
    assert to_desktop_display_name("prime-coding") == "prime-coding"
    assert to_desktop_display_name("gemini-3.5-flash-low") == "gemini-3.5-flash-low"


def test_readable_anthropic_id_for_prime_coding() -> None:
    public = to_desktop_public_id("prime-coding")
    assert public == "anthropic/claude-prime-coding"
    assert not is_maestro_desktop_opaque_id(public)
    assert peek_routing_name_from_public_id(public) == "prime-coding"


def test_legacy_maestro_hex_prefix_still_resolves() -> None:
    internal = "gemini-3.5-flash-low"
    slug = route_alphabetic_slug(internal)
    _memory_routes[route_hash(internal)] = normalize_routing_name(internal)
    legacy = f"claude-maestro-{route_hash(internal)}"
    resolved = asyncio.run(resolve_desktop_public_id(legacy))
    assert resolved == normalize_routing_name(internal)
    assert slug  # alphabetic slug exists for new ids


def test_opaque_id_resolves_with_desktop_header() -> None:
    internal = "google/codegemma-7b"
    public = to_desktop_public_id(internal)
    resolved = asyncio.run(_resolve_claude_request_model(public, desktop=True))
    assert resolved == normalize_routing_name(internal)
    assert not is_maestro_desktop_opaque_id(resolved)
