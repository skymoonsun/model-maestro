"""Unit tests for SystemPromptService matching / ordering / injection logic.

All core logic lives in sync static methods, so these run without DB/Redis.
apply_to_request is exercised via asyncio.run with a monkeypatched cache loader.
"""

import asyncio
import pytest

from app.services.system_prompt_service import SystemPromptService as S


def _p(scope_type, scope_value, prompt, priority=0):
    return {"scope_type": scope_type, "scope_value": scope_value, "prompt": prompt, "priority": priority}


# --------------------------------------------------------------------- _match

class TestMatch:
    def test_matches_each_scope_type(self):
        prompts = [
            _p("mapping", "disp", "MAP"),
            _p("model", "real:7b", "MODEL"),
            _p("group", "g1", "GROUP"),
            _p("node", "nodeA", "NODE"),
        ]
        m = S._match(prompts, mapping="disp", model="real:7b", group="g1", node_keys={"nodeA"})
        assert {x["prompt"] for x in m} == {"MAP", "MODEL", "GROUP", "NODE"}

    def test_non_matching_excluded(self):
        prompts = [_p("mapping", "other", "X"), _p("node", "nope", "Y")]
        m = S._match(prompts, mapping="disp", model="real", group=None, node_keys={"nodeA"})
        assert m == []

    def test_node_matches_name_code_or_id(self):
        prompts = [_p("node", "42", "BY_ID"), _p("node", "trmix", "BY_CODE")]
        m = S._match(prompts, mapping=None, model=None, group=None, node_keys={"main", "trmix", "42"})
        assert {x["prompt"] for x in m} == {"BY_ID", "BY_CODE"}

    def test_none_context_does_not_match_empty(self):
        # A mapping prompt must not match when the request has no mapping value.
        prompts = [_p("mapping", "", "EMPTY"), _p("model", "real", "M")]
        m = S._match(prompts, mapping=None, model="real", group=None, node_keys=set())
        assert [x["prompt"] for x in m] == ["M"]

    def test_ordering_priority_then_scope_breadth(self):
        prompts = [
            _p("node", "n", "NODE", priority=0),
            _p("group", "g", "GROUP", priority=0),
            _p("model", "m", "MODEL", priority=0),
            _p("mapping", "d", "MAP", priority=5),  # highest priority -> first
        ]
        m = S._match(prompts, mapping="d", model="m", group="g", node_keys={"n"})
        assert [x["prompt"] for x in m] == ["MAP", "NODE", "GROUP", "MODEL"]

    def test_user_scope_matches_username(self):
        prompts = [_p("user", "alice", "USER_PROMPT"), _p("user", "bob", "OTHER")]
        m = S._match(prompts, mapping=None, model=None, group=None, node_keys=set(), user="alice")
        assert [x["prompt"] for x in m] == ["USER_PROMPT"]

    def test_user_scope_not_matched_without_username(self):
        prompts = [_p("user", "alice", "USER_PROMPT")]
        m = S._match(prompts, mapping=None, model=None, group=None, node_keys=set(), user=None)
        assert m == []

    def test_multiple_prompts_same_target_stack_by_priority(self):
        prompts = [
            _p("model", "real", "SECOND", priority=1),
            _p("model", "real", "FIRST", priority=2),
            _p("model", "real", "THIRD", priority=0),
        ]
        m = S._match(prompts, mapping=None, model="real", group=None, node_keys=set())
        assert [x["prompt"] for x in m] == ["FIRST", "SECOND", "THIRD"]

    def test_user_sits_at_top_of_hierarchy(self):
        prompts = [
            _p("mapping", "d", "MAP"),
            _p("node", "n", "NODE"),
            _p("user", "alice", "USER"),
            _p("group", "g", "GROUP"),
            _p("model", "m", "MODEL"),
        ]
        m = S._match(prompts, mapping="d", model="m", group="g", node_keys={"n"}, user="alice")
        assert [x["prompt"] for x in m] == ["USER", "NODE", "GROUP", "MODEL", "MAP"]


