"""Tests for Claude OpenAI SSE -> Anthropic SSE tool streaming helpers."""

from app.claude import (
    _merge_streaming_tool_call_deltas,
    _tool_stream_argument_delta_events,
    _tool_stream_start_events,
    _tool_stream_stop_event,
)


def test_merge_streaming_tool_call_deltas_accumulates_arguments() -> None:
    acc: dict = {}
    _merge_streaming_tool_call_deltas(
        acc,
        [
            {
                "index": 0,
                "id": "tu_abc",
                "function": {"name": "Read", "arguments": '{"path":'},
            }
        ],
    )
    _merge_streaming_tool_call_deltas(
        acc,
        [{"index": 0, "function": {"arguments": ' "/tmp"}'}}],
    )
    assert acc[0]["id"] == "tu_abc"
    assert acc[0]["name"] == "Read"
    assert acc[0]["arguments"] == '{"path": "/tmp"}'


def test_tool_stream_events_lifecycle() -> None:
    state = {
        "id": "tu_abc",
        "name": "Bash",
        "arguments": '{"command":"ls"}',
        "arguments_emitted_len": 0,
        "block_index": None,
        "started": False,
        "stopped": False,
    }
    start_events = _tool_stream_start_events(state, block_index=2)
    assert len(start_events) == 1
    assert state["started"] is True
    assert state["block_index"] == 2

    delta_events = _tool_stream_argument_delta_events(state)
    assert len(delta_events) == 1
    assert state["arguments_emitted_len"] == len(state["arguments"])

    delta_events_again = _tool_stream_argument_delta_events(state)
    assert delta_events_again == []

    stop = _tool_stream_stop_event(state)
    assert stop is not None
    assert state["stopped"] is True
