"""OpenAI API compatible adapter for Cursor and other OpenAI clients"""

from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field


class OpenAIMessage(BaseModel):
    """OpenAI chat message format"""
    role: str
    content: str


class OpenAIChatRequest(BaseModel):
    """OpenAI chat completion request"""
    model: str
    messages: List[OpenAIMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None


class OpenAIChatChoice(BaseModel):
    """OpenAI chat completion choice"""
    index: int = 0
    message: OpenAIMessage
    finish_reason: Optional[str] = "stop"


class OpenAIChatResponse(BaseModel):
    """OpenAI chat completion response"""
    id: str = "chatcmpl-proxy"
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChatChoice]
    usage: Optional[Dict[str, int]] = None


class OpenAIStreamChoice(BaseModel):
    """OpenAI streaming choice"""
    index: int = 0
    delta: Dict[str, Any]
    finish_reason: Optional[str] = None


class OpenAIStreamResponse(BaseModel):
    """OpenAI streaming response"""
    id: str = "chatcmpl-proxy"
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[OpenAIStreamChoice]


def ollama_to_openai_message(ollama_msg: Dict[str, Any]) -> OpenAIMessage:
    """Convert Ollama message format to OpenAI format"""
    return OpenAIMessage(
        role=ollama_msg.get("role", "assistant"),
        content=ollama_msg.get("content", "")
    )


def openai_to_ollama_messages(openai_messages: List[OpenAIMessage]) -> List[Dict[str, str]]:
    """Convert OpenAI messages to Ollama format"""
    return [
        {"role": msg.role, "content": msg.content}
        for msg in openai_messages
    ]


def ollama_to_openai_response(ollama_response: Dict[str, Any]) -> OpenAIChatResponse:
    """Convert Ollama response to OpenAI format"""
    import time
    
    message = ollama_response.get("message", {})
    
    return OpenAIChatResponse(
        id="chatcmpl-proxy",
        object="chat.completion",
        created=int(time.time()),
        model=ollama_response.get("model", "unknown"),
        choices=[
            OpenAIChatChoice(
                index=0,
                message=OpenAIMessage(
                    role=message.get("role", "assistant"),
                    content=message.get("content", "")
                ),
                finish_reason="stop"
            )
        ],
        usage={
            "prompt_tokens": ollama_response.get("prompt_eval_count", 0),
            "completion_tokens": ollama_response.get("eval_count", 0),
            "total_tokens": ollama_response.get("prompt_eval_count", 0) + ollama_response.get("eval_count", 0)
        }
    )


def ollama_stream_to_openai_stream(ollama_chunk: Dict[str, Any]) -> Optional[OpenAIStreamResponse]:
    """Convert Ollama streaming chunk to OpenAI format"""
    import time
    
    message = ollama_chunk.get("message", {})
    content = message.get("content", "")
    done = ollama_chunk.get("done", False)
    
    # Skip empty content chunks
    if not content and not done:
        return None
    
    delta = {}
    finish_reason = None
    
    if content:
        delta["content"] = content
    
    if done:
        finish_reason = "stop"
    
    return OpenAIStreamResponse(
        id="chatcmpl-proxy",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=ollama_chunk.get("model", "unknown"),
        choices=[
            OpenAIStreamChoice(
                index=0,
                delta=delta,
                finish_reason=finish_reason
            )
        ]
    )

