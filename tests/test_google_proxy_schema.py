"""Tests for Google v1internal tool parameter schema sanitization."""

import pytest
from fastapi import HTTPException

from app.google_proxy import (
    _antigravity_model_variants,
    _clean_json_schema,
    _convert_messages_to_contents,
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


def test_convert_messages_does_not_merge_consecutive_model_turns() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tu_a",
                    "type": "function",
                    "function": {"name": "Read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tu_a", "name": "Read", "content": "ok"},
        {"role": "assistant", "content": "done"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tu_b",
                    "type": "function",
                    "function": {"name": "Bash", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tu_b", "name": "Bash", "content": "ok"},
    ]
    contents, _ = _convert_messages_to_contents(messages)
    model_turns = [c for c in contents if c["role"] == "model"]
    assert len(model_turns) == 2
    assert any(
        part.get("functionCall", {}).get("id") == "tu_a"
        for part in model_turns[0]["parts"]
    )
    assert any(
        part.get("functionCall", {}).get("id") == "tu_b"
        for part in model_turns[1]["parts"]
    )


def test_convert_messages_merges_consecutive_user_tool_results() -> None:
    messages = [
        {"role": "user", "content": "run tools"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tu_1",
                    "type": "function",
                    "function": {"name": "Read", "arguments": "{}"},
                },
                {
                    "id": "tu_2",
                    "type": "function",
                    "function": {"name": "Bash", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "tu_1", "name": "Read", "content": "a"},
        {"role": "tool", "tool_call_id": "tu_2", "name": "Bash", "content": "b"},
    ]
    contents, _ = _convert_messages_to_contents(messages)
    user_turns = [c for c in contents if c["role"] == "user"]
    assert len(user_turns) == 2
    tool_responses = [
        p["functionResponse"]["id"]
        for p in user_turns[1]["parts"]
        if "functionResponse" in p
    ]
    assert tool_responses == ["tu_1", "tu_2"]


def test_antigravity_model_variants_adds_claude_prefix() -> None:
    variants = _antigravity_model_variants("opus-4-6-thinking")
    assert "opus-4-6-thinking" in variants
    assert "claude-opus-4-6-thinking" in variants


@pytest.mark.asyncio
async def test_resolve_antigravity_model_exact_catalog_match() -> None:
    resolved = await resolve_antigravity_model_name(
        "opus-4-6-thinking",
        known_model_names=["claude-opus-4-6-thinking", "gemini-3-flash"],
    )
    assert resolved == "claude-opus-4-6-thinking"


@pytest.mark.asyncio
async def test_resolve_antigravity_passes_through_when_catalog_empty() -> None:
    resolved = await resolve_antigravity_model_name("opus-4-6-thinking")
    assert resolved == "opus-4-6-thinking"


@pytest.mark.asyncio
async def test_resolve_raises_when_catalog_has_no_exact_match() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await resolve_antigravity_model_name(
            "haiku-4-5-20251001",
            known_model_names=["claude-opus-4-6-thinking"],
        )
    assert exc_info.value.status_code == 404
