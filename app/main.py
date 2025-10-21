"""Main FastAPI application - Ollama Proxy with JWT Authentication"""

from typing import Any, Dict
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
import logging
import json

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
# Directly proxy to Ollama's native OpenAI compatible endpoints
@app.post("/v1/chat/completions")
async def openai_chat_completions(
    request: Request,
    username: str = Depends(get_current_user)
):
    """
    OpenAI compatible chat completions endpoint
    
    Directly proxies to Ollama's native /v1/chat/completions endpoint
    Requires JWT authentication
    """
    # Get request body
    body = await request.body()
    request_data = json.loads(body.decode('utf-8'))
    
    logger.info(f"User {username} requesting OpenAI chat completion with model {request_data.get('model', 'unknown')}")
    
    # Proxy directly to Ollama's OpenAI compatible endpoint
    return await ollama_proxy.proxy_request(
        method="POST",
        endpoint="/v1/chat/completions",
        data=request_data,
        stream=request_data.get("stream", False)
    )


@app.get("/v1/models")
async def openai_list_models(
    username: str = Depends(get_current_user)
):
    """
    OpenAI compatible models list endpoint
    
    Directly proxies to Ollama's native /v1/models endpoint
    Requires JWT authentication
    """
    logger.info(f"User {username} requesting OpenAI models list")
    
    # Proxy directly to Ollama's OpenAI compatible endpoint
    return await ollama_proxy.proxy_request(
        method="GET",
        endpoint="/v1/models"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

