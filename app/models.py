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
    capabilities: Optional[List[str]] = None  # ["completion", "tools", "thinking", "vision"]


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
    capabilities: Optional[List[str]] = None  # ["completion", "tools", "thinking", "vision"]
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


# =============================================================================
# FRONTEND PANEL - NEW MODELS
# =============================================================================

# --- System Config ---

class SystemConfigItem(BaseModel):
    """Single system config item"""
    key: str
    value: str
    description: Optional[str] = None
    updated_at: Optional[str] = None

class SystemConfigResponse(BaseModel):
    """Complete system config grouped by category"""
    background_tasks: Dict[str, Any] = {}
    http_client: Dict[str, Any] = {}
    defaults: Dict[str, Any] = {}
    ollama_unsupported_params: List[str] = []

class UpdateSystemConfigRequest(BaseModel):
    """Update system config (partial update)"""
    background_tasks: Optional[Dict[str, Any]] = None
    http_client: Optional[Dict[str, Any]] = None
    defaults: Optional[Dict[str, Any]] = None
    ollama_unsupported_params: Optional[List[str]] = None

# --- Model Config ---

class ModelConfigRequest(BaseModel):
    """Create/update model config"""
    model_prefix: str
    allowed_tools: Optional[List[str]] = None
    unsupported_params: Optional[List[str]] = None
    default_context_length: Optional[int] = 32768
    max_context_length: Optional[int] = None
    requests_per_minute: Optional[int] = None
    tokens_per_minute: Optional[int] = None
    is_active: bool = True
    maintenance_mode: bool = False
    description: Optional[str] = None
    cost_multiplier: Optional[float] = 1.0

class ModelConfigResponse(BaseModel):
    """Model config response"""
    id: int
    model_prefix: str
    allowed_tools: Optional[List[str]] = None
    unsupported_params: Optional[List[str]] = None
    default_context_length: Optional[int] = 32768
    max_context_length: Optional[int] = None
    requests_per_minute: Optional[int] = None
    tokens_per_minute: Optional[int] = None
    is_active: bool = True
    maintenance_mode: bool = False
    description: Optional[str] = None
    cost_multiplier: Optional[float] = 1.0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# --- Tool Sets ---

class ToolSetRequest(BaseModel):
    """Create/update tool set"""
    name: str
    tools: Optional[List[str]] = None  # None = full (all tools)
    description: Optional[str] = None

class ToolSetResponse(BaseModel):
    """Tool set response"""
    id: int
    name: str
    tools: Optional[List[str]] = None
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# --- Model Format Patterns ---

class ModelFormatPatternRequest(BaseModel):
    """Create/update model format pattern"""
    model_prefix: str
    format_type: str
    pattern_config: Dict[str, Any]
    is_active: bool = True

class ModelFormatPatternResponse(BaseModel):
    """Model format pattern response"""
    id: int
    model_prefix: str
    format_type: str
    pattern_config: Dict[str, Any]
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# --- Dashboard ---

class DashboardStatsResponse(BaseModel):
    """Dashboard statistics"""
    users: Dict[str, Any]
    requests: Dict[str, Any]
    tokens: Dict[str, Any]
    models: Dict[str, Any]
    system: Dict[str, Any]

class ChartDataResponse(BaseModel):
    """Chart data response"""
    labels: List[str]
    data: List[Any]
    period: Optional[str] = None

# --- Audit Log ---

class AuditLogResponse(BaseModel):
    """Audit log entry response"""
    id: int
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    admin_ip: Optional[str] = None
    created_at: Optional[str] = None


# --- Ollama Model Management ---

class ModelPullRequest(BaseModel):
    """Pull a model from Ollama registry"""
    name: str
    stream: bool = True

class OllamaModelListItem(BaseModel):
    """A model from Ollama /api/tags"""
    name: str
    model: Optional[str] = None
    size: Optional[int] = None
    digest: Optional[str] = None
    modified_at: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    # Enriched fields (from our DB)
    is_mapped: bool = False
    display_name: Optional[str] = None

class ModelShowResponse(BaseModel):
    """Detailed model info from Ollama /api/show"""
    name: str
    capabilities: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = None
    model_info: Optional[Dict[str, Any]] = None
    template: Optional[str] = None
    modified_at: Optional[str] = None

class SyncCapabilitiesResponse(BaseModel):
    """Response from capabilities sync"""
    synced: int
    failed: int
    results: List[Dict[str, Any]]
