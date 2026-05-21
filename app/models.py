"""Pydantic models for the application"""

from typing import Optional, Any, Dict, List
from pydantic import BaseModel, model_validator


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

class OllamaEmbedRequest(BaseModel):
    """Ollama /api/embed request — generate embeddings for text(s)."""
    model: str
    input: str | List[str]
    truncate: Optional[bool] = True
    options: Optional[Dict[str, Any]] = None
    keep_alive: Optional[str] = None


class CompletionRequest(BaseModel):
    """OpenAI-compatible /v1/completions request"""
    model: str
    prompt: str | List[str]
    suffix: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stream: Optional[bool] = False
    logprobs: Optional[int] = None
    echo: Optional[bool] = False
    stop: Optional[str | List[str]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    best_of: Optional[int] = None
    user: Optional[str] = None


class OpenAIEmbeddingRequest(BaseModel):
    """OpenAI-compatible /v1/embeddings request"""
    input: str | List[str]
    model: str
    encoding_format: Optional[str] = "float"
    dimensions: Optional[int] = None
    user: Optional[str] = None


class EmbeddingData(BaseModel):
    """Single embedding result"""
    object: str = "embedding"
    embedding: List[float]
    index: int


class EmbeddingUsage(BaseModel):
    """Token usage for embedding request"""
    prompt_tokens: int
    total_tokens: int


class OpenAIEmbeddingResponse(BaseModel):
    """OpenAI-compatible /v1/embeddings response"""
    object: str = "list"
    data: List[EmbeddingData]
    model: str
    usage: EmbeddingUsage


class OllamaShowRequest(BaseModel):
    """Ollama show model request"""
    model: str
    verbose: Optional[bool] = None
    system: Optional[str] = None


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
    node_ids: Optional[List[int]] = None  # empty / omitted = global (all nodes)
    node_id: Optional[int] = None  # deprecated: use node_ids
    context_length: Optional[str] = None  # Human-friendly format: "198K", "128K", "1M", "32768"
    capabilities: Optional[List[str]] = None  # ["completion", "tools", "thinking", "vision"]

    @model_validator(mode="after")
    def merge_legacy_mapping_node(self) -> "CreateMappingRequest":
        if self.node_id is not None and not self.node_ids:
            object.__setattr__(self, "node_ids", [self.node_id])
        return self


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


class AssignNodeRequest(BaseModel):
    """Assign node to user request"""
    node_id: int


class AssignNodeModelRequest(BaseModel):
    """Assign node-model to user request"""
    node_id: int
    model_name: str


class UserNodesResponse(BaseModel):
    """User nodes response"""
    username: str
    has_restriction: bool
    nodes: List[dict]


class UserNodeModelsResponse(BaseModel):
    """User node-models response"""
    username: str
    has_restriction: bool
    node_models: List[dict]


class ModelMappingResponse(BaseModel):
    """Model mapping response"""
    display_name: str
    real_name: str
    node_ids: List[int] = []
    node_names: Optional[List[str]] = None
    node_types: Optional[List[str]] = None
    node_id: Optional[int] = None  # ilk node (geriye dönük uyumluluk)
    node_name: Optional[str] = None
    node_type: Optional[str] = None  # 'ollama' | 'vllm'
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
    search: Dict[str, Any] = {}
    tunnel: Dict[str, Any] = {}
    claude: Dict[str, Any] = {}
    ollama_unsupported_params: List[str] = []

class UpdateSystemConfigRequest(BaseModel):
    """Update system config (partial update)"""
    background_tasks: Optional[Dict[str, Any]] = None
    http_client: Optional[Dict[str, Any]] = None
    defaults: Optional[Dict[str, Any]] = None
    tunnel: Optional[Dict[str, Any]] = None
    search: Optional[Dict[str, Any]] = None
    claude: Optional[Dict[str, Any]] = None
    ollama_unsupported_params: Optional[List[str]] = None

# --- Model Config ---

class ModelConfigRequest(BaseModel):
    """Create/update model config"""
    model_prefix: str
    is_exact_match: bool = False
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
    is_exact_match: bool = False
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

# --- Request Log ---

class RequestLogItem(BaseModel):
    """Single request log entry"""
    id: int
    username: Optional[str] = None
    model_name: str
    request_type: str
    source: Optional[str] = None
    url_path: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    status_code: Optional[int] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None

class RequestLogResponse(BaseModel):
    """Paginated request log response"""
    logs: List[RequestLogItem]
    total: int
    limit: int
    offset: int

class UserStatsItem(BaseModel):
    """Per-user aggregated statistics"""
    username: str
    total_requests: int = 0
    total_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0

class UserStatsResponse(BaseModel):
    """All users aggregated statistics"""
    users: List[UserStatsItem]
    total_users: int

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
    is_available: bool = True
    # Which nodes have this model
    nodes: Optional[List[str]] = None
    context_length: Optional[int] = None
    capabilities: Optional[List[str]] = None

class VllmModelListItem(BaseModel):
    """A model from a vLLM node"""
    name: str
    node_name: str
    node_id: int
    base_url: str
    model_size: Optional[int] = None
    model_family: Optional[str] = None
    digest: Optional[str] = None
    modified_at: Optional[str] = None
    is_mapped: bool = False
    display_name: Optional[str] = None
    is_available: bool = True
    context_length: Optional[int] = None
    capabilities: Optional[List[str]] = None
    max_model_len: Optional[int] = None


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


class SyncVllmMetaResponse(BaseModel):
    """Response from vLLM metadata sync"""
    synced: int
    failed: int
    results: List[Dict[str, Any]]


# =============================================================================
# LOAD BALANCING - NODE MANAGEMENT
# =============================================================================

class OAuthTokens(BaseModel):
    """Google OAuth tokens for antigravity node"""
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    token_type: Optional[str] = "Bearer"
    scope: Optional[str] = None
    obtained_at: Optional[str] = None


class OllamaNodeCreate(BaseModel):
    """Create a new Ollama node"""
    name: str
    base_url: str
    api_key: Optional[str] = None
    priority: Optional[int] = 0
    weight: Optional[int] = 100
    is_active: Optional[bool] = True
    node_type: Optional[str] = "ollama"
    warmup_enabled: Optional[bool] = True
    auto_sync_enabled: Optional[bool] = True
    health_check_url: Optional[str] = None
    code: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    oauth_tokens: Optional[OAuthTokens] = None
    project_id: Optional[str] = None
    aws_secret_key: Optional[str] = None
    aws_region: Optional[str] = None
    aws_session_token: Optional[str] = None
    bedrock_auth_mode: Optional[str] = None  # 'iam' | 'api_key'
    scoped_models: Optional[bool] = False
    auto_cookie_refresh: Optional[bool] = False


class OllamaNodeUpdate(BaseModel):
    """Update an Ollama node"""
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    priority: Optional[int] = None
    weight: Optional[int] = None
    is_active: Optional[bool] = None
    node_type: Optional[str] = None
    warmup_enabled: Optional[bool] = None
    auto_sync_enabled: Optional[bool] = None
    health_check_url: Optional[str] = None
    code: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    oauth_tokens: Optional[OAuthTokens] = None
    project_id: Optional[str] = None
    aws_secret_key: Optional[str] = None
    aws_region: Optional[str] = None
    aws_session_token: Optional[str] = None
    bedrock_auth_mode: Optional[str] = None
    scoped_models: Optional[bool] = False
    auto_cookie_refresh: Optional[bool] = False


class OllamaNodeResponse(BaseModel):
    """Ollama node response"""
    id: int
    name: str
    base_url: str
    api_key: Optional[str] = None
    api_key_set: bool = False
    priority: int = 0
    weight: int = 100
    is_active: bool = True
    node_type: str = "ollama"
    warmup_enabled: bool = True
    auto_sync_enabled: bool = True
    code: Optional[str] = None
    health_status: str = "unknown"
    last_health_check: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    oauth_tokens: Optional[OAuthTokens] = None
    project_id: Optional[str] = None
    aws_secret_key: Optional[str] = None
    aws_region: Optional[str] = None
    aws_session_token: Optional[str] = None
    bedrock_auth_mode: Optional[str] = None
    scoped_models: bool = False
    auto_cookie_refresh: bool = False


class NodeModelInfo(BaseModel):
    """Model info for a node"""
    model_name: str
    model_size: Optional[int] = None
    model_family: Optional[str] = None
    is_available: bool = True


class OllamaNodeDetailResponse(BaseModel):
    """Detailed Ollama node response with models"""
    id: int
    name: str
    base_url: str
    api_key: Optional[str] = None
    api_key_set: bool = False
    priority: int = 0
    weight: int = 100
    is_active: bool = True
    node_type: str = "ollama"
    warmup_enabled: bool = True
    auto_sync_enabled: bool = True
    code: Optional[str] = None
    health_status: str = "unknown"
    last_health_check: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    oauth_tokens: Optional[Dict[str, Any]] = None
    project_id: Optional[str] = None
    aws_secret_key: Optional[str] = None
    aws_region: Optional[str] = None
    aws_session_token: Optional[str] = None
    bedrock_auth_mode: Optional[str] = None
    scoped_models: bool = False
    auto_cookie_refresh: bool = False
    model_count: int = 0
    models: List[NodeModelInfo] = []


class ModelDistributionResponse(BaseModel):
    """Model distribution across nodes"""
    model_name: str
    node_count: int
    nodes: List[str]


class AntigravityModelQuota(BaseModel):
    """Per-model quota from Google fetchAvailableModels."""
    name: str
    percentage: int
    reset_time: str = ""
    display_name: Optional[str] = None
    supports_images: Optional[bool] = None
    supports_thinking: Optional[bool] = None
    recommended: Optional[bool] = None


class AntigravityQuotaData(BaseModel):
    """Antigravity account quota snapshot."""
    models: List[AntigravityModelQuota] = []
    last_updated: int
    is_forbidden: bool = False
    forbidden_reason: Optional[str] = None
    subscription_tier: Optional[str] = None
    model_forwarding_rules: Dict[str, str] = {}


class AntigravityQuotaResponse(BaseModel):
    """Admin API response for Antigravity node quota."""
    success: bool
    node_id: int
    node_name: str
    project_id: Optional[str] = None
    quota: AntigravityQuotaData


class NodeSyncResponse(BaseModel):
    """Response from node model sync"""
    success: bool
    node_id: int
    node_name: str
    synced_count: int = 0
    models: List[str] = []
    total_models: int = 0
    error: Optional[str] = None


class PullModelRequest(BaseModel):
    """Pull model request"""
    name: str
    stream: bool = True


class PullModelResponse(BaseModel):
    """Pull model response"""
    status: str
    node: Optional[str] = None
    digest: Optional[str] = None
    total: Optional[int] = None
    completed: Optional[int] = None
    error: Optional[str] = None


class NodeLoadMetricsResponse(BaseModel):
    """Node load metrics"""
    node_id: int
    active_requests: int
    total_requests_today: int
    avg_response_time_ms: Optional[int] = None
    last_5_min_requests: Optional[int] = None
    recorded_at: Optional[str] = None


class LoadBalancerStatusResponse(BaseModel):
    """Load balancer status"""
    strategy: str
    total_nodes: int
    active_nodes: int
    healthy_nodes: int
    total_models: int
    node_loads: Dict[str, Any] = {}


# =============================================================================
# LOAD BALANCING - OLLAMA NODES
# =============================================================================

class NodePriorityItem(BaseModel):
    """Single node priority update"""
    node_id: int
    priority: int

class NodePriorityBatchRequest(BaseModel):
    """Batch update node priorities"""
    priorities: List[NodePriorityItem]

class NodePriorityBatchResponse(BaseModel):
    """Batch update node priorities response"""
    updated: int
    nodes: List[OllamaNodeResponse]

# --- Node Model Discovery ---

class NodeModelResponse(BaseModel):
    """Model on a node"""
    id: int
    node_id: int
    node_name: str
    model_name: str
    model_size: Optional[int] = None
    model_family: Optional[str] = None
    model_capabilities: Optional[Dict[str, Any]] = None
    digest: Optional[str] = None
    modified_at: Optional[str] = None
    last_seen: Optional[str] = None
    is_available: bool

class NodeModelListResponse(BaseModel):
    """List of models on a node"""
    node_id: int
    node_name: str
    models: List[NodeModelResponse]
    total: int

# --- Model Distribution ---

class ModelDistributionRow(BaseModel):
    """Per-model distribution row (models across nodes)"""
    model_name: str
    node_count: int
    nodes: List[str]

class ModelDistributionItem(BaseModel):
    """Model distribution across nodes"""
    model_name: str
    display_name: Optional[str] = None
    is_mapped: bool = False
    total_instances: int
    available_nodes: List[Dict[str, Any]]  # [{node_id, node_name, load, is_primary}]
    primary_node: Optional[Dict[str, Any]] = None  # {node_id, node_name}

class ModelDistributionResponse(BaseModel):
    """Model distribution across all nodes"""
    distribution: List[ModelDistributionItem]
    total_models: int
    total_nodes: int

# --- Load Metrics ---

class NodeLoadMetricResponse(BaseModel):
    """Node load metric"""
    node_id: int
    node_name: str
    active_requests: int
    total_requests_today: int
    avg_response_time_ms: Optional[int] = None
    last_5_min_requests: int
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    recorded_at: Optional[str] = None

class LoadBalancerStatusResponse(BaseModel):
    """Load balancer status"""
    strategy: str
    total_nodes: int
    active_nodes: int
    healthy_nodes: int
    unhealthy_nodes: int
    total_models_available: int
    load_metrics: List[NodeLoadMetricResponse]

# --- Model Routing Rules ---

class ModelRoutingRuleCreate(BaseModel):
    """Create a routing rule"""
    model_pattern: str
    preferred_node_id: Optional[int] = None
    fallback_node_ids: Optional[List[int]] = None
    load_balance_strategy: str = "least_loaded"  # round_robin, weighted, least_loaded
    is_active: bool = True

class ModelRoutingRuleUpdate(BaseModel):
    """Update a routing rule"""
    model_pattern: Optional[str] = None
    preferred_node_id: Optional[int] = None
    fallback_node_ids: Optional[List[int]] = None
    load_balance_strategy: Optional[str] = None
    is_active: Optional[bool] = None

class ModelRoutingRuleResponse(BaseModel):
    """Routing rule response"""
    id: int
    model_pattern: str
    preferred_node_id: Optional[int] = None
    preferred_node_name: Optional[str] = None
    fallback_node_ids: Optional[List[int]] = None
    fallback_node_names: Optional[List[str]] = None
    load_balance_strategy: str
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# --- Model Pull with Node Selection ---

class ModelPullToNodeRequest(BaseModel):
    """Pull model to specific node(s)"""
    name: str
    node_ids: Optional[List[int]] = None  # None = all nodes, specific IDs = selected nodes
    stream: bool = True
    insecure: bool = False

class ModelPullStatusResponse(BaseModel):
    """Status of model pull to multiple nodes"""
    model_name: str
    total_nodes: int
    completed_nodes: int
    failed_nodes: int
    results: List[Dict[str, Any]]  # [{node_id, node_name, status, error}]


# =============================================================================
# MODEL GROUPS - DYNAMIC MODEL SELECTION
# =============================================================================

class ModelGroupMemberRequest(BaseModel):
    """Add member to model group"""
    model_display_name: str
    capability_tags: Optional[List[str]] = None
    weight: int = 1
    priority: int = 0
    is_fallback: bool = False
    is_active: bool = True
    preferred_node_ids: Optional[List[int]] = None
    preferred_node_id: Optional[int] = None  # deprecated: use preferred_node_ids

    @model_validator(mode="after")
    def merge_legacy_preferred_node(self) -> "ModelGroupMemberRequest":
        if self.preferred_node_id is not None and not self.preferred_node_ids:
            object.__setattr__(self, "preferred_node_ids", [self.preferred_node_id])
        return self


class ModelGroupCreateRequest(BaseModel):
    """Create model group request"""
    name: str
    description: Optional[str] = None
    strategy: str = "round_robin"  # round_robin, weighted, priority
    is_active: bool = True
    list_in_catalog: bool = False
    members: Optional[List[ModelGroupMemberRequest]] = None


class ModelGroupUpdateRequest(BaseModel):
    """Update model group request"""
    name: Optional[str] = None
    description: Optional[str] = None
    strategy: Optional[str] = None
    is_active: Optional[bool] = None
    list_in_catalog: Optional[bool] = None


class ModelGroupMemberResponse(BaseModel):
    """Model group member response"""
    id: int
    model_display_name: str
    capability_tags: Optional[List[str]] = None
    weight: int = 1
    priority: int = 0
    is_fallback: bool = False
    is_active: bool = True
    preferred_node_ids: List[int] = []


class ModelGroupResponse(BaseModel):
    """Model group response"""
    id: int
    name: str
    description: Optional[str] = None
    strategy: str = "round_robin"
    is_active: bool = True
    list_in_catalog: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ModelGroupDetailResponse(BaseModel):
    """Model group response with members"""
    id: int
    name: str
    description: Optional[str] = None
    strategy: str = "round_robin"
    is_active: bool = True
    list_in_catalog: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    members: List[ModelGroupMemberResponse] = []


class ModelGroupListResponse(BaseModel):
    """List of model groups"""
    groups: List[ModelGroupResponse]
    total: int


class MemberReorderItem(BaseModel):
    """Single item for member reorder request"""
    id: int
    priority: int


class MemberReorderRequest(BaseModel):
    """Reorder members by updating their priorities"""
    members: List[MemberReorderItem]

