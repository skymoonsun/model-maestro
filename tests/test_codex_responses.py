import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

# Set environment before import
import os
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@localhost/test"

from app.main import app

def test_codex_responses_text_streaming():
    # Mock authentication dependencies
    from app.main import get_current_user, check_model_access, ollama_proxy

    app.dependency_overrides[get_current_user] = lambda: "test-user"

    async def mock_check_access(*args):
        return True

    # We will use patching for dependency helper functions in main
    with patch("app.main.check_model_access", side_effect=mock_check_access), \
         patch("app.main.get_context_length_for_model", return_value=4096), \
         patch("app.services.config_manager.is_model_in_maintenance", return_value=False), \
         patch("app.services.config_manager.get_model_unsupported_params", return_value=[]), \
         patch("app.services.config_manager.get_ollama_unsupported_params", return_value=[]):

        # Mock ollama_proxy.check_user_limits
        ollama_proxy.check_user_limits = AsyncMock(return_value=True)

        # Prepare OpenAI Chat Completion stream chunks
        openai_chunks = [
            b"data: " + json.dumps({
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 12345,
                "model": "kimi-k2.6:latest",
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None
                }]
            }).encode() + b"\n\n",
            b"data: " + json.dumps({
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 12345,
                "model": "kimi-k2.6:latest",
                "choices": [{
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None
                }]
            }).encode() + b"\n\n",
            b"data: " + json.dumps({
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 12345,
                "model": "kimi-k2.6:latest",
                "choices": [{
                    "index": 0,
                    "delta": {"content": " world!"},
                    "finish_reason": "stop"
                }]
            }).encode() + b"\n\n",
            b"data: [DONE]\n\n"
        ]

        async def mock_body_iterator():
            for chunk in openai_chunks:
                yield chunk
                await asyncio.sleep(0.001)

        # Mock the StreamingResponse returned by proxy_request
        mock_response = StreamingResponse(mock_body_iterator(), media_type="text/event-stream")
        ollama_proxy.proxy_request = AsyncMock(return_value=mock_response)

        client = TestClient(app)

        # Make the request to our converted endpoint
        headers = {"Authorization": "Bearer test-jwt-token"}
        payload = {
            "model": "kimi-k2.6:latest",
            "input": "test-prompt",
            "instructions": "You are a tester",
            "temperature": 0.7
        }

        response = client.post("/codex/responses", json=payload, headers=headers)
        assert response.status_code == 200

        # Parse the SSE output
        events = []
        for line in response.iter_lines():
            if line:
                events.append(line)

        # Ensure we have our custom events
        event_names = [e.split(":")[1].strip() for e in events if e.startswith("event:")]
        data_lines = [json.loads(e.split(":", 1)[1].strip()) for e in events if e.startswith("data:")]

        assert "response.created" in event_names
        assert "response.in_progress" in event_names
        assert "response.output_item.added" in event_names
        assert "response.content_part.added" in event_names
        assert "response.output_text.delta" in event_names
        assert "response.output_text.done" in event_names
        assert "response.content_part.done" in event_names
        assert "response.output_item.done" in event_names
        assert "response.completed" in event_names

        # Clean dependency overrides
        app.dependency_overrides.clear()


