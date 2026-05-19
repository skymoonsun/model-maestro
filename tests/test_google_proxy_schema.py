"""Tests for Google v1internal tool parameter schema sanitization."""

import pytest
from fastapi import HTTPException

from app.gemini_schema import clean_json_schema_for_gemini
from app.google_proxy import (
    GEMINI_SKIP_THOUGHT_SIGNATURE,
    _antigravity_model_variants,
    _clean_json_schema,
    _convert_messages_to_contents,
    _convert_tools_to_gemini,
    _extract_generation_limits,
    _finalize_gemini_tool_parameters,
    _requires_thought_signature,
    resolve_antigravity_model_name,
    transform_openai_to_google,
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


def test_clean_json_schema_inlines_ref_defs() -> None:
    schema = {
        "$defs": {
            "SkillInput": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            }
        },
        "type": "object",
        "properties": {
            "input": {"$ref": "#/$defs/SkillInput"},
        },
    }
    cleaned = clean_json_schema_for_gemini(schema)
    assert cleaned["type"] == "OBJECT"
    assert "input" in cleaned["properties"]
    inner = cleaned["properties"]["input"]
    assert inner["type"] == "OBJECT"
    assert "command" in inner["properties"]


def test_clean_json_schema_simplifies_anyof_nullable() -> None:
    schema = {
        "type": "object",
        "properties": {
            "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
    }
    cleaned = clean_json_schema_for_gemini(schema)
    assert cleaned["properties"]["value"]["type"] == "STRING"


def test_finalize_gemini_tool_parameters_uppercases_types() -> None:
    params = _finalize_gemini_tool_parameters({
        "type": "object",
        "properties": {"path": {"type": "string"}},
    })
    assert params["type"] == "OBJECT"
    assert params["properties"]["path"]["type"] == "STRING"


def test_extract_generation_limits_reads_options_num_predict() -> None:
    max_tokens, gen_fields = _extract_generation_limits({
        "options": {"num_predict": 32000, "temperature": 0.2},
    })
    assert max_tokens == 32000
    assert gen_fields["temperature"] == 0.2


def test_transform_openai_to_google_tools_use_validated_mode() -> None:
    body = transform_openai_to_google({
        "model": "gemini-3.1-pro-high",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 32000,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "Skill",
                    "description": "Run a skill",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    })
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"
    # maxOutputTokens must exceed thinkingBudget for v1internal
    assert body["generationConfig"]["maxOutputTokens"] == 57344
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 49152


def test_transform_adaptive_thinking_uses_safe_budget() -> None:
    body = transform_openai_to_google({
        "model": "gemini-3.1-pro-high",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 32000,
        "thinking": {"type": "adaptive"},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "Read",
                    "description": "Read file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    })
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 24576
    assert body["safetySettings"]


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


def test_requires_thought_signature_for_gemini_3_flash() -> None:
    assert _requires_thought_signature("gemini-3.5-flash-low") is True
    assert _requires_thought_signature("claude-opus-4-6-thinking") is False


def test_convert_messages_injects_thought_signature_on_function_calls() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tu_skill",
                    "type": "function",
                    "function": {"name": "Skill", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tu_skill", "name": "Skill", "content": "ok"},
        {"role": "user", "content": "continue"},
    ]
    contents, _ = _convert_messages_to_contents(messages, model_name="gemini-3.5-flash-low")
    func_parts = [
        p
        for c in contents
        if c["role"] == "model"
        for p in c["parts"]
        if "functionCall" in p
    ]
    assert len(func_parts) == 1
    assert func_parts[0]["thoughtSignature"] == GEMINI_SKIP_THOUGHT_SIGNATURE
    assert func_parts[0]["functionCall"]["name"] == "Skill"


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


def test_tool_role_has_only_function_response_part() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tu_1",
                    "type": "function",
                    "function": {"name": "Read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tu_1", "name": "Read", "content": "result body"},
    ]
    contents, _ = _convert_messages_to_contents(messages)
    user_parts = [c for c in contents if c["role"] == "user"][0]["parts"]
    assert len(user_parts) == 1
    assert "functionResponse" in user_parts[0]
    assert user_parts[0]["functionResponse"]["response"]["result"] == "result body"


def test_user_turn_orders_function_response_before_text() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tu_1",
                    "type": "function",
                    "function": {"name": "Read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tu_1", "name": "Read", "content": "data"},
        {"role": "user", "content": "continue"},
    ]
    contents, _ = _convert_messages_to_contents(messages)
    user_parts = [c for c in contents if c["role"] == "user"][0]["parts"]
    assert "functionResponse" in user_parts[0]
    assert "text" in user_parts[-1]


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
