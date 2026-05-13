"""Tests for node-scoped outbound model mapping."""

from app.config import ModelMappingManager


def test_mapping_without_node_restriction_applies_globally() -> None:
    m = ModelMappingManager()
    m._mappings = {"serve:latest": "serve:cloud"}
    m._mapping_node_ids = {}

    assert m.get_real_model_name_for_node("serve", None) == "serve:cloud"
    assert m.get_real_model_name_for_node("serve", 99) == "serve:cloud"


def test_mapping_with_node_restriction_only_on_allowed_nodes() -> None:
    m = ModelMappingManager()
    m._mappings = {"serve:latest": "serve:cloud"}
    m._mapping_node_ids = {"serve:latest": [1, 2]}

    assert m.get_real_model_name_for_node("serve", None) == "serve"
    assert m.get_real_model_name_for_node("serve", 99) == "serve"
    assert m.get_real_model_name_for_node("serve", 1) == "serve:cloud"


def test_explicit_empty_nodes_list_behaves_as_global() -> None:
    m = ModelMappingManager()
    m._mappings = {"serve:latest": "serve:cloud"}
    m._mapping_node_ids = {"serve:latest": []}

    assert m.get_real_model_name_for_node("serve", None) == "serve:cloud"
