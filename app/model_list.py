"""Merge model groups into public model-list API responses."""

from typing import Any, Dict, List, Optional

from app.config import get_context_length_for_model, model_group_manager


def _user_may_use_group(
    group_name: str,
    member_names: List[str],
    user_models_data: Dict[str, Any],
) -> bool:
    if user_models_data.get("has_all_models"):
        return True
    allowed = set(user_models_data.get("models") or [])
    if group_name in allowed:
        return True
    return any(m in allowed for m in member_names)


async def get_visible_catalog_group_names(
    user_models_data: Optional[Dict[str, Any]],
) -> List[str]:
    """
    Model group names that should appear in /api/tags, /v1/models, etc.

    Requires group active, list_in_catalog enabled, at least one active member,
    and user access to the group name or any member display name.
    """
    if not user_models_data:
        return []

    await model_group_manager.ensure_loaded()
    visible: List[str] = []

    for group_name, data in model_group_manager._groups.items():
        group = data["group"]
        if not group.is_active or not getattr(group, "list_in_catalog", False):
            continue
        members = data.get("members") or []
        member_names = [m.model_display_name for m in members if m.is_active]
        if not member_names:
            continue
        if _user_may_use_group(group_name, member_names, user_models_data):
            visible.append(group_name)

    return sorted(visible)


def append_groups_to_ollama_models(
    models: List[Dict[str, Any]],
    group_names: List[str],
) -> List[Dict[str, Any]]:
    """Add synthetic Ollama-style entries for model groups."""
    existing = {
        m.get("name") or m.get("model")
        for m in models
        if isinstance(m, dict)
    }
    for name in group_names:
        if name in existing:
            continue
        ctx = get_context_length_for_model(name)
        entry: Dict[str, Any] = {
            "name": name,
            "model": name,
            "modified_at": "",
            "size": 0,
            "digest": "",
            "details": {
                "family": "model_group",
                "families": ["model_group"],
                "parameter_size": "group",
                "quantization_level": "",
            },
        }
        if ctx:
            entry["context_length"] = ctx
        models.append(entry)
    return models


def append_groups_to_openai_models(
    models: List[Dict[str, Any]],
    group_names: List[str],
) -> List[Dict[str, Any]]:
    """Add synthetic OpenAI-style entries for model groups."""
    existing = {m.get("id") for m in models if isinstance(m, dict)}
    for name in group_names:
        if name in existing:
            continue
        entry: Dict[str, Any] = {
            "id": name,
            "object": "model",
            "created": 0,
            "owned_by": "model-maestro",
        }
        ctx = get_context_length_for_model(name)
        if ctx:
            entry["max_model_len"] = ctx
        models.append(entry)
    return models
