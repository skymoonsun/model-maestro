"""Tests for Google v1internal tool parameter schema sanitization."""

from app.google_proxy import _clean_json_schema, _convert_tools_to_gemini


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
