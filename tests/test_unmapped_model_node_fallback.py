"""
Regression: routing an unrecognized/unsynced model name must never pick a
specialized-provider node (antigravity/bedrock/cursor). Those providers only
ever serve their own small, fixed model catalog via a dedicated proxy contract
(app/google_proxy.py, app/bedrock_proxy.py, app/cursor_proxy.py) — they can
never satisfy an arbitrary raw model string the way a generic ollama/vllm
passthrough node can. Guessing them as a fallback candidate is guaranteed to
fail (see resolve_antigravity_model_name's 404).

get_all_active_healthy_nodes() is the sole "unmapped model" fallback pool
(app/proxy.py's _select_node_url, "Falling back to all active nodes") — this
is the only place that needs the node_type filter.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.repositories.node_repository as nr
from app.node_manager import node_manager


def _fake_node(id, node_type, name=None):
    return SimpleNamespace(
        id=id, name=name or f"node-{id}", base_url=f"http://node-{id}:11434",
        api_key=None, node_type=node_type, priority=0, weight=100,
        health_status="healthy", headers=None, oauth_tokens=None,
        project_id=None, scoped_models=False,
    )


class TestUnmappedModelFallbackPool:
    def test_excludes_specialized_provider_nodes(self, monkeypatch):
        fake_nodes = [
            _fake_node(1, "ollama"),
            _fake_node(2, "vllm"),
            _fake_node(3, "antigravity"),
            _fake_node(4, "bedrock"),
            _fake_node(5, "cursor"),
        ]
        monkeypatch.setattr(nr.NodeRepository, "list_active", AsyncMock(return_value=fake_nodes))

        result = asyncio.run(node_manager.get_all_active_healthy_nodes())
        node_types = {n["node_type"] for n in result}

        assert node_types == {"ollama", "vllm"}, (
            f"specialized provider nodes leaked into the unmapped-model guess pool: {node_types}. "
            "These nodes can never serve an arbitrary model name (fixed catalog only) and will "
            "always 404/error, wasting a failover attempt or crashing the stream."
        )

    def test_generic_nodes_still_returned(self, monkeypatch):
        fake_nodes = [_fake_node(1, "ollama"), _fake_node(2, "vllm")]
        monkeypatch.setattr(nr.NodeRepository, "list_active", AsyncMock(return_value=fake_nodes))

        result = asyncio.run(node_manager.get_all_active_healthy_nodes())
        assert {n["node_id"] for n in result} == {1, 2}
