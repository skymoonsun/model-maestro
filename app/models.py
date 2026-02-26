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


# Admin API Models

class CreateUserRequest(BaseModel):
    """Create user request"""
    username: str


class AssignModelsRequest(BaseModel):
    """Assign models to user request"""
    models: List[str]


class CreateMappingRequest(BaseModel):
    """Create or update model mapping request"""
    display_name: str
    real_name: str
    context_length: Optional[str] = None  # Human-friendly format: "198K", "128K", "1M", "32768"


class UserResponse(BaseModel):
    """User response"""
    username: str
    token: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_active: bool = True


class UserWithModelsResponse(BaseModel):
    """User with models response"""
    username: str
    token: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_active: bool = True
    has_all_models: bool = False
    models: List[str] = []


class UserModelsResponse(BaseModel):
    """User models response"""
    username: str
    has_all_models: bool
    models: List[str]


class ModelMappingResponse(BaseModel):
    """Model mapping response"""
    display_name: str
    real_name: str
    context_length: Optional[int] = None  # Token cinsinden (e.g., 202752)
    context_length_display: Optional[str] = None  # İnsan-dostu format (e.g., "198K")
    created_at: Optional[str] = None

class SetUserLimitRequest(BaseModel):
    """Set user limit request"""
    request_limit: Optional[int] = None
    token_limit: Optional[int] = None

class UserLimitResponse(BaseModel):
    """User limit response"""
    username: str
    request_limit: Optional[int] = None
    token_limit: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

