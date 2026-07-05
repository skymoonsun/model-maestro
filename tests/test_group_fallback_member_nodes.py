"""
Regression: when a model-group fallback switches to another member, that
member's OWN preferred nodes must be used for node selection.

Root cause: resolve_model_with_metadata() explicitly documents "non-group
model_name is returned unchanged with no preferred nodes" (app/config.py). A
fallback member's display name (e.g. "z-ai/glm-5.2") is NOT itself a group
name, so re-running _resolve_model_groups() on it yields no preferred nodes,
and allowed_node_ids ends up None. With no restriction, _select_node_url falls
back to whatever _gather_nodes_for_model_candidates finds via
``routing_catalog_names`` — a union of EVERY group member's catalog aliases
(app/config.py get_member_catalog_names), not just this member's. Nodes that
only host some OTHER member (e.g. an Antigravity node hosting a Claude member)
get pulled into the pool and tried for a model they never had — see the
"[Antigravity] 404 ... trying next node" / NVIDIA vLLM misroute seen in
production logs.

Fix: after picking the fallback member, look up its own preferred_node_ids /
node_priority_overrides (same helpers resolve_model_with_metadata already uses
for the initial resolution) and inject them into current_data so the existing
_prepare_routing_allowed() picks them up — restoring the same per-member node
restriction the group's FIRST member enjoys.
"""

import asyncio
from types import SimpleNamespace

from app.proxy import OllamaProxy


def _fake_member(id, display_name, preferred_ids=None):
    nodes = [SimpleNamespace(id=nid) for nid in (preferred_ids or [])]
    return SimpleNamespace(id=id, model_display_name=display_name, preferred_nodes=nodes)


class TestGroupFallbackUsesMemberOwnPreferredNodes:
    def _run_fallback(self, monkeypatch, fallback_member):
        p = OllamaProxy()
        captured = {}

        async def fake_select(model_name, **kw):
            captured.update(kw)
            return ("http://fallback-node:11434", None, "ollama", None, False)

        async def fake_resolve(d):
            # Real behavior for a non-group real model name: returned unchanged,
            # no _preferred_node_ids injected (see resolve_model_with_metadata docstring).
            return dict(d)

        async def fake_rebind(d, snap, url):
            return d

        monkeypatch.setattr(p, "_select_node_url", fake_select)
        monkeypatch.setattr(p, "_resolve_model_groups", fake_resolve)
        monkeypatch.setattr(p, "_rebind_body_to_node", fake_rebind)
        monkeypatch.setattr(p, "_should_model_group_failover", lambda *a: True)
        monkeypatch.setattr(p, "_get_413_fallback_model", lambda g, f: None)
        monkeypatch.setattr(p, "_get_fallback_model", lambda g, f, t: "z-ai/glm-5.2")

        import app.proxy as proxy_module
        monkeypatch.setattr(
            proxy_module.model_group_manager,
            "get_member_by_display_name",
            lambda group, name: fallback_member if name == "z-ai/glm-5.2" else None,
        )

        state = asyncio.run(p._apply_model_group_fallback(
            original_group="kilo-code",
            failed_for_group="glm-5.2:latest",
            tried_models={"glm-5.2:latest"},
            original_data={"model": "glm-5.2:latest"},
            current_data={"model": "glm-5.2:latest", "_preferred_node_ids": [3, 1]},
            rsnap={},
            endpoint="/v1/chat/completions",
            exclude_scoped=False,
            bypass_node_access=True,
            username=None,
            tried_nodes=set(),
            routing_catalog_names=["glm-5.2:latest", "z-ai/glm-5.2", "claude-opus-4-6-thinking"],
            status_code=404,
            attempt=0,
        ))
        return captured, state

    def test_fallback_member_own_nodes_restrict_selection(self, monkeypatch):
        member = _fake_member(id=7, display_name="z-ai/glm-5.2", preferred_ids=[2, 5])
        captured, state = self._run_fallback(monkeypatch, member)

        assert captured.get("allowed_node_ids") == [2, 5], (
            f"expected the fallback member's own preferred nodes [2, 5], got "
            f"{captured.get('allowed_node_ids')!r}. Without this, _select_node_url has no "
            "restriction and widens to routing_catalog_names' whole-group union pool, "
            "which can include unrelated nodes (e.g. Antigravity hosting a different member)."
        )

    def test_member_without_preferred_nodes_leaves_unrestricted(self, monkeypatch):
        # A member with no explicit preferred_nodes config: allowed_node_ids should
        # be None (not crash, not fabricate a restriction) — falls back to prior behavior.
        member = _fake_member(id=8, display_name="z-ai/glm-5.2", preferred_ids=[])
        captured, _ = self._run_fallback(monkeypatch, member)
        assert not captured.get("allowed_node_ids")
