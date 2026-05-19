"""JSON Schema sanitization for Gemini v1internal functionDeclarations.

Antigravity-Manager expands $ref/$defs and simplifies unions before sending tools.
Stripping $ref without inlining breaks Claude Code tool definitions (25+ tools).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Set

_MAX_DEPTH = 32

_ALLOWED_SCHEMA_FIELDS = frozenset({
    "type",
    "description",
    "properties",
    "required",
    "items",
    "enum",
    "title",
})


def _collect_all_defs(value: Any, defs: Dict[str, Any]) -> None:
    if isinstance(value, dict):
        for defs_key in ("$defs", "definitions"):
            nested = value.get(defs_key)
            if isinstance(nested, dict):
                for name, schema in nested.items():
                    defs.setdefault(name, schema)
        for key, child in value.items():
            if key not in ("$defs", "definitions"):
                _collect_all_defs(child, defs)
    elif isinstance(value, list):
        for item in value:
            _collect_all_defs(item, defs)


def _flatten_refs(obj: Dict[str, Any], defs: Dict[str, Any], depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        return

    ref_path = obj.pop("$ref", None)
    if isinstance(ref_path, str):
        ref_name = ref_path.split("/")[-1]
        target = defs.get(ref_name)
        if isinstance(target, dict):
            for key, val in target.items():
                obj.setdefault(key, copy.deepcopy(val))
            _flatten_refs(obj, defs, depth + 1)
        else:
            obj.setdefault("type", "string")
            hint = f"(Unresolved $ref: {ref_path})"
            desc = obj.get("description", "")
            if isinstance(desc, str) and hint not in desc:
                obj["description"] = f"{desc} {hint}".strip()

    for key, val in list(obj.items()):
        if isinstance(val, dict):
            _flatten_refs(val, defs, depth + 1)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    _flatten_refs(item, defs, depth + 1)


def _normalize_type_field(obj: Dict[str, Any]) -> bool:
    """Return True if schema is effectively nullable."""
    type_val = obj.get("type")
    nullable = False
    if isinstance(type_val, list):
        non_null = [t for t in type_val if t != "null"]
        nullable = len(non_null) < len(type_val)
        if non_null:
            obj["type"] = non_null[0]
        else:
            obj["type"] = "string"
            nullable = True
    elif type_val == "null":
        obj["type"] = "string"
        nullable = True
    return nullable


def _pick_union_branch(branches: List[Any]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        b = copy.deepcopy(branch)
        _normalize_type_field(b)
        if b.get("type") == "null":
            continue
        candidates.append(b)
    if not candidates:
        return None
    for b in candidates:
        if str(b.get("type", "")).lower() == "object":
            return b
    return candidates[0]


def _apply_union_simplification(obj: Dict[str, Any]) -> None:
    for union_key in ("anyOf", "oneOf"):
        branches = obj.pop(union_key, None)
        if not isinstance(branches, list):
            continue
        chosen = _pick_union_branch(branches)
        if not chosen:
            continue
        for key, val in chosen.items():
            if key not in obj:
                obj[key] = copy.deepcopy(val)


def _merge_simple_allof(obj: Dict[str, Any]) -> None:
    branches = obj.pop("allOf", None)
    if not isinstance(branches, list):
        return
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        for key, val in branch.items():
            if key == "properties" and isinstance(val, dict):
                props = obj.setdefault("properties", {})
                if isinstance(props, dict):
                    for pk, pv in val.items():
                        props.setdefault(pk, copy.deepcopy(pv))
            elif key not in obj:
                obj[key] = copy.deepcopy(val)


def _looks_like_schema(obj: Dict[str, Any]) -> bool:
    if "functionCall" in obj or "functionResponse" in obj:
        return False
    return any(k in obj for k in _ALLOWED_SCHEMA_FIELDS)


def _whitelist_schema_node(obj: Dict[str, Any]) -> None:
    if obj.get("type") in ("object", "OBJECT") and "properties" not in obj:
        obj["properties"] = {}
    props = obj.get("properties")
    if isinstance(props, dict):
        valid_keys = set(props.keys())
        required = obj.get("required")
        if isinstance(required, list):
            obj["required"] = [r for r in required if r in valid_keys]
            if not obj["required"]:
                obj.pop("required", None)
    keys_to_remove = [k for k in obj if k not in _ALLOWED_SCHEMA_FIELDS]
    for key in keys_to_remove:
        obj.pop(key, None)
    if "type" not in obj:
        if "enum" in obj:
            obj["type"] = "string"
        elif "properties" in obj:
            obj["type"] = "object"
        elif "items" in obj:
            obj["type"] = "array"


def _clean_schema_recursive(value: Any, is_schema_node: bool, depth: int = 0) -> bool:
    """Clean a schema node. Returns True if effectively nullable."""
    if depth > _MAX_DEPTH:
        return False

    if isinstance(value, list):
        nullable = False
        for item in value:
            if _clean_schema_recursive(item, is_schema_node, depth + 1):
                nullable = True
        return nullable

    if not isinstance(value, dict):
        return False

    _apply_union_simplification(value)
    _merge_simple_allof(value)
    nullable = _normalize_type_field(value)

    props = value.get("properties")
    if isinstance(props, dict):
        nullable_keys: Set[str] = set()
        for key, prop_schema in props.items():
            if _clean_schema_recursive(prop_schema, True, depth + 1):
                nullable_keys.add(key)
        if not value.get("type"):
            value["type"] = "object"
        required = value.get("required")
        if isinstance(required, list) and nullable_keys:
            value["required"] = [r for r in required if r not in nullable_keys]
            if not value["required"]:
                value.pop("required", None)

    items = value.get("items")
    if items is not None:
        _clean_schema_recursive(items, True, depth + 1)
        if not value.get("type"):
            value["type"] = "array"

    if "const" in value and "enum" not in value:
        value["enum"] = [value.pop("const")]

    if _looks_like_schema(value):
        _whitelist_schema_node(value)

    return nullable


def _uppercase_types(value: Any) -> Any:
    if isinstance(value, list):
        return [_uppercase_types(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: Dict[str, Any] = {}
    for key, val in value.items():
        if key == "type" and isinstance(val, str):
            out[key] = val.upper()
        elif isinstance(val, dict):
            out[key] = _uppercase_types(val)
        elif isinstance(val, list):
            out[key] = [_uppercase_types(item) for item in val]
        else:
            out[key] = val
    if "properties" in out and "type" not in out:
        out["type"] = "OBJECT"
    return out


def clean_json_schema_for_gemini(schema: Any) -> Dict[str, Any]:
    """Full schema pipeline: inline refs, simplify unions, Gemini-safe whitelist."""
    if not isinstance(schema, dict) or not schema:
        return {
            "type": "OBJECT",
            "properties": {
                "content": {
                    "type": "STRING",
                    "description": "The raw content or arguments for the tool",
                }
            },
            "required": ["content"],
        }

    root = copy.deepcopy(schema)
    defs: Dict[str, Any] = {}
    _collect_all_defs(root, defs)
    root.pop("$defs", None)
    root.pop("definitions", None)
    _flatten_refs(root, defs, 0)
    _clean_schema_recursive(root, True, 0)
    finalized = _uppercase_types(root)
    if isinstance(finalized, dict):
        return finalized
    return {"type": "OBJECT", "properties": {}}
