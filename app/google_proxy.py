"""Google v1internal API proxy for Antigravity nodes.

Handles:
- OpenAI format -> Google v1internal format transformation
- Google v1internal -> OpenAI format response transformation
- SSE streaming for streamGenerateContent?alt=sse
- Endpoint fallback (Sandbox -> Daily -> Prod)
- Model discovery via fetchAvailableModels
- Token health validation
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, AsyncGenerator
from datetime import datetime

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.gemini_schema import clean_json_schema_for_gemini
from app.google_auth import (
    V1_INTERNAL_BASE_URLS,
    build_v1internal_headers,
    derive_stable_session_id,
    ensure_fresh_token,
)

logger = logging.getLogger(__name__)

# Retry alternate Google hosts only for these statuses. Do not fall through on 429
# (quota exhausted) — that hammers prod and hides the real error.
_V1INTERNAL_ENDPOINT_FALLBACK_CODES = frozenset({408, 404, 500, 502, 503, 504})

# Google v1internal tool_use.id regex: ^[a-zA-Z0-9_-]+$
# Claude Code sends base64-like IDs that may contain + / =; strip those chars.
_TOOL_ID_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _sanitize_tool_id(tool_id: str) -> str:
    """Strip characters Google v1internal rejects from tool IDs, then truncate to 64 chars.

    Google v1internal regex: ^[a-zA-Z0-9_-]+$
    Claude Code may send base64-like IDs (toolu_01H…); most chars are already safe,
    but we defensively strip anything outside the allowed set.
    """
    if not tool_id:
        return tool_id
    cleaned = "".join(c for c in tool_id if c in _TOOL_ID_ALLOWED)
    cleaned = cleaned[:64]
    if not cleaned:
        # If stripping empties the ID, generate a fresh v1internal-safe ID
        cleaned = f"call_{uuid.uuid4().hex[:12]}"
    return cleaned

# Sentinel accepted by Gemini v1internal when no real thought signature is available
# (e.g. Claude Code tool_use replay). See Antigravity-Manager openai/request.rs.
GEMINI_SKIP_THOUGHT_SIGNATURE = "skip_thought_signature_validator"

_ANTIGRAVITY_IDENTITY = (
    "You are Antigravity, a powerful agentic AI coding assistant designed by the "
    "Google Deepmind team working on Advanced Agentic Coding.\n"
    "You are pair programming with a USER to solve their coding task. The task may "
    "require creating a new codebase, modifying or debugging an existing codebase, "
    "or simply answering a question.\n"
    "**Absolute paths only**\n"
    "**Proactiveness**"
)


def _requires_thought_signature(model_name: str) -> bool:
    """Whether functionCall parts must include thoughtSignature for this model."""
    m = (model_name or "").lower()
    if "claude" in m:
        return False
    if "gemini-3" in m or "gemini-2.0-pro" in m:
        return True
    if "-thinking" in m:
        return True
    return False


def _make_function_call_part(
    func_call: Dict[str, Any],
    *,
    thought_signature: Optional[str] = None,
    model_name: str = "",
) -> Dict[str, Any]:
    """Build a Gemini content part for a functionCall with optional thoughtSignature."""
    part: Dict[str, Any] = {"functionCall": func_call}
    sig = thought_signature
    if not sig and _requires_thought_signature(model_name):
        sig = GEMINI_SKIP_THOUGHT_SIGNATURE
    if sig:
        part["thoughtSignature"] = sig
    return part


def _ensure_thought_signatures_on_contents(
    contents: List[Dict[str, Any]],
    model_name: str,
) -> None:
    """Backfill thoughtSignature on any functionCall parts still missing one."""
    if not _requires_thought_signature(model_name):
        return
    for msg in contents:
        for part in msg.get("parts", []):
            if isinstance(part, dict) and "functionCall" in part and "thoughtSignature" not in part:
                part["thoughtSignature"] = GEMINI_SKIP_THOUGHT_SIGNATURE


def _is_gemini_thinking_model(model_name: str) -> bool:
    """Whether to inject generationConfig.thinkingConfig for this mapped model."""
    m = (model_name or "").lower()
    if "claude" in m:
        return m.endswith("-thinking")
    return (
        "-thinking" in m
        or "gemini-2.0-pro" in m
        or "gemini-3-pro" in m
        or "gemini-3.1-pro" in m
        or "gemini-3-flash" in m
        or "gemini-3.1-flash" in m
    )


def _get_thinking_budget(model_name: str) -> int:
    """Model-specific thinking budget caps (Antigravity model_specs defaults)."""
    m = (model_name or "").lower()
    if "gemini-3.1-pro" in m or "gemini-3-pro-high" in m:
        return 49152
    if "gemini-3-flash" in m or "gemini-3.1-flash" in m or "gemini-2.5" in m:
        return 32768
    return 24576


def _extract_generation_limits(data: Dict[str, Any]) -> Tuple[Optional[int], Dict[str, Any]]:
    """Read max_tokens and generation knobs from OpenAI body or Ollama-style options."""
    options = data.get("options") if isinstance(data.get("options"), dict) else {}

    max_tokens = data.get("max_tokens") or data.get("max_completion_tokens")
    if max_tokens is None and isinstance(options, dict):
        max_tokens = options.get("num_predict")

    gen_fields: Dict[str, Any] = {}
    for src_key, dst_key in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("top_k", "top_k"),
    ):
        val = data.get(src_key)
        if val is None and isinstance(options, dict):
            val = options.get(src_key)
        if val is not None:
            gen_fields[dst_key] = val

    return (int(max_tokens) if max_tokens is not None else None), gen_fields


def _enforce_uppercase_schema_types(schema: Any) -> Any:
    """Gemini v1internal expects protobuf-style type names (OBJECT, STRING, ...)."""
    if isinstance(schema, list):
        return [_enforce_uppercase_schema_types(item) for item in schema]

    if not isinstance(schema, dict):
        return schema

    out: Dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            out[key] = value.upper()
        elif isinstance(value, dict):
            out[key] = _enforce_uppercase_schema_types(value)
        elif isinstance(value, list):
            out[key] = [_enforce_uppercase_schema_types(item) for item in value]
        else:
            out[key] = value

    if "properties" in out and "type" not in out:
        out["type"] = "OBJECT"
    return out


def _finalize_gemini_tool_parameters(parameters: Any) -> Dict[str, Any]:
    """Sanitize Anthropic/OpenAI JSON Schema for Gemini functionDeclarations."""
    return clean_json_schema_for_gemini(parameters)


def _deep_clean_undefined(value: Any) -> Any:
    """Remove Cherry Studio / client placeholders that break v1internal JSON."""
    if isinstance(value, str):
        return None if value == "[undefined]" else value
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            cleaned = _deep_clean_undefined(item)
            if cleaned is not None:
                cleaned_list.append(cleaned)
        return cleaned_list
    if isinstance(value, dict):
        cleaned_dict: Dict[str, Any] = {}
        for key, item in value.items():
            cleaned = _deep_clean_undefined(item)
            if cleaned is not None:
                cleaned_dict[key] = cleaned
        return cleaned_dict
    return value


_V1INTERNAL_SAFETY_SETTINGS: List[Dict[str, str]] = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "OFF"},
]


# =============================================================================
# Request Transformation: OpenAI -> Google v1internal
# =============================================================================

def _convert_messages_to_contents(
    messages: List[Dict[str, Any]],
    model_name: str = "",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Convert OpenAI messages to Google contents format.

    Returns:
        Tuple of (contents, system_instructions)
    """
    system_instructions: List[str] = []
    contents: List[Dict[str, Any]] = []
    tool_call_names: Dict[str, str] = {}
    last_thought_signature: Optional[str] = None

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role in ("system", "developer"):
            if isinstance(content, str):
                system_instructions.append(content)
            elif isinstance(content, list):
                texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
                if texts:
                    system_instructions.append("\n".join(texts))
            continue

        # Map roles: assistant -> model, tool/function -> user
        google_role = {"assistant": "model", "tool": "user", "function": "user"}.get(role, role)
        parts: List[Dict[str, Any]] = []

        # Handle reasoning_content (thinking blocks)
        reasoning = msg.get("reasoning_content")
        if reasoning and isinstance(reasoning, str) and reasoning != "[undefined]":
            thought_part: Dict[str, Any] = {"text": reasoning, "thought": True}
            msg_sig = msg.get("thought_signature") or msg.get("thoughtSignature")
            if isinstance(msg_sig, str) and msg_sig:
                last_thought_signature = msg_sig
                thought_part["thoughtSignature"] = msg_sig
            elif _requires_thought_signature(model_name):
                thought_part["thoughtSignature"] = GEMINI_SKIP_THOUGHT_SIGNATURE
            parts.append(thought_part)

        # Handle content (tool role uses functionResponse only — not a duplicate text part)
        if role not in ("tool", "function"):
            if isinstance(content, str):
                if content:
                    parts.append({"text": content})
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text = block.get("text", "")
                        if text:
                            parts.append({"text": text})
                    elif block_type == "tool_use" and role == "assistant":
                        tu_name = block.get("name", "")
                        tu_id_raw = block.get("id", "")
                        tu_id = _sanitize_tool_id(tu_id_raw)
                        if tu_id:
                            tool_call_names[tu_id] = tu_name
                        if tu_name == "local_shell_call":
                            tu_name = "shell"
                        tu_sig = block.get("thought_signature") or block.get("thoughtSignature")
                        if isinstance(tu_sig, str) and tu_sig:
                            last_thought_signature = tu_sig
                        parts.append(
                            _make_function_call_part(
                                {
                                    "name": tu_name,
                                    "args": block.get("input", {}) or {},
                                    "id": tu_id,
                                },
                                thought_signature=last_thought_signature if isinstance(last_thought_signature, str) else None,
                                model_name=model_name,
                            )
                        )
                    elif block_type == "image_url":
                        image_url = block.get("image_url", {})
                        url = image_url.get("url", "")
                        if url.startswith("data:"):
                            comma_pos = url.find(",")
                            if comma_pos != -1:
                                mime_part = url[5:comma_pos]
                                mime_type = mime_part.split(";")[0] if ";" in mime_part else mime_part
                                data = url[comma_pos + 1:]
                                parts.append({"inlineData": {"mimeType": mime_type or "image/jpeg", "data": data}})
                        elif url.startswith("http"):
                            parts.append({"fileData": {"fileUri": url, "mimeType": "image/jpeg"}})
                    elif block_type == "tool_result" and role == "user":
                        tr_id_raw = block.get("tool_use_id", "")
                        tr_id = _sanitize_tool_id(tr_id_raw)
                        tr_name = block.get("name") or tool_call_names.get(tr_id, "") or "unknown"
                        if tr_name == "local_shell_call":
                            tr_name = "shell"
                        tr_content = block.get("content", "")
                        if isinstance(tr_content, list):
                            tr_texts = [
                                b.get("text", "")
                                for b in tr_content
                                if isinstance(b, dict) and b.get("type") == "text"
                            ]
                            tr_content = "\n".join(tr_texts) if tr_texts else ""
                        elif not isinstance(tr_content, str):
                            tr_content = str(tr_content) if tr_content is not None else ""
                        parts.append({
                            "functionResponse": {
                                "name": tr_name,
                                "response": {"result": tr_content},
                                "id": tr_id,
                            }
                        })

        # Handle tool calls (assistant message)
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function", {})
                if not isinstance(func, dict):
                    continue
                name = func.get("name", "")
                arguments = func.get("arguments", "{}")
                call_id_raw = tc.get("id", "")
                call_id = _sanitize_tool_id(call_id_raw)

                # Normalize shell tool name
                if name == "local_shell_call":
                    name = "shell"

                try:
                    args_parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (json.JSONDecodeError, TypeError):
                    args_parsed = {}

                if call_id:
                    tool_call_names[call_id] = name

                tc_sig = tc.get("thought_signature") or tc.get("thoughtSignature")
                if isinstance(tc_sig, str) and tc_sig:
                    last_thought_signature = tc_sig
                parts.append(
                    _make_function_call_part(
                        {
                            "name": name,
                            "args": args_parsed,
                            "id": call_id,
                        },
                        thought_signature=last_thought_signature if isinstance(last_thought_signature, str) else None,
                        model_name=model_name,
                    )
                )

        # Handle tool response
        if role in ("tool", "function"):
            tool_call_id_raw = msg.get("tool_call_id", "")
            tool_call_id = _sanitize_tool_id(tool_call_id_raw)
            name = msg.get("name") or tool_call_names.get(tool_call_id, "")
            if not name:
                name = "unknown"
            if name == "local_shell_call":
                name = "shell"

            content_val = ""
            if isinstance(content, str):
                content_val = content
            elif isinstance(content, list):
                texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
                content_val = "\n".join(texts)

            parts.append({
                "functionResponse": {
                    "name": name,
                    "response": {"result": content_val},
                    "id": tool_call_id,
                }
            })

        if parts:
            contents.append({"role": google_role, "parts": parts})

    def _has_function_call(parts: List[Dict[str, Any]]) -> bool:
        return any("functionCall" in part for part in parts)

    def _reorder_user_parts(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """functionResponse parts must come before plain text for Claude-on-Google."""
        responses = [p for p in parts if "functionResponse" in p]
        rest = [p for p in parts if "functionResponse" not in p]
        return responses + rest

    # Merge consecutive user turns. For model turns, merge only when we are not
    # stacking two separate tool-call rounds (breaks tool_use / tool_result pairing).
    merged: List[Dict[str, Any]] = []
    for msg in contents:
        if not merged:
            merged.append(msg)
            continue
        prev = merged[-1]
        if prev["role"] == "user" and msg["role"] == "user":
            prev["parts"].extend(msg["parts"])
            prev["parts"] = _reorder_user_parts(prev["parts"])
        elif prev["role"] == "model" and msg["role"] == "model":
            if _has_function_call(prev["parts"]) and _has_function_call(msg["parts"]):
                merged.append(msg)
            else:
                prev["parts"].extend(msg["parts"])
        else:
            merged.append(msg)

    for msg in merged:
        if msg["role"] == "user":
            msg["parts"] = _reorder_user_parts(msg["parts"])

    _ensure_thought_signatures_on_contents(merged, model_name)

    return merged, system_instructions


def _convert_tools_to_gemini(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tools to Gemini functionDeclarations format."""
    result: List[Dict[str, Any]] = []
    skip_names = frozenset({
        "web_search",
        "google_search",
        "web_search_20250305",
        "builtin_web_search",
    })

    for tool in tools:
        if not isinstance(tool, dict):
            continue

        func = tool.get("function", {})
        if not isinstance(func, dict):
            func_name = tool.get("name", "")
            func_params = tool.get("parameters", {})
            if func_name:
                func = {
                    "name": func_name,
                    "parameters": func_params,
                    "description": tool.get("description", ""),
                }
            else:
                continue

        name = func.get("name", "")
        if not name or name in skip_names:
            continue
        if name == "local_shell_call":
            name = "shell"

        parameters = _finalize_gemini_tool_parameters(func.get("parameters"))
        description = func.get("description", "") or ""

        result.append({
            "name": name,
            "description": description,
            "parameters": parameters,
        })

    if result:
        return [{"functionDeclarations": result}]
    return []


# JSON Schema keywords rejected by Google v1internal function_declarations.parameters
_GEMINI_FORBIDDEN_SCHEMA_KEYS = frozenset({
    "$schema",
    "$id",
    "$defs",
    "$anchor",
    "$comment",
    "$vocabulary",
    "$dynamicRef",
    "$ref",
    "multipleOf",
    "pattern",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "propertyNames",
    "unevaluatedProperties",
    "unevaluatedItems",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
    "contentEncoding",
    "contentMediaType",
    "examples",
    "readOnly",
    "writeOnly",
    "deprecated",
    "not",
    "prefixItems",
    "contains",
})


def _clean_json_schema(schema: Any) -> Any:
    """Remove or rewrite JSON Schema fields incompatible with Gemini tool parameters."""
    if isinstance(schema, list):
        return [_clean_json_schema(item) for item in schema]

    if not isinstance(schema, dict):
        return schema

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in _GEMINI_FORBIDDEN_SCHEMA_KEYS or key.startswith("$"):
            continue
        if key == "const":
            if "enum" not in schema and "enum" not in cleaned:
                cleaned["enum"] = [value]
            continue
        if isinstance(value, dict):
            cleaned[key] = _clean_json_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [_clean_json_schema(item) for item in value]
        else:
            cleaned[key] = value
    return cleaned


def transform_openai_to_google(data: Dict[str, Any]) -> Dict[str, Any]:
    """Transform OpenAI chat completion request to Google v1internal body format.

    Returns the inner request body (contents, generationConfig, systemInstruction, tools).
    This must be wrapped by wrap_v1internal_request before sending.
    """
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        messages = []

    model_name = (data.get("model") or "").lower()
    contents, system_instructions = _convert_messages_to_contents(messages, model_name=model_name)

    max_tokens, gen_fields = _extract_generation_limits(data)

    # Build generationConfig
    gen_config: Dict[str, Any] = {
        "temperature": gen_fields.get("temperature", data.get("temperature", 1.0)),
        "topP": gen_fields.get("top_p", data.get("top_p", 1.0)),
        "topK": gen_fields.get("top_k", data.get("top_k", 40)),
    }

    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens

    user_thinking_budget: Optional[int] = None
    thinking_cfg = data.get("thinking")
    if isinstance(thinking_cfg, dict):
        raw_budget = thinking_cfg.get("budget_tokens") or thinking_cfg.get("budgetTokens")
        if raw_budget is not None:
            try:
                user_thinking_budget = int(raw_budget)
            except (TypeError, ValueError):
                user_thinking_budget = None

    if _is_gemini_thinking_model(model_name):
        thinking_budget = _get_thinking_budget(model_name)
        if isinstance(thinking_cfg, dict):
            thinking_type = thinking_cfg.get("type")
            # Gemini v1internal: adaptive maps to fixed budget (not thinkingLevel)
            if thinking_type == "adaptive":
                thinking_budget = 24576
            elif thinking_type == "enabled" and user_thinking_budget is not None:
                thinking_budget = min(user_thinking_budget, thinking_budget)

        gen_config["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": thinking_budget,
        }

        current_max = int(gen_config.get("maxOutputTokens") or 0)
        min_overhead = 8192
        if current_max <= thinking_budget:
            gen_config["maxOutputTokens"] = min(thinking_budget + min_overhead, 65536)
        elif current_max > 65536:
            gen_config["maxOutputTokens"] = 65536

    # Handle n -> candidateCount
    n = data.get("n")
    if n:
        gen_config["candidateCount"] = n

    result: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": gen_config,
        "safetySettings": _V1INTERNAL_SAFETY_SETTINGS,
    }

    # System instruction (Antigravity identity + user/developer prompts)
    sys_parts: List[Dict[str, Any]] = []
    user_has_antigravity = any(
        isinstance(s, str) and "You are Antigravity" in s for s in system_instructions
    )
    if not user_has_antigravity:
        sys_parts.append({"text": _ANTIGRAVITY_IDENTITY})
    for inst in system_instructions:
        if isinstance(inst, str) and inst.strip():
            sys_parts.append({"text": inst})
    if sys_parts:
        result["systemInstruction"] = {"role": "user", "parts": sys_parts}

    # Tools
    tools = data.get("tools")
    if tools and isinstance(tools, list):
        gemini_tools = _convert_tools_to_gemini(tools)
        if gemini_tools:
            result["tools"] = gemini_tools
            result["toolConfig"] = {
                "functionCallingConfig": {"mode": "VALIDATED"},
            }

    # Tool choice
    tool_choice = data.get("tool_choice")
    if tool_choice == "auto":
        pass  # Default
    elif tool_choice == "none":
        result.pop("tools", None)
        result.pop("toolConfig", None)
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        func_name = tool_choice.get("function", {}).get("name", "")
        if func_name:
            if func_name == "local_shell_call":
                func_name = "shell"
            result["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [func_name],
                }
            }

    cleaned = _deep_clean_undefined(result)
    if isinstance(cleaned, dict):
        return finalize_v1internal_inner_request(cleaned, model_name)
    return result


def finalize_v1internal_inner_request(body: Dict[str, Any], model_name: str) -> Dict[str, Any]:
    """Second pass on inner request before v1internal wrap (Antigravity wrap_request parity)."""
    model_lower = (model_name or "").lower()

    tools = body.get("tools")
    if isinstance(tools, list):
        for tool_group in tools:
            if not isinstance(tool_group, dict):
                continue
            decls = tool_group.get("functionDeclarations")
            if not isinstance(decls, list):
                continue
            for decl in decls:
                if not isinstance(decl, dict):
                    continue
                for bad_key in (
                    "type",
                    "strict",
                    "format",
                    "additionalProperties",
                    "external_web_access",
                ):
                    decl.pop(bad_key, None)
                if "parametersJsonSchema" in decl:
                    decl["parameters"] = decl.pop("parametersJsonSchema")
                name = decl.get("name", "")
                if name == "local_shell_call":
                    decl["name"] = "shell"
                params = decl.get("parameters")
                decl["parameters"] = clean_json_schema_for_gemini(params if params is not None else {})

    gen_config = body.get("generationConfig")
    if isinstance(gen_config, dict):
        thinking = gen_config.get("thinkingConfig")
        if isinstance(thinking, dict):
            level = thinking.pop("thinkingLevel", None)
            if level is not None and thinking.get("thinkingBudget") is None:
                level_str = str(level).upper()
                cap = _get_thinking_budget(model_name)
                budget_map = {
                    "NONE": 0,
                    "LOW": max(cap // 4, 4096),
                    "MEDIUM": max(cap // 2, 8192),
                    "HIGH": cap,
                }
                thinking["thinkingBudget"] = budget_map.get(level_str, max(cap // 2, 8192))

            budget = thinking.get("thinkingBudget")
            max_out = gen_config.get("maxOutputTokens")
            try:
                budget_i = int(budget) if budget is not None else 0
            except (TypeError, ValueError):
                budget_i = 0
            try:
                max_i = int(max_out) if max_out is not None else 0
            except (TypeError, ValueError):
                max_i = 0
            if budget_i > 0 and max_i <= budget_i:
                gen_config["maxOutputTokens"] = min(budget_i + 8192, 65536)

        max_out = gen_config.get("maxOutputTokens")
        if max_out is not None:
            try:
                if int(max_out) > 65536:
                    gen_config["maxOutputTokens"] = 65536
            except (TypeError, ValueError):
                gen_config.pop("maxOutputTokens", None)

        if "claude-opus-4-6-thinking" in model_lower and isinstance(thinking, dict):
            thinking["thinkingBudget"] = 24576
            gen_config["maxOutputTokens"] = 57344
            gen_config.pop("stopSequences", None)

    return body


def wrap_v1internal_request(
    body: Dict[str, Any],
    project_id: str,
    mapped_model: str,
    *,
    message_count: int = 1,
    session_key: Optional[str] = None,
    is_image_gen: bool = False,
) -> Dict[str, Any]:
    """Wrap inner body with v1internal envelope (Antigravity gemini/wrapper.rs parity).

    HTTP method stays ``streamGenerateContent`` / ``generateContent``; envelope
    ``requestType`` is ``agent`` for chat (not ``stream_generate_content``).
    """
    inner = dict(body)
    if session_key:
        inner["sessionId"] = derive_stable_session_id(session_key)

    timestamp_ms = int(time.time() * 1000)
    request_id = f"agent/{timestamp_ms}/{uuid.uuid4().hex[:8]}"

    envelope: Dict[str, Any] = {
        "project": project_id,
        "requestId": request_id,
        "request": inner,
        "model": mapped_model,
        "userAgent": "antigravity",
        "requestType": "image_gen" if is_image_gen else "agent",
    }
    if not is_image_gen:
        envelope["enabledCreditTypes"] = ["GOOGLE_ONE_AI"]
    return envelope


def _antigravity_model_variants(requested: str) -> List[str]:
    """Candidate model IDs for exact catalog lookup (with/without ``claude-`` prefix)."""
    if not requested:
        return []
    variants: List[str] = []
    seen: Set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            variants.append(value)

    add(requested)
    if requested.startswith("claude-"):
        add(requested[7:])
    else:
        add(f"claude-{requested}")
    return variants


async def resolve_antigravity_model_name(
    requested: str,
    node_id: Optional[int] = None,
    known_model_names: Optional[Iterable[str]] = None,
) -> str:
    """Map to a synced Google model id using exact catalog matches only.

    Claude Code may send names without the ``claude-`` prefix; the node catalog
    often stores the full id. No fuzzy scoring or prefix guessing when unmatched.
    """
    if not requested:
        return requested

    catalog: Set[str] = set()
    if known_model_names is not None:
        catalog.update(known_model_names)
    elif node_id is not None:
        try:
            from app.database import async_session_maker
            from app.repositories.node_repository import NodeModelRepository

            async with async_session_maker() as session:
                repo = NodeModelRepository(session)
                for row in await repo.get_models_for_node(node_id):
                    if row.model_name:
                        catalog.add(row.model_name)
        except Exception as e:
            logger.warning(f"[Antigravity] Could not load node model catalog: {e}")

    if not catalog:
        return requested

    for candidate in _antigravity_model_variants(requested):
        if candidate in catalog:
            if candidate != requested:
                logger.info(
                    f"[Antigravity] Resolved model '{requested}' -> '{candidate}' (catalog)"
                )
            return candidate

    sample = ", ".join(sorted(catalog)[:5])
    more = f" (+{len(catalog) - 5} more)" if len(catalog) > 5 else ""
    raise HTTPException(
        status_code=404,
        detail=(
            f"Model '{requested}' is not available on this Antigravity node. "
            f"Sync models on the node or pin Claude Code env vars to a listed id. "
            f"Known: {sample}{more}"
        ),
    )


# =============================================================================
# Response Transformation: Google -> OpenAI
# =============================================================================

def _extract_usage_metadata(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract usage metadata from Google response."""
    usage = raw.get("usageMetadata")
    if not usage:
        return None

    prompt_tokens = usage.get("promptTokenCount", 0)
    completion_tokens = usage.get("candidatesTokenCount", 0)
    total_tokens = usage.get("totalTokenCount", 0)
    cached_tokens = usage.get("cachedContentTokenCount")

    result: Dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

    if cached_tokens is not None:
        result["prompt_tokens_details"] = {"cached_tokens": cached_tokens}

    return result


def transform_google_to_openai(
    gemini_response: Dict[str, Any],
    model_name: str,
) -> Dict[str, Any]:
    """Transform Google v1internal response to OpenAI chat.completion format."""
    # Unwrap the response envelope if present
    raw = gemini_response.get("response", gemini_response)

    choices: List[Dict[str, Any]] = []
    candidates = raw.get("candidates", [])

    for idx, candidate in enumerate(candidates):
        content_out = ""
        thought_out = ""
        tool_calls: List[Dict[str, Any]] = []

        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            is_thought = part.get("thought", False)

            text = part.get("text", "")
            if text:
                if is_thought:
                    thought_out += text
                else:
                    content_out += text

            # Image inline data
            inline_data = part.get("inlineData")
            if inline_data:
                mime = inline_data.get("mimeType", "image/png")
                data = inline_data.get("data", "")
                if data:
                    content_out += f"![image](data:{mime};base64,{data})"

            # Function call
            func_call = part.get("functionCall")
            if func_call:
                name = func_call.get("name", "unknown")
                args = func_call.get("args", {})
                call_id = func_call.get("id", f"{name}-{uuid.uuid4().hex[:8]}")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                    },
                })

        # Grounding metadata
        grounding = candidate.get("groundingMetadata")
        if grounding:
            queries = grounding.get("webSearchQueries", [])
            if queries:
                content_out += "\n\n---\n**Searched:** " + ", ".join(queries)

            chunks = grounding.get("groundingChunks", [])
            if chunks:
                links = []
                for i, chunk in enumerate(chunks):
                    web = chunk.get("web", {})
                    title = web.get("title", "Source")
                    uri = web.get("uri", "#")
                    links.append(f"[{i + 1}] [{title}]({uri})")
                if links:
                    content_out += "\n\n**Sources:**\n" + "\n".join(links)

        # Finish reason mapping
        finish_reason = candidate.get("finishReason", "STOP")
        finish_map = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }
        mapped_finish = finish_map.get(finish_reason, "stop")

        # If tool calls exist, finish_reason should be tool_calls
        if tool_calls and mapped_finish == "stop":
            mapped_finish = "tool_calls"

        message: Dict[str, Any] = {
            "role": "assistant",
            "content": content_out if content_out else None,
        }
        if thought_out:
            message["reasoning_content"] = thought_out
        if tool_calls:
            message["tool_calls"] = tool_calls

        choices.append({
            "index": idx,
            "message": message,
            "finish_reason": mapped_finish,
        })

    response: Dict[str, Any] = {
        "id": raw.get("responseId", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
        "object": "chat.completion",
        "created": int(datetime.utcnow().timestamp()),
        "model": raw.get("modelVersion", model_name),
        "choices": choices,
    }

    usage = _extract_usage_metadata(raw)
    if usage:
        response["usage"] = usage

    return response


# =============================================================================
# v1internal API Client with Fallback
# =============================================================================

async def call_v1internal(
    method: str,
    access_token: str,
    body: Dict[str, Any],
    query_string: Optional[str] = None,
    project_id: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any], str]:
    """Call Google v1internal API with automatic endpoint fallback.

    Returns:
        Tuple of (status_code, response_json_or_error, final_url_used)
    """
    headers = build_v1internal_headers(access_token, extra_headers)

    # NOTE: x-goog-user-project header causes 403 PERMISSION_DENIED on v1internal.
    # The project_id is already sent in the request body envelope under "project".
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

    last_err = ""
    has_triggered_downgrade = False
    header_removed_for_403 = False

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0),
        http2=True,
    ) as client:
        while True:
            for idx, base_url in enumerate(V1_INTERNAL_BASE_URLS):
                if query_string:
                    url = f"{base_url}:{method}?{query_string}"
                else:
                    url = f"{base_url}:{method}"

                has_next = idx + 1 < len(V1_INTERNAL_BASE_URLS)

                try:
                    # For streamGenerateContent, use chunked body to avoid Content-Length issues
                    if method == "streamGenerateContent":
                        response = await client.post(
                            url,
                            headers=headers,
                            content=body_bytes,
                        )
                    else:
                        response = await client.post(
                            url,
                            headers=headers,
                            json=body,
                        )

                    if response.status_code == 200:
                        try:
                            resp_json = response.json()
                        except (json.JSONDecodeError, ValueError):
                            resp_json = {"raw_text": response.text}
                        return response.status_code, resp_json, url

                    # Log every non-200 response for debugging
                    logger.warning(
                        f"[v1internal] {url} returned {response.status_code}: {response.text[:500]}"
                    )

                    # Handle 403 with project header -> retry without it once
                    if (
                        response.status_code == 403
                        and not has_triggered_downgrade
                        and "x-goog-user-project" in headers
                    ):
                        logger.warning(
                            "Detected 403 with project header, retrying WITHOUT x-goog-user-project"
                        )
                        headers.pop("x-goog-user-project", None)
                        has_triggered_downgrade = True
                        header_removed_for_403 = True
                        break  # Restart from first endpoint

                    # Retryable status codes
                    if has_next and response.status_code in _V1INTERNAL_ENDPOINT_FALLBACK_CODES:
                        err_msg = f"Upstream {url} returned {response.status_code}"
                        logger.warning(err_msg)
                        last_err = err_msg
                        continue

                    # Non-retryable or last endpoint
                    try:
                        resp_json = response.json()
                    except (json.JSONDecodeError, ValueError):
                        resp_json = {"error": response.text}
                    return response.status_code, resp_json, url

                except httpx.RequestError as e:
                    err_msg = f"HTTP request failed at {url}: {e}"
                    logger.warning(err_msg)
                    last_err = err_msg
                    if not has_next:
                        break
                    continue

            # If we broke out due to 403 downgrade, retry all endpoints once more
            if header_removed_for_403:
                header_removed_for_403 = False
                continue
            else:
                break

    return 503, {"error": last_err or "All endpoints failed"}, ""


