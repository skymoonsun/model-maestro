"""
System prompt service — transparent, admin-defined system prompt injection.

On each text-generation request the proxy calls ``apply_to_request`` with the
resolved request context (requesting username, mapping display name, real model
name, group name and selected node identity). Every active prompt whose scope
matches is collected, ordered (priority desc, then scope rank user → node →
group → model → mapping — user sits at the top of the hierarchy) and merged
into the request's system message.

Active prompts are cached in Redis (single key) and invalidated on admin edits,
so the hot path costs at most one Redis GET. Redis being unavailable degrades
gracefully to a direct DB read.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Ordering used as the tie-breaker when priorities are equal. `user` sits at the
# top of the hierarchy (applied first), then broad → specific for the rest.
_SCOPE_RANK = {"user": 0, "node": 1, "group": 2, "model": 3, "mapping": 4}


class SystemPromptService:
    """Loads, caches and injects admin-defined system prompts."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------ cache

    async def _load_from_db(self) -> List[Dict[str, Any]]:
        from app.database import async_session_maker
        from app.repositories.system_prompt_repository import SystemPromptRepository

        async with async_session_maker() as session:
            repo = SystemPromptRepository(session)
            rows = await repo.get_all_active()
            return [
                {
                    "scope_type": r.scope_type,
                    "scope_value": r.scope_value,
                    "prompt": r.prompt,
                    "priority": r.priority or 0,
                }
                for r in rows
            ]

    async def get_active_prompts(self) -> List[Dict[str, Any]]:
        """Return active prompts from Redis, falling back to DB on miss/outage."""
        from app.redis import redis_manager, CACHE_KEYS, CACHE_TTL

        key = CACHE_KEYS["SYSTEM_PROMPTS"]
        cached = None
        if redis_manager:
            cached = await redis_manager.get(key)  # None on miss / disconnect
        if cached is not None:
            return cached

        prompts = await self._load_from_db()
        if redis_manager:
            await redis_manager.set(key, prompts, expire=CACHE_TTL["SYSTEM_PROMPTS"])
        return prompts

    async def invalidate_cache(self) -> None:
        """Drop the Redis cache so the next request repopulates from DB."""
        from app.redis import redis_manager, CACHE_KEYS

        if redis_manager:
            await redis_manager.delete(CACHE_KEYS["SYSTEM_PROMPTS"])
        logger.info("[SystemPrompt] cache invalidated")

    # -------------------------------------------------------------- injection

    @staticmethod
    def _match(
        prompts: List[Dict[str, Any]],
        mapping: Optional[str],
        model: Optional[str],
        group: Optional[str],
        node_keys: set,
        user: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return prompts whose scope matches, ordered priority desc then rank."""
        matched: List[Dict[str, Any]] = []
        for p in prompts:
            st, sv = p.get("scope_type"), p.get("scope_value")
            if not sv:
                continue
            if st == "user" and user is not None and sv == user:
                matched.append(p)
            elif st == "mapping" and mapping is not None and sv == mapping:
                matched.append(p)
            elif st == "model" and model is not None and sv == model:
                matched.append(p)
            elif st == "group" and group is not None and sv == group:
                matched.append(p)
            elif st == "node" and sv in node_keys:
                matched.append(p)

        matched.sort(key=lambda p: (-(p.get("priority") or 0), _SCOPE_RANK.get(p.get("scope_type"), 99)))
        return matched

    @staticmethod
    def _build_block(matched: List[Dict[str, Any]]) -> str:
        parts = [p["prompt"].strip() for p in matched if (p.get("prompt") or "").strip()]
        return "\n\n".join(parts)

    @staticmethod
    def _merge_into_data(data: Dict[str, Any], block: str) -> Dict[str, Any]:
        """Merge the injected block into the request's system message (new dict)."""
        if not block:
            return data

        messages = data.get("messages")
        if isinstance(messages, list):
            new_messages = [dict(m) if isinstance(m, dict) else m for m in messages]
            for m in new_messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    content = m.get("content")
                    if isinstance(content, str):
                        # Admin policy goes first, user's own system content after.
                        m["content"] = f"{block}\n\n{content}" if content.strip() else block
                        return {**data, "messages": new_messages}
                    # Non-string (multimodal) system content -> safe fallback below.
                    break
            new_messages.insert(0, {"role": "system", "content": block})
            return {**data, "messages": new_messages}

        # Generate path (Ollama native `system` field).
        existing = data.get("system")
        if isinstance(existing, str) and existing.strip():
            return {**data, "system": f"{block}\n\n{existing}"}
        return {**data, "system": block}

    async def apply_to_request(
        self,
        data: Dict[str, Any],
        *,
        mapping: Optional[str] = None,
        model: Optional[str] = None,
        group: Optional[str] = None,
        node_name: Optional[str] = None,
        node_code: Optional[str] = None,
        node_id: Optional[Any] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Inject matching system prompts into ``data`` and return the (possibly new)
        request body. Returns ``data`` untouched when nothing matches.
        """
        if not isinstance(data, dict):
            return data

        has_messages = isinstance(data.get("messages"), list)
        # Only chat (messages) or generate (prompt-based) requests carry a system
        # prompt; anything else (embeddings, tags, ...) is skipped by the caller.
        if not has_messages and "prompt" not in data:
            return data

        try:
            prompts = await self.get_active_prompts()
        except Exception as e:
            logger.warning(f"[SystemPrompt] failed to load prompts, skipping injection: {e}")
            return data
        if not prompts:
            return data

        node_keys = {str(v) for v in (node_name, node_code, node_id) if v not in (None, "")}
        matched = self._match(prompts, mapping, model, group, node_keys, user=user)
        block = self._build_block(matched)
        if not block:
            return data

        logger.debug(
            f"[SystemPrompt] injecting {len(matched)} prompt(s) "
            f"(user={user}, mapping={mapping}, model={model}, group={group})"
        )
        return self._merge_into_data(data, block)


# Global singleton
system_prompt_service = SystemPromptService()
