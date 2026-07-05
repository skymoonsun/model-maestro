"""
Regression: a model group's ``max_failover_retries`` must actually bound the
streaming/non-streaming failover loops (app/proxy.py), not just exist as an
unused DB column. Exercises the real model_group_manager.get_max_failover_retries
lookup end-to-end (group registered in ``_groups``), not just a mock.
"""

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from app.config import model_group_manager
from app.proxy import OllamaProxy, DEFAULT_MAX_FAILOVER_RETRIES


class TestGroupMaxFailoverRetriesBoundsStreamingLoop:
    def _run(self, monkeypatch, original_group):
        p = OllamaProxy()
        call_count = {"n": 0}

        async def always_retryable_failure(*a, **kw):
            call_count["n"] += 1
            raise HTTPException(status_code=404, detail="model not available on this node")

        node_counter = {"n": 0}

        async def always_new_node(*a, **kw):
            node_counter["n"] += 1
            return (f"http://node-{node_counter['n']}:11434", None, "antigravity", None, False)

        async def no_group_fallback(*a, **kw):
            return None

        monkeypatch.setattr(p, "_try_specialized_node_proxy", always_retryable_failure)
        monkeypatch.setattr(p, "_select_node_url", always_new_node)
        monkeypatch.setattr(p, "_apply_model_group_fallback", no_group_fallback)

        response = asyncio.run(p._stream_with_failover(
            url="https://antigravity.google/v1/chat/completions",
            data={"model": "z-ai/glm-5.2", "messages": [{"role": "user", "content": "hi"}]},
            is_openai_endpoint=True,
            username=None,
            original_group=original_group,
            tried_models={"z-ai/glm-5.2"},
            tried_nodes=set(),
            original_data={"model": "z-ai/glm-5.2"},
            endpoint="/v1/chat/completions",
            base_url="https://antigravity.google",
            api_key=None,
            start_time=0.0,
            node_type="antigravity",
            bypass_node_access=True,
        ))

        async def consume():
            async for _ in response.body_iterator:
                pass

        asyncio.run(consume())
        return call_count["n"]

    def test_no_group_uses_global_default(self, monkeypatch):
        attempts = self._run(monkeypatch, original_group=None)
        assert attempts == DEFAULT_MAX_FAILOVER_RETRIES + 1

    def test_group_override_shortens_loop(self, monkeypatch):
        group = SimpleNamespace(max_failover_retries=1)
        model_group_manager._groups["max-retry-test-group"] = {"group": group, "members": []}
        try:
            attempts = self._run(monkeypatch, original_group="max-retry-test-group")
        finally:
            model_group_manager._groups.pop("max-retry-test-group", None)

        assert attempts == 2, f"expected 1+1=2 attempts (max_failover_retries=1), got {attempts}"

    def test_group_with_no_override_uses_global_default(self, monkeypatch):
        group = SimpleNamespace(max_failover_retries=None)
        model_group_manager._groups["no-override-test-group"] = {"group": group, "members": []}
        try:
            attempts = self._run(monkeypatch, original_group="no-override-test-group")
        finally:
            model_group_manager._groups.pop("no-override-test-group", None)

        assert attempts == DEFAULT_MAX_FAILOVER_RETRIES + 1
