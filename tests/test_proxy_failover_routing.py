"""Regression tests for model-group fallback routing.

Bug 1: fallback paths passed the raw (ids, overrides) tuple from
_prepare_routing_allowed as ``allowed_node_ids``, crashing _select_node_url
with ``TypeError: unhashable type: 'list'`` (set() over a tuple containing a
list) and degrading node selection to the default URL.

Bug 2: clients (e.g. Kilo Code) send ``max_tokens: 0``; Ollama rejects it with
400 "max_tokens must be positive", triggering pointless node retries.
"""

import asyncio

from app.proxy import OllamaProxy


class TestFallbackRoutingState:
    def _run_fallback(self, monkeypatch):
        p = OllamaProxy()
        captured = {}

        async def fake_select(model_name, **kw):
            captured.update(kw)
            return ("http://fallback-node:11434", None, "ollama", None, False)

        async def fake_resolve(d):
            # Mirror the real resolver: the new group member injects its
            # preferred-node routing metadata into the request body.
            d = dict(d)
            d["_preferred_node_ids"] = [1, 3]
            return d

        async def fake_rebind(d, snap, url):
            return d

        monkeypatch.setattr(p, "_select_node_url", fake_select)
        monkeypatch.setattr(p, "_resolve_model_groups", fake_resolve)
        monkeypatch.setattr(p, "_rebind_body_to_node", fake_rebind)
        monkeypatch.setattr(p, "_should_model_group_failover", lambda *a: True)
        monkeypatch.setattr(p, "_get_413_fallback_model", lambda g, f: None)
        monkeypatch.setattr(p, "_get_fallback_model", lambda g, f, t: "fb-model")

        state = asyncio.run(p._apply_model_group_fallback(
            original_group="grp",
            failed_for_group="member-a",
            tried_models={"member-a"},
            original_data={"model": "member-a"},
            current_data={"model": "member-a", "_preferred_node_ids": [1, 3]},
            rsnap={},
            endpoint="/v1/chat/completions",
            exclude_scoped=False,
            bypass_node_access=True,
            username=None,
            tried_nodes=set(),
            routing_catalog_names=None,
            status_code=404,
            attempt=0,
        ))
        return captured, state

    def test_allowed_node_ids_is_list_not_tuple(self, monkeypatch):
        captured, _ = self._run_fallback(monkeypatch)
        allowed = captured.get("allowed_node_ids")
        assert allowed is None or isinstance(allowed, list), (
            f"allowed_node_ids must be a list of ids (got {type(allowed).__name__}: {allowed!r}); "
            "passing the raw (ids, overrides) tuple crashes _select_node_url"
        )
        assert allowed == [1, 3]

    def test_fallback_state_returns_unpacked_ids(self, monkeypatch):
        _, state = self._run_fallback(monkeypatch)
        assert state is not None
        returned_allowed = state[-1]
        assert returned_allowed is None or isinstance(returned_allowed, list), (
            "fallback_state must carry plain node-id list; the tuple poisons "
            "subsequent NODE RETRY selections in the streaming loop"
        )
        assert returned_allowed == [1, 3]


class TestMaxTokensNormalization:
    def test_zero_max_tokens_removed(self):
        data = {"model": "m", "max_tokens": 0, "messages": []}
        out = OllamaProxy._strip_nonpositive_token_limits(data)
        assert "max_tokens" not in out

    def test_negative_max_completion_tokens_removed(self):
        data = {"model": "m", "max_completion_tokens": -1}
        out = OllamaProxy._strip_nonpositive_token_limits(data)
        assert "max_completion_tokens" not in out

    def test_positive_values_kept(self):
        data = {"model": "m", "max_tokens": 4096}
        out = OllamaProxy._strip_nonpositive_token_limits(data)
        assert out["max_tokens"] == 4096

    def test_non_numeric_left_alone(self):
        data = {"model": "m", "max_tokens": None}
        out = OllamaProxy._strip_nonpositive_token_limits(data)
        assert "max_tokens" in out and out["max_tokens"] is None