# ----------------------------------------------------------------- _build_block

class TestBuildBlock:
    def test_joins_with_blank_line(self):
        assert S._build_block([_p("node", "n", "A"), _p("group", "g", "B")]) == "A\n\nB"

    def test_skips_empty_and_whitespace(self):
        assert S._build_block([_p("node", "n", "  "), _p("group", "g", "B")]) == "B"

    def test_empty_when_no_prompts(self):
        assert S._build_block([]) == ""


# -------------------------------------------------------------- _merge_into_data

class TestMergeChat:
    def test_merge_with_existing_system(self):
        data = {"messages": [{"role": "system", "content": "USER"}, {"role": "user", "content": "hi"}]}
        out = S._merge_into_data(data, "INJ")
        assert out["messages"][0]["content"] == "INJ\n\nUSER"
        # original untouched (immutability)
        assert data["messages"][0]["content"] == "USER"

    def test_insert_when_no_system(self):
        data = {"messages": [{"role": "user", "content": "hi"}]}
        out = S._merge_into_data(data, "INJ")
        assert out["messages"][0] == {"role": "system", "content": "INJ"}
        assert out["messages"][1]["role"] == "user"

    def test_non_string_system_falls_back_to_leading_message(self):
        data = {"messages": [{"role": "system", "content": [{"type": "text", "text": "x"}]}]}
        out = S._merge_into_data(data, "INJ")
        assert out["messages"][0] == {"role": "system", "content": "INJ"}
        # the original multimodal system is preserved after it
        assert out["messages"][1]["content"] == [{"type": "text", "text": "x"}]

    def test_empty_block_is_noop(self):
        data = {"messages": [{"role": "user", "content": "hi"}]}
        assert S._merge_into_data(data, "") is data


class TestMergeGenerate:
    def test_merge_with_existing_system_field(self):
        data = {"prompt": "p", "system": "S"}
        assert S._merge_into_data(data, "INJ")["system"] == "INJ\n\nS"

    def test_set_when_no_system_field(self):
        data = {"prompt": "p"}
        assert S._merge_into_data(data, "INJ")["system"] == "INJ"


# ------------------------------------------------------------- apply_to_request

class TestApplyToRequest:
    def _run(self, coro):
        return asyncio.run(coro)

    def _service_with(self, prompts, monkeypatch):
        svc = S()

        async def fake_get_active_prompts():
            return prompts

        monkeypatch.setattr(svc, "get_active_prompts", fake_get_active_prompts)
        return svc

    def test_injects_matching_prompt(self, monkeypatch):
        svc = self._service_with([_p("model", "real:7b", "POLICY")], monkeypatch)
        data = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        out = self._run(svc.apply_to_request(data, model="real:7b"))
        assert out["messages"][0] == {"role": "system", "content": "POLICY"}

    def test_no_match_returns_unchanged(self, monkeypatch):
        svc = self._service_with([_p("model", "other", "X")], monkeypatch)
        data = {"messages": [{"role": "user", "content": "hi"}]}
        out = self._run(svc.apply_to_request(data, model="real:7b"))
        assert out is data

    def test_skips_non_text_requests(self, monkeypatch):
        svc = self._service_with([_p("model", "real", "X")], monkeypatch)
        data = {"input": "embed me"}  # no messages / prompt
        out = self._run(svc.apply_to_request(data, model="real"))
        assert out is data

    def test_user_prompt_injected_first(self, monkeypatch):
        svc = self._service_with(
            [_p("model", "real", "MODEL_POLICY"), _p("user", "alice", "USER_POLICY")],
            monkeypatch,
        )
        data = {"messages": [{"role": "user", "content": "hi"}]}
        out = self._run(svc.apply_to_request(data, model="real", user="alice"))
        assert out["messages"][0]["content"] == "USER_POLICY\n\nMODEL_POLICY"
