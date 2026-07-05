"""
Regression: an HTTPException raised by a specialized-node proxy (Antigravity /
Bedrock / Cursor — e.g. "model not available on this node") must be treated as
a retryable node failure in the STREAMING failover path, exactly like it
already is in the non-streaming path (_non_streaming_with_failover wraps the
same call in try/except HTTPException, see app/proxy.py ~line 3958).

Before the fix, `stream_generator_with_failover`'s call to
`_try_specialized_node_proxy` was unguarded: the exception propagated straight
out of the async generator, crashing the StreamingResponse (unhandled
ExceptionGroup / ASGI 500) instead of failing over to the next node or ending
with a clean error payload.
"""

import asyncio

from fastapi import HTTPException

from app.proxy import OllamaProxy


class TestStreamSpecializedNodeFailover:
    def test_http_exception_does_not_crash_the_stream(self, monkeypatch):
        p = OllamaProxy()

        async def always_raises(*a, **kw):
            raise HTTPException(status_code=404, detail="model not available on this node")

        async def no_more_nodes(*a, **kw):
            return ("", None, "ollama", None, False)

        async def no_group_fallback(*a, **kw):
            return None

        monkeypatch.setattr(p, "_try_specialized_node_proxy", always_raises)
        monkeypatch.setattr(p, "_select_node_url", no_more_nodes)
        monkeypatch.setattr(p, "_apply_model_group_fallback", no_group_fallback)

        response = asyncio.run(p._stream_with_failover(
            url="https://antigravity.google/v1/chat/completions",
            data={"model": "z-ai/glm-5.2", "messages": [{"role": "user", "content": "hi"}]},
            is_openai_endpoint=True,
            username=None,
            original_group=None,
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
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            return chunks

        # Must complete without an unhandled exception escaping the generator.
        chunks = asyncio.run(consume())
        assert chunks, "expected a graceful error payload, got an empty/crashed stream"
        body = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
        assert b"model not available" in body or b"404" in body