def test_codex_responses_reasoning_streaming():
    from app.main import get_current_user, check_model_access, ollama_proxy

    app.dependency_overrides[get_current_user] = lambda: "test-user"

    async def mock_check_access(*args):
        return True

    with patch("app.main.check_model_access", side_effect=mock_check_access), \
         patch("app.main.get_context_length_for_model", return_value=4096), \
         patch("app.services.config_manager.is_model_in_maintenance", return_value=False), \
         patch("app.services.config_manager.get_model_unsupported_params", return_value=[]), \
         patch("app.services.config_manager.get_ollama_unsupported_params", return_value=[]):

        ollama_proxy.check_user_limits = AsyncMock(return_value=True)

        # Prepare OpenAI Chat Completion stream chunks containing reasoning
        openai_chunks = [
            b"data: " + json.dumps({
                "id": "chatcmpl-2",
                "object": "chat.completion.chunk",
                "created": 12345,
                "model": "deepseek-reasoning",
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "reasoning_content": "Thinking process..."},
                    "finish_reason": None
                }]
            }).encode() + b"\n\n",
            b"data: " + json.dumps({
                "id": "chatcmpl-2",
                "object": "chat.completion.chunk",
                "created": 12345,
                "model": "deepseek-reasoning",
                "choices": [{
                    "index": 0,
                    "delta": {"content": "Final conclusion."},
                    "finish_reason": "stop"
                }]
            }).encode() + b"\n\n",
            b"data: [DONE]\n\n"
        ]

        async def mock_body_iterator():
            for chunk in openai_chunks:
                yield chunk
                await asyncio.sleep(0.001)

        mock_response = StreamingResponse(mock_body_iterator(), media_type="text/event-stream")
        ollama_proxy.proxy_request = AsyncMock(return_value=mock_response)

        client = TestClient(app)

        headers = {"Authorization": "Bearer test-jwt-token"}
        payload = {
            "model": "deepseek-reasoning",
            "input": "test-prompt"
        }

        response = client.post("/codex/responses", json=payload, headers=headers)
        assert response.status_code == 200

        events = []
        for line in response.iter_lines():
            if line:
                events.append(line)

        event_names = [e.split(":")[1].strip() for e in events if e.startswith("event:")]
        data_lines = [json.loads(e.split(":", 1)[1].strip()) for e in events if e.startswith("data:")]

        # Ensure reasoning events were emitted correctly
        assert "response.output_item.added" in event_names
        # First one generated is for reasoning type output item
        reasoning_add_event = next(d for d in data_lines if d["type"] == "response.output_item.added")
        assert reasoning_add_event["item"]["type"] == "reasoning"

        assert "response.reasoning_summary_text.delta" in event_names
        reasoning_delta_event = next(d for d in data_lines if d["type"] == "response.reasoning_summary_text.delta")
        assert reasoning_delta_event["delta"] == "Thinking process..."

        assert "response.reasoning_summary_text.done" in event_names
        assert "response.output_item.done" in event_names

        # Second output item is the text message
        message_add_event = [d for d in data_lines if d["type"] == "response.output_item.added"][-1]
        assert message_add_event["item"]["type"] == "message"

        assert "response.output_text.delta" in event_names
        assert "response.completed" in event_names

        # Clean dependency overrides
        app.dependency_overrides.clear()


def test_codex_responses_tool_call_streaming():
    from app.main import get_current_user, check_model_access, ollama_proxy

    app.dependency_overrides[get_current_user] = lambda: "test-user"

    async def mock_check_access(*args):
        return True

    with patch("app.main.check_model_access", side_effect=mock_check_access), \
         patch("app.main.get_context_length_for_model", return_value=4096), \
         patch("app.services.config_manager.is_model_in_maintenance", return_value=False), \
         patch("app.services.config_manager.get_model_unsupported_params", return_value=[]), \
         patch("app.services.config_manager.get_ollama_unsupported_params", return_value=[]):

        ollama_proxy.check_user_limits = AsyncMock(return_value=True)

        # Prepare OpenAI Chat Completion stream chunks containing a tool call
        openai_chunks = [
            b"data: " + json.dumps({
                "id": "chatcmpl-3",
                "object": "chat.completion.chunk",
                "created": 12345,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": ""}
                        }]
                    },
                    "finish_reason": None
                }]
            }).encode() + b"\n\n",
            b"data: " + json.dumps({
                "id": "chatcmpl-3",
                "object": "chat.completion.chunk",
                "created": 12345,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "function": {"arguments": '{"location": "Tokyo"}'}
                        }]
                    },
                    "finish_reason": "tool_calls"
                }]
            }).encode() + b"\n\n",
            b"data: [DONE]\n\n"
        ]

        async def mock_body_iterator():
            for chunk in openai_chunks:
                yield chunk
                await asyncio.sleep(0.001)

        mock_response = StreamingResponse(mock_body_iterator(), media_type="text/event-stream")
        ollama_proxy.proxy_request = AsyncMock(return_value=mock_response)

        client = TestClient(app)

        headers = {"Authorization": "Bearer test-jwt-token"}
        payload = {
            "model": "gpt-4",
            "input": "How is the weather?",
            "tools": [{
                "name": "get_weather",
                "description": "Get weather info",
                "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}
            }]
        }

        response = client.post("/codex/responses", json=payload, headers=headers)
        assert response.status_code == 200

        events = []
        for line in response.iter_lines():
            if line:
                events.append(line)

        event_names = [e.split(":")[1].strip() for e in events if e.startswith("event:")]
        data_lines = [json.loads(e.split(":", 1)[1].strip()) for e in events if e.startswith("data:")]

        # Check tool call sequence
        assert "response.output_item.added" in event_names
        tool_add_event = next(d for d in data_lines if d["type"] == "response.output_item.added")
        assert tool_add_event["item"]["type"] == "function_call"

        assert "response.function_call_arguments.delta" in event_names
        assert "response.function_call_arguments.done" in event_names
        assert "response.output_item.done" in event_names
        assert "response.completed" in event_names

        # Clean dependency overrides
        app.dependency_overrides.clear()
