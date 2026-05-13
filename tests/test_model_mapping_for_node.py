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


def test_junction_row_display_without_latest_still_restricts_tagless_client_name() -> None:
    """DB display_name may omit :latest while _mappings resolves tagless names via :latest."""
    m = ModelMappingManager()
    m._mappings = {"deepseek-v4-pro:latest": "deepseek-v4-pro:cloud"}
    m._mapping_node_ids = {"deepseek-v4-pro": [42]}

    assert m.get_real_model_name_for_node("deepseek-v4-pro", 13) == "deepseek-v4-pro"
    assert m.get_real_model_name_for_node("deepseek-v4-pro", 42) == "deepseek-v4-pro:cloud"
    assert m.get_restricted_node_ids("deepseek-v4-pro") == [42]


def test_junction_only_under_latest_key_also_visible_for_tagless_name() -> None:
    m = ModelMappingManager()
    m._mappings = {"deepseek-v4-pro:latest": "deepseek-v4-pro:cloud"}
    m._mapping_node_ids = {"deepseek-v4-pro:latest": [7]}

    assert m.get_restricted_node_ids("deepseek-v4-pro") == [7]
