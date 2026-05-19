"""Tests for node model name alias lookup (Claude prefix stripping)."""

from app.repositories.node_repository import NodeModelRepository


def test_model_lookup_variants_includes_claude_prefix() -> None:
    variants = NodeModelRepository._model_lookup_variants("opus-4-6-thinking")
    assert "opus-4-6-thinking" in variants
    assert "claude-opus-4-6-thinking" in variants


def test_model_lookup_variants_strips_claude_prefix() -> None:
    variants = NodeModelRepository._model_lookup_variants("claude-opus-4-6-thinking")
    assert "claude-opus-4-6-thinking" in variants
    assert "opus-4-6-thinking" in variants