# =============================================================================
# Streaming: Google SSE -> OpenAI SSE
# =============================================================================

async def stream_google_to_openai(
    gemini_stream: AsyncGenerator[bytes, None],
    model_name: str,
) -> AsyncGenerator[bytes, None]:
    """Transform Google SSE stream to OpenAI SSE format."""
    stream_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(datetime.utcnow().timestamp())

    buffer = b""
    final_usage: Optional[Dict[str, Any]] = None
    error_occurred = False
    emitted_tool_calls: set = set()
    tool_call_index = 0

    async for chunk in gemini_stream:
        if not chunk:
            continue

        buffer += chunk

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line:
                continue

            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str.startswith("data: "):
                continue

            json_part = line_str[6:].strip()
            if json_part == "[DONE]":
                continue

            try:
                data = json.loads(json_part)
            except (json.JSONDecodeError, ValueError):
                continue

            # Unwrap response envelope if present
            actual_data = data.get("response", data)

            # Extract usage metadata
            usage_meta = actual_data.get("usageMetadata")
            if usage_meta:
                final_usage = _extract_usage_metadata(actual_data)

            candidates = actual_data.get("candidates", [])
            if not candidates:
                continue

            for idx, candidate in enumerate(candidates):
                parts = candidate.get("content", {}).get("parts", [])
                content_out = ""
                thought_out = ""
                tool_calls: List[Dict[str, Any]] = []

                for part in parts:
                    is_thought = part.get("thought", False)

                    text = part.get("text", "")
                    if text:
                        if is_thought:
                            thought_out += text
                        else:
                            content_out += text

                    # Image
                    inline_data = part.get("inlineData")
                    if inline_data:
                        mime = inline_data.get("mimeType", "image/png")
                        data_b64 = inline_data.get("data", "")
                        if data_b64:
                            content_out += f"![image](data:{mime};base64,{data_b64})"

                    # Function call
                    func_call = part.get("functionCall")
                    if func_call:
                        call_key = json.dumps(func_call, sort_keys=True)
                        if call_key not in emitted_tool_calls:
                            emitted_tool_calls.add(call_key)
                            name = func_call.get("name", "unknown")
                            args = func_call.get("args", {})
                            args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                            call_id = func_call.get("id", f"call_{name}_{tool_call_index}")

                            tool_calls.append({
                                "index": tool_call_index,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": args_str,
                                },
                            })
                            tool_call_index += 1

                # Grounding metadata
                grounding = candidate.get("groundingMetadata")
                if grounding:
                    queries = grounding.get("webSearchQueries", [])
                    if queries:
                        content_out += "\n\n---\n**Searched:** " + ", ".join(queries)

                    chunks = grounding.get("groundingChunks", [])
                    if chunks:
                        links = []
                        for i, chunk in enumerate(chunks):
                            web = chunk.get("web", {})
                            title = web.get("title", "Source")
                            uri = web.get("uri", "#")
                            links.append(f"[{i + 1}] [{title}]({uri})")
                        if links:
                            content_out += "\n\n**Sources:**\n" + "\n".join(links)

                # Finish reason
                finish_reason = candidate.get("finishReason", "STOP")
                finish_map = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "content_filter", "RECITATION": "content_filter"}
                mapped_finish = finish_map.get(finish_reason, "stop")

                if tool_calls and mapped_finish == "stop":
                    mapped_finish = "tool_calls"

                # Yield reasoning first
                if thought_out:
                    reasoning_chunk = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [{
                            "index": idx,
                            "delta": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": thought_out,
                            },
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(reasoning_chunk)}\n\n".encode("utf-8")

                # Yield content / tool calls
                delta: Dict[str, Any] = {}
                if content_out:
                    delta["content"] = content_out
                if tool_calls:
                    delta["tool_calls"] = tool_calls

                if delta or mapped_finish != "stop":
                    openai_chunk: Dict[str, Any] = {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [{
                            "index": idx,
                            "delta": delta if delta else {},
                            "finish_reason": mapped_finish if mapped_finish != "stop" else None,
                        }],
                    }

                    if mapped_finish != "stop" and final_usage:
                        openai_chunk["usage"] = final_usage
                        final_usage = None

                    yield f"data: {json.dumps(openai_chunk)}\n\n".encode("utf-8")

    if not error_occurred:
        yield b"data: [DONE]\n\n"


