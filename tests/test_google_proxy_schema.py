"""Tests for Google v1internal tool parameter schema sanitization."""

import pytest

from app.google_proxy import (
    _antigravity_model_variants,
    _clean_json_schema,
    _convert_tools_to_gemini,
    resolve_antigravity_model_name,
)


def test_clean_json_schema_strips_gemini_incompatible_fields() -> None:
    raw = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "const": "/tmp",
                "description": "File path",
            },
            "meta": {
                "type": "object",
                "propertyNames": {"type": "string"},
                "properties": {"k": {"type": "string"}},
            },
        },
        "required": ["path"],
        "multipleOf": 1,
    }
    cleaned = _clean_json_schema(raw)

    assert "$schema" not in cleaned
    assert "multipleOf" not in cleaned
    props = cleaned["properties"]
    assert "const" not in props["path"]
    assert props["path"]["enum"] == ["/tmp"]
    assert "propertyNames" not in props["meta"]


def test_convert_tools_to_gemini_sanitizes_parameters() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "example_tool",
                "description": "Demo",
                "parameters": {
                    "$schema": "https://example/schema",
                    "type": "object",
                    "properties": {"x": {"type": "string", "const": "y"}},
                },
            },
        }
    ]
    gemini = _convert_tools_to_gemini(tools)
    params = gemini[0]["functionDeclarations"][0]["parameters"]
    assert "$schema" not in params
    assert params["properties"]["x"]["enum"] == ["y"]


def test_antigravity_model_variants_adds_claude_prefix() -> None:
    variants = _antigravity_model_variants("opus-4-6-thinking")
    assert "opus-4-6-thinking" in variants
    assert "claude-opus-4-6-thinking" in variants


@pytest.mark.asyncio
async def test_resolve_antigravity_model_from_catalog() -> None:
    resolved = await resolve_antigravity_model_name(
        "opus-4-6-thinking",
        known_model_names=["claude-opus-4-6-thinking", "gemini-3-flash"],
    )
    assert resolved == "claude-opus-4-6-thinking"


@pytest.mark.asyncio
async def test_resolve_antigravity_model_heuristic_without_catalog() -> None:
    resolved = await resolve_antigravity_model_name("opus-4-6-thinking")
    assert resolved == "claude-opus-4-6-thinking"
