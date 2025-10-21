"""Main FastAPI application - Ollama Proxy with JWT Authentication"""

from typing import Any, Dict
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
import logging
import json
import time

from app.auth import get_current_user
from app.proxy import ollama_proxy
from app.config import get_settings
from app.models import (
    OllamaGenerateRequest,
    OllamaChatRequest,
    OllamaEmbeddingsRequest,
    OllamaShowRequest,
    OllamaCopyRequest,
    OllamaDeleteRequest,
    OllamaPullRequest,
    OllamaPushRequest,
    OllamaCreateRequest,
)
from app.openai_adapter import (
    OpenAIChatRequest,
    OpenAIChatResponse,
    openai_to_ollama_messages,
    ollama_to_openai_response,
    ollama_stream_to_openai_stream,
)

# Setup logging
settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Ollama Proxy API",
    description="JWT authenticated proxy for Ollama with cloud model mapping",
    version="1.0.0"
)

# Disable response buffering for streaming
@app.on_event("startup")
async def startup_event():
    logger.info("Starting Ollama Proxy API with streaming support")


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Ollama Proxy API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/api/generate")
async def generate(
    request: OllamaGenerateRequest,
    username: str = Depends(get_current_user)
):
    """
    Generate completion from a model
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting generate with model {request.model}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/generate",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/chat")
async def chat(
    request: OllamaChatRequest,
    username: str = Depends(get_current_user)
):
    """
    Generate chat completion
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting chat with model {request.model}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/chat",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/embeddings")
async def embeddings(
    request: OllamaEmbeddingsRequest,
    username: str = Depends(get_current_user)
):
    """
    Generate embeddings from a model
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting embeddings with model {request.model}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/embeddings",
        data=request.model_dump(exclude_none=True)
    )


@app.get("/api/tags")
async def list_models(
    username: str = Depends(get_current_user)
):
    """
    List available models
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting model list")
    
    return await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/api/tags"
    )


@app.post("/api/show")
async def show_model(
    request: OllamaShowRequest,
    username: str = Depends(get_current_user)
):
    """
    Show model information
    
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting show for model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/show",
        data=request.model_dump(exclude_none=True)
    )


@app.post("/api/copy")
async def copy_model(
    request: OllamaCopyRequest,
    username: str = Depends(get_current_user)
):
    """
    Copy a model
    
    Requires JWT authentication
    """
    logger.info(f"User {username} copying model from {request.source} to {request.destination}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/copy",
        data=request.model_dump(exclude_none=True)
    )


@app.delete("/api/delete")
async def delete_model(
    request: OllamaDeleteRequest,
    username: str = Depends(get_current_user)
):
    """
    Delete a model
    
    Requires JWT authentication
    """
    logger.info(f"User {username} deleting model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="DELETE",
        endpoint="/api/delete",
        data=request.model_dump(exclude_none=True)
    )


@app.post("/api/pull")
async def pull_model(
    request: OllamaPullRequest,
    username: str = Depends(get_current_user)
):
    """
    Pull a model from registry
    
    Requires JWT authentication
    """
    logger.info(f"User {username} pulling model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/pull",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/push")
async def push_model(
    request: OllamaPushRequest,
    username: str = Depends(get_current_user)
):
    """
    Push a model to registry
    
    Requires JWT authentication
    """
    logger.info(f"User {username} pushing model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/push",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


@app.post("/api/create")
async def create_model(
    request: OllamaCreateRequest,
    username: str = Depends(get_current_user)
):
    """
    Create a model from a Modelfile
    
    Requires JWT authentication
    """
    logger.info(f"User {username} creating model {request.name}")
    
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/api/create",
        data=request.model_dump(exclude_none=True),
        stream=request.stream or False
    )


# OpenAI Compatible Endpoints for Cursor
@app.post("/v1/chat/completions")
async def openai_chat_completions(
    request: OpenAIChatRequest,
    username: str = Depends(get_current_user)
):
    """
    OpenAI compatible chat completions endpoint
    
    This allows using the API with Cursor and other OpenAI-compatible clients
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting OpenAI chat completion with model {request.model}")
    
    # Convert OpenAI request to Ollama format
    ollama_request = {
        "model": request.model,
        "messages": openai_to_ollama_messages(request.messages),
        "stream": request.stream or False
    }
    
    # Add optional parameters if provided
    if request.temperature is not None:
        ollama_request.setdefault("options", {})["temperature"] = request.temperature
    if request.max_tokens is not None:
        ollama_request.setdefault("options", {})["num_predict"] = request.max_tokens
    if request.top_p is not None:
        ollama_request.setdefault("options", {})["top_p"] = request.top_p
    
    if request.stream:
        # Handle streaming response
        async def openai_stream_generator():
            ollama_response = await ollama_proxy.proxy_request(
                method="POST",
                endpoint="/api/chat",
                data=ollama_request,
                stream=True
            )
            
            # ollama_response is a StreamingResponse
            async for chunk in ollama_response.body_iterator:
                if not chunk:
                    continue
                try:
                    # Parse Ollama chunk
                    ollama_data = json.loads(chunk.decode('utf-8'))
                    
                    # Convert to OpenAI format
                    openai_chunk = ollama_stream_to_openai_stream(ollama_data)
                    if openai_chunk:
                        yield f"data: {openai_chunk.model_dump_json()}\n\n".encode('utf-8')
                    
                    # Send [DONE] at the end
                    if ollama_data.get("done", False):
                        yield b"data: [DONE]\n\n"
                except Exception as e:
                    logger.error(f"Error processing stream chunk: {e}")
                    continue
        
        return StreamingResponse(
            openai_stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        # Non-streaming response
        ollama_response = await ollama_proxy.proxy_request(
            method="POST",
            endpoint="/api/chat",
            data=ollama_request,
            stream=False
        )
        
        # Convert Ollama response to OpenAI format
        openai_response = ollama_to_openai_response(ollama_response)
        return openai_response


@app.get("/v1/models")
async def openai_list_models(
    username: str = Depends(get_current_user)
):
    """
    OpenAI compatible models list endpoint
    
    Returns available models in OpenAI format
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting OpenAI models list")
    
    # Get models from Ollama
    ollama_response = await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/api/tags"
    )
    
    # Convert to OpenAI format
    models_list = []
    for model in ollama_response.get("models", []):
        models_list.append({
            "id": model.get("name"),
            "object": "model",
            "created": int(time.time()),
            "owned_by": "ollama",
            "permission": [],
            "root": model.get("name"),
            "parent": None
        })
    
    return {
        "object": "list",
        "data": models_list
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

