"""Pydantic models for the application"""

from typing import Optional, Any, Dict, List
from pydantic import BaseModel, Field
from datetime import datetime


class User(BaseModel):
    """User model"""
    username: str
    token: str
    created_at: str
    updated_at: Optional[str] = None


class UserInDB(BaseModel):
    """User database model"""
    users: List[User] = []


class TokenPayload(BaseModel):
    """JWT token payload"""
    username: str
    iat: int


class OllamaGenerateRequest(BaseModel):
    """Ollama generate request"""
    model: str
    prompt: str
    stream: Optional[bool] = False
    options: Optional[Dict[str, Any]] = None
    context: Optional[List[int]] = None
    template: Optional[str] = None
    system: Optional[str] = None
    raw: Optional[bool] = False


class OllamaChatRequest(BaseModel):
    """Ollama chat request"""
    model: str
    messages: List[Dict[str, Any]]
    stream: Optional[bool] = False
    options: Optional[Dict[str, Any]] = None
    template: Optional[str] = None
    format: Optional[str] = None
    keep_alive: Optional[str] = None


class OllamaEmbeddingsRequest(BaseModel):
    """Ollama embeddings request"""
    model: str
    prompt: str
    options: Optional[Dict[str, Any]] = None
    keep_alive: Optional[str] = None


class OllamaShowRequest(BaseModel):
    """Ollama show model request"""
    name: str


class OllamaCopyRequest(BaseModel):
    """Ollama copy model request"""
    source: str
    destination: str


class OllamaDeleteRequest(BaseModel):
    """Ollama delete model request"""
    name: str


class OllamaPullRequest(BaseModel):
    """Ollama pull model request"""
    name: str
    stream: Optional[bool] = False
    insecure: Optional[bool] = False


class OllamaPushRequest(BaseModel):
    """Ollama push model request"""
    name: str
    stream: Optional[bool] = False
    insecure: Optional[bool] = False


class OllamaCreateRequest(BaseModel):
    """Ollama create model request"""
    name: str
    modelfile: Optional[str] = None
    stream: Optional[bool] = False
    path: Optional[str] = None