# =============================================================================
# Main Proxy Entry Point
# =============================================================================

async def proxy_antigravity_request(
    data: Dict[str, Any],
    stream: bool,
    endpoint: str,
    base_url: str,
    oauth_tokens: Dict[str, Any],
    project_id: Optional[str],
    node_headers: Optional[Dict[str, Any]],
    model_name: str,
    username: Optional[str],
    node_id: Optional[int] = None,
) -> Any:
    """Proxy a request to Google v1internal API (Antigravity node).

    Returns:
        StreamingResponse for streaming, or JSON dict for non-streaming.
    """
    from app.google_auth import ensure_fresh_token

    # Ensure token is fresh
    try:
        access_token = await ensure_fresh_token(
            oauth_tokens,
            client_id="",  # Not needed for refresh if refresh_token exists
            client_secret="",
        )
    except Exception as e:
        logger.error(f"[Antigravity] Token refresh failed: {e}")
        raise HTTPException(status_code=401, detail=f"Google OAuth token expired or invalid: {e}")

    if not project_id:
        logger.warning("[Antigravity] No project_id configured for node; will attempt without x-goog-user-project header")

    requested_model = (data.get("model") if isinstance(data, dict) else None) or model_name
    model_name = await resolve_antigravity_model_name(requested_model, node_id=node_id)
    if isinstance(data, dict):
        data = {**data, "model": model_name}

    # Transform OpenAI -> Google
    google_body = transform_openai_to_google(data)

    # Determine method and query string
    if stream:
        method = "streamGenerateContent"
        query_string = "alt=sse"
    else:
        method = "generateContent"
        query_string = None

    message_count = len(data.get("messages", [])) if isinstance(data.get("messages"), list) else 1
    session_key = (
        project_id
        or (oauth_tokens.get("refresh_token") if isinstance(oauth_tokens, dict) else None)
        or username
        or str(node_id or "")
    )
    wrapped = wrap_v1internal_request(
        google_body,
        project_id or "",
        model_name,
        message_count=message_count,
        session_key=str(session_key) if session_key else None,
    )

    logger.info(
        f"[Antigravity] Forwarding to Google v1internal ({method}), model={model_name}, "
        f"stream={stream}, requestType={wrapped.get('requestType')}"
    )

    if stream:
        # Streaming response
        headers = build_v1internal_headers(access_token)
        if node_headers:
            headers.update(node_headers)
        # NOTE: x-goog-user-project omitted — it causes 403; project is in body envelope

        body_bytes = json.dumps(wrapped, ensure_ascii=False).encode("utf-8")

        async def antigravity_stream_generator() -> AsyncGenerator[bytes, None]:
            last_err = ""
            has_triggered_downgrade = False
            header_removed_for_403 = False

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=30.0),
                http2=True,
            ) as client:
                while True:
                    for idx, base_url in enumerate(V1_INTERNAL_BASE_URLS):
                        if query_string:
                            url = f"{base_url}:{method}?{query_string}"
                        else:
                            url = f"{base_url}:{method}"

                        has_next = idx + 1 < len(V1_INTERNAL_BASE_URLS)

                        try:
                            async with client.stream(
                                "POST",
                                url,
                                headers=headers,
                                content=body_bytes,
                            ) as resp:
                                if resp.status_code == 200:
                                    async for chunk in stream_google_to_openai(
                                        resp.aiter_raw(),
                                        model_name,
                                    ):
                                        yield chunk
                                    return

                                if (
                                    resp.status_code == 403
                                    and not has_triggered_downgrade
                                    and "x-goog-user-project" in headers
                                ):
                                    logger.warning("[Antigravity] 403 with project header, retrying without")
                                    headers.pop("x-goog-user-project", None)
                                    has_triggered_downgrade = True
                                    header_removed_for_403 = True
                                    break

                                if has_next and resp.status_code in _V1INTERNAL_ENDPOINT_FALLBACK_CODES:
                                    err_msg = f"Upstream {url} returned {resp.status_code}"
                                    logger.warning(f"[Antigravity] {err_msg}")
                                    last_err = err_msg
                                    continue

                                # Non-retryable error
                                error_text = await resp.aread()
                                try:
                                    error_json = json.loads(error_text.decode("utf-8", errors="replace"))
                                    error_detail = error_json.get("error", error_text.decode("utf-8", errors="replace"))
                                except (json.JSONDecodeError, ValueError):
                                    error_detail = error_text.decode("utf-8", errors="replace")

                                tool_count = 0
                                req_inner = wrapped.get("request") if isinstance(wrapped, dict) else {}
                                if isinstance(req_inner, dict):
                                    for tg in req_inner.get("tools") or []:
                                        if isinstance(tg, dict):
                                            tool_count += len(tg.get("functionDeclarations") or [])
                                logger.error(
                                    f"[Antigravity] Upstream error {resp.status_code}: {error_detail} "
                                    f"(model={model_name}, tools={tool_count}, "
                                    f"requestType={wrapped.get('requestType')}, "
                                    f"requestId={wrapped.get('requestId')})"
                                )
                                if os.environ.get("ANTIGRAVITY_DEBUG_REQUEST") == "1":
                                    logger.error(
                                        "[Antigravity] Debug envelope: %s",
                                        json.dumps(wrapped, ensure_ascii=False)[:12000],
                                    )
                                error_chunk = {
                                    "error": {
                                        "message": f"Google v1internal error: {error_detail}",
                                        "type": "api_error",
                                        "code": resp.status_code,
                                    }
                                }
                                yield f"data: {json.dumps(error_chunk)}\n\n".encode("utf-8")
                                yield b"data: [DONE]\n\n"
                                return

                        except httpx.RequestError as e:
                            err_msg = f"HTTP request failed at {url}: {e}"
                            logger.warning(f"[Antigravity] {err_msg}")
                            last_err = err_msg
                            if not has_next:
                                break
                            continue

                    if header_removed_for_403:
                        header_removed_for_403 = False
                        continue
                    else:
                        break

            # All endpoints failed
            logger.error(f"[Antigravity] All endpoints failed: {last_err}")
            error_chunk = {
                "error": {
                    "message": f"All Google v1internal endpoints failed: {last_err}",
                    "type": "api_error",
                    "code": 503,
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            antigravity_stream_generator(),
            media_type="text/event-stream",
        )

    else:
        # Non-streaming response
        status_code, resp_json, url = await call_v1internal(
            method="generateContent",
            access_token=access_token,
            body=wrapped,
            query_string=None,
            project_id=project_id,
            extra_headers=node_headers,
        )

        if status_code != 200:
            error_detail = resp_json.get("error", str(resp_json))
            if isinstance(error_detail, dict):
                error_msg = error_detail.get("message", str(error_detail))
            else:
                error_msg = str(error_detail)
            logger.error(f"[Antigravity] Upstream error {status_code}: {error_msg}")
            raise HTTPException(
                status_code=status_code if status_code < 500 else 502,
                detail=f"Google v1internal error: {error_msg}",
            )

        openai_response = transform_google_to_openai(resp_json, model_name)
        return openai_response


# =============================================================================
# Model Discovery
# =============================================================================

async def discover_antigravity_models(
    oauth_tokens: Dict[str, Any],
    project_id: Optional[str],
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    """Discover models from Google v1internal fetchAvailableModels.

    Returns:
        Tuple of (success, models_list, error_message)
    """
    try:
        access_token = await ensure_fresh_token(
            oauth_tokens,
            client_id="",
            client_secret="",
        )
    except Exception as e:
        return False, [], f"Token refresh failed: {e}"

    status_code, resp_json, url = await call_v1internal(
        method="fetchAvailableModels",
        access_token=access_token,
        body={},
        query_string=None,
        project_id=project_id,
    )

    if status_code != 200:
        return False, [], f"fetchAvailableModels failed: HTTP {status_code} - {resp_json}"

    # Parse models from response
    models: List[Dict[str, Any]] = []
    data = resp_json.get("response", resp_json)

    # The response format is not fully documented; try common patterns
    model_list = data.get("models", []) or data.get("data", [])
    if not model_list and isinstance(data, list):
        model_list = data

    for model in model_list:
        if isinstance(model, str):
            models.append({
                "name": model,
                "size": None,
                "digest": None,
                "modified_at": None,
                "details": {},
                "family": None,
            })
        elif isinstance(model, dict):
            name = model.get("name") or model.get("id") or model.get("model")
            if name:
                models.append({
                    "name": name,
                    "size": None,
                    "digest": None,
                    "modified_at": None,
                    "details": model,
                    "family": None,
                })

    return True, models, None


# =============================================================================
# Health Check
# =============================================================================

async def health_check_antigravity(
    oauth_tokens: Dict[str, Any],
    timeout: float = 15.0,
) -> Tuple[bool, Optional[str]]:
    """Check if an antigravity node is healthy by verifying token validity.

    Returns:
        Tuple of (is_healthy, error_message)
    """
    try:
        access_token = await ensure_fresh_token(
            oauth_tokens,
            client_id="",
            client_secret="",
        )
    except Exception as e:
        return False, f"Token refresh failed: {e}"

    # Verify token works with a lightweight Google API call
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            http2=True,
        ) as client:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=timeout,
            )

            if response.status_code == 200:
                return True, None
            else:
                return False, f"Token validation failed: HTTP {response.status_code}"

    except httpx.TimeoutException:
        return False, "Request timeout"
    except httpx.ConnectError as e:
        return False, f"Connection error: {str(e)}"
    except Exception as e:
        return False, str(e)
