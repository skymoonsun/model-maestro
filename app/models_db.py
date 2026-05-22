"""SQLAlchemy database models"""

from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, UniqueConstraint, Numeric, BigInteger, Table
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


model_group_member_nodes = Table(
    "model_group_member_nodes",
    Base.metadata,
    Column("member_id", Integer, ForeignKey("model_group_members.id", ondelete="CASCADE"), primary_key=True),
    Column("node_id", Integer, ForeignKey("ollama_nodes.id", ondelete="CASCADE"), primary_key=True),
)

model_mapping_nodes = Table(
    "model_mapping_nodes",
    Base.metadata,
    Column("mapping_id", Integer, ForeignKey("model_mappings.id", ondelete="CASCADE"), primary_key=True),
    Column("node_id", Integer, ForeignKey("ollama_nodes.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    """User model"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    token = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_active = Column(Boolean, default=True, nullable=False)
    
    # Relationships
    user_models = relationship("UserModel", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(username='{self.username}')>"


class ModelMapping(Base):
    """Model name mapping"""
    __tablename__ = "model_mappings"

    id = Column(Integer, primary_key=True, index=True)
    display_name = Column(String(255), unique=True, nullable=False, index=True)
    real_name = Column(String(255), nullable=False)
    context_length = Column(Integer, nullable=True)  # Context window size in tokens (e.g., 131072 for 128K)
    capabilities = Column(ARRAY(String), nullable=True)  # ["completion", "tools", "thinking", "vision"]
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    nodes = relationship("OllamaNode", secondary=model_mapping_nodes)

    def __repr__(self):
        ctx = f", ctx={self.context_length}" if self.context_length else ""
        caps = f", caps={self.capabilities}" if self.capabilities else ""
        return f"<ModelMapping(display_name='{self.display_name}', real_name='{self.real_name}'{ctx}{caps})>"


class UserModel(Base):
    """User-Model relationship"""
    __tablename__ = "user_models"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    model_display_name = Column(String(255), nullable=True)
    has_all_models = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="user_models")
    
    # Unique constraint
    __table_args__ = (
        UniqueConstraint('user_id', 'model_display_name', name='uq_user_model'),
    )
    
    def __repr__(self):
        return f"<UserModel(user_id={self.user_id}, model='{self.model_display_name}', all={self.has_all_models})>"

class UserNode(Base):
    """User-Node relationship for node-level access control"""
    __tablename__ = "user_nodes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    node_id = Column(Integer, ForeignKey("ollama_nodes.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User")
    node = relationship("OllamaNode")

    __table_args__ = (
        UniqueConstraint('user_id', 'node_id', name='uq_user_node'),
    )

    def __repr__(self):
        return f"<UserNode(user_id={self.user_id}, node_id={self.node_id})>"


class UserNodeModel(Base):
    """User-Node-Model relationship for fine-grained access control"""
    __tablename__ = "user_node_models"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    node_id = Column(Integer, ForeignKey("ollama_nodes.id", ondelete="CASCADE"), nullable=False)
    model_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User")
    node = relationship("OllamaNode")

    __table_args__ = (
        UniqueConstraint('user_id', 'node_id', 'model_name', name='uq_user_node_model'),
    )

    def __repr__(self):
        return f"<UserNodeModel(user_id={self.user_id}, node_id={self.node_id}, model='{self.model_name}')>"


class UserActivityLog(Base):
    """User activity log for tracking token usage and model access"""
    __tablename__ = "user_activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    model_name = Column(String(255), nullable=False)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    request_type = Column(String(50), nullable=False)  # generate, chat, embeddings, etc.
    source = Column(String(100), nullable=True)  # Cursor, Claude, OpenClaw, Ollama Native, OpenAI-Compatible, Grafana
    url_path = Column(String(500), nullable=True)  # Full request URL path
    status_code = Column(Integer, nullable=True)  # HTTP status: 200, 400, 429, 500
    duration_ms = Column(Integer, nullable=True)  # Request duration in milliseconds
    error_message = Column(Text, nullable=True)  # Error message for failed requests
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User")
    
    def __repr__(self):
        return f"<UserActivityLog(user_id={self.user_id}, model='{self.model_name}', tokens={self.total_tokens})>"

class UserLimit(Base):
    """User request and token limits"""
    __tablename__ = "user_limits"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    request_limit = Column(Integer, nullable=True)  # None for unlimited
    token_limit = Column(Integer, nullable=True)    # None for unlimited
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User")
    
    def __repr__(self):
        return f"<UserLimit(user_id={self.user_id}, requests={self.request_limit}, tokens={self.token_limit})>"


# =============================================================================
# FRONTEND PANEL - NEW TABLES
# =============================================================================

class SystemConfig(Base):
    """System-wide key/value configuration"""
    __tablename__ = "system_config"
    
    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<SystemConfig(key='{self.key}', value='{self.value[:50]}...')>"


class ModelConfig(Base):
    """Model-specific configuration (tool filtering, param restrictions, rate limits)"""
    __tablename__ = "model_config"
    
    id = Column(Integer, primary_key=True, index=True)
    model_prefix = Column(String(255), unique=True, nullable=False, index=True)  # "minimax", "deepseek", etc.
    is_exact_match = Column(Boolean, default=False, nullable=False, server_default="false")
    
    
    # Tool filtering
    allowed_tools = Column(ARRAY(String), nullable=True)  # NULL = tüm tool'lar izinli
    
    # Parameter restrictions
    unsupported_params = Column(ARRAY(String), nullable=True)  # Kaldırılacak parametreler
    
    # Context settings
    default_context_length = Column(Integer, default=32768)
    max_context_length = Column(Integer, nullable=True)
    
    # Rate limiting
    requests_per_minute = Column(Integer, nullable=True)
    tokens_per_minute = Column(Integer, nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    maintenance_mode = Column(Boolean, default=False, nullable=False)
    
    # Metadata
    description = Column(Text, nullable=True)
    cost_multiplier = Column(Numeric(6, 2), default=1.0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<ModelConfig(prefix='{self.model_prefix}', active={self.is_active})>"


class ToolSet(Base):
    """Pre-defined tool sets for easy assignment"""
    __tablename__ = "tool_sets"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)  # "basic", "standard", "full"
    tools = Column(ARRAY(String), nullable=True)  # NULL = tüm tool'lar (full set)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        count = len(self.tools) if self.tools else "∞"
        return f"<ToolSet(name='{self.name}', tools={count})>"


class ModelFormatPattern(Base):
    """Custom format patterns for specific models (Kimi tool calls, etc.)"""
    __tablename__ = "model_format_patterns"
    
    id = Column(Integer, primary_key=True, index=True)
    model_prefix = Column(String(255), nullable=False, index=True)
    format_type = Column(String(50), nullable=False)  # 'custom_tool_call', 'reasoning_split', etc.
    pattern_config = Column(JSONB, nullable=False)  # Regex patterns and settings
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Unique constraint: one pattern type per model prefix
    __table_args__ = (
        UniqueConstraint('model_prefix', 'format_type', name='uq_model_format'),
    )
    
    def __repr__(self):
        return f"<ModelFormatPattern(prefix='{self.model_prefix}', type='{self.format_type}')>"


class AuditLog(Base):
    """Admin action audit log"""
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(100), nullable=False, index=True)  # "create_user", "update_config", etc.
    entity_type = Column(String(100), nullable=True)  # "user", "model_config", "system_config", etc.
    entity_id = Column(String(255), nullable=True)  # Affected entity identifier
    details = Column(JSONB, nullable=True)  # Action details (before/after values, etc.)
    admin_ip = Column(String(45), nullable=True)  # Admin's IP address
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    def __repr__(self):
        return f"<AuditLog(action='{self.action}', entity='{self.entity_type}:{self.entity_id}')>"


# =============================================================================
# LOAD BALANCING - OLLAMA NODES
# =============================================================================

class OllamaNode(Base):
    """Ollama server node for load balancing"""
    __tablename__ = "ollama_nodes"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)  # "main-server", "backup-1"
    base_url = Column(String(500), nullable=False)  # "http://194.87.188.8:11434"
    api_key = Column(String(2000), nullable=True)  # Optional auth header (JWT tokens can be very long)
    priority = Column(Integer, default=0)  # Higher = preferred (fallback order)
    weight = Column(Integer, default=100)  # Load balancing weight
    is_active = Column(Boolean, default=True, nullable=False)
    node_type = Column(String(50), default='ollama', nullable=False)  # 'ollama', 'vllm', 'antigravity', 'bedrock'
    warmup_enabled = Column(Boolean, default=True, nullable=False, server_default='true')  # Enable model warmup for this node
    auto_sync_enabled = Column(Boolean, default=True, nullable=False, server_default='true')  # Include in background model discovery sync
    code = Column(String(30), unique=True, nullable=True, index=True)  # Short routing code e.g. "trmix", "us-east-1"
    health_check_url = Column(String(500), nullable=True)  # Custom health check endpoint
    headers = Column(JSONB, nullable=True)  # Custom HTTP headers as {"X-Custom": "value"}
    oauth_tokens = Column(JSONB, nullable=True)  # Google OAuth tokens {"access_token": "...", "refresh_token": "...", "expires_in": 3600}
    project_id = Column(String(100), nullable=True)  # Google cloudaicompanionProject ID
    aws_secret_key = Column(String(2000), nullable=True)  # AWS Secret Access Key for Bedrock (IAM mode)
    aws_region = Column(String(50), nullable=True)  # AWS Region for Bedrock (e.g. us-east-1)
    aws_session_token = Column(String(4000), nullable=True)  # AWS Session Token (optional, for STS temp creds)
    bedrock_auth_mode = Column(String(20), nullable=True)  # 'iam' | 'api_key'
    scoped_models = Column(Boolean, default=False, nullable=True, server_default='false')  # If True, models only accessible via node:code:model prefix
    auto_cookie_refresh = Column(Boolean, default=False, nullable=False, server_default='false')  # Auto-capture WAF challenge cookies
    last_health_check = Column(DateTime(timezone=True), nullable=True)
    health_status = Column(String(50), default='unknown')  # 'healthy', 'unhealthy', 'unknown'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    node_models = relationship("NodeModel", back_populates="node", cascade="all, delete-orphan")
    load_metrics = relationship("NodeLoadMetric", back_populates="node", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<OllamaNode(name='{self.name}', url='{self.base_url}', status='{self.health_status}')>"


class NodeModel(Base):
    """Models available on each node (auto-discovered from /api/tags)"""
    __tablename__ = "node_models"
    
    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, ForeignKey("ollama_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name = Column(String(255), nullable=False, index=True)  # Real model name (glm-5:cloud)
    model_size = Column(BigInteger, nullable=True)  # Model size in bytes
    model_family = Column(String(100), nullable=True)  # "glm", "qwen", "llama"
    model_capabilities = Column(JSONB, nullable=True)  # {"completion": true, "vision": true}
    digest = Column('model_digest', String(255), nullable=True)  # Model digest/SHA (DB: model_digest)
    modified_at = Column(DateTime(timezone=True), nullable=True)  # Last modified from Ollama
    last_seen = Column(DateTime(timezone=True), server_default=func.now())  # When last detected
    is_available = Column(Boolean, default=True, nullable=False)  # Manual override
    
    # Relationships
    node = relationship("OllamaNode", back_populates="node_models")
    
    # Unique constraint: one model per node
    __table_args__ = (
        UniqueConstraint('node_id', 'model_name', name='uq_node_model_name'),
    )
    
    def __repr__(self):
        return f"<NodeModel(node_id={self.node_id}, model='{self.model_name}', available={self.is_available})>"


class NodeLoadMetric(Base):
    """Node load tracking metrics"""
    __tablename__ = "node_load_metrics"
    
    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, ForeignKey("ollama_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    active_requests = Column(Integer, default=0, nullable=False)
    total_requests_today = Column(Integer, default=0, nullable=False)
    avg_response_time_ms = Column(Integer, nullable=True)  # Rolling average
    last_5_min_requests = Column(Integer, default=0, nullable=False)  # Rate limiting
    cpu_usage = Column(Numeric(5, 2), nullable=True)  # CPU percentage
    memory_usage = Column(Numeric(5, 2), nullable=True)  # Memory percentage
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Relationships
    node = relationship("OllamaNode", back_populates="load_metrics")
    
    def __repr__(self):
        return f"<NodeLoadMetric(node_id={self.node_id}, active={self.active_requests}, avg_ms={self.avg_response_time_ms})>"


class ModelRoutingRule(Base):
    """Manual routing rules for specific models (optional override)"""
    __tablename__ = "model_routing_rules"

    id = Column(Integer, primary_key=True, index=True)
    model_pattern = Column(String(255), nullable=False, index=True)  # "glm-*" or "qwen3-coder:*"
    preferred_node_id = Column(Integer, ForeignKey("ollama_nodes.id", ondelete="SET NULL"), nullable=True)
    fallback_node_ids = Column(ARRAY(Integer), nullable=True)  # [2, 3] - fallback order
    load_balance_strategy = Column(String(50), default='least_loaded')  # 'round_robin', 'weighted', 'least_loaded'
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    preferred_node = relationship("OllamaNode", foreign_keys=[preferred_node_id])

    def __repr__(self):
        return f"<ModelRoutingRule(pattern='{self.model_pattern}', strategy='{self.load_balance_strategy}')>"


# =============================================================================
# MODEL GROUPS - DYNAMIC MODEL SELECTION
# =============================================================================

class ModelGroup(Base):
    """Model group for dynamic model selection with fallback chain"""
    __tablename__ = "model_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    strategy = Column(String(50), default='round_robin', nullable=False)  # 'round_robin', 'weighted', 'priority'
    is_active = Column(Boolean, default=True, nullable=False)
    list_in_catalog = Column(Boolean, default=False, nullable=False, server_default='false')
    priority = Column(Integer, default=0, nullable=False, server_default='0')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    members = relationship("ModelGroupMember", back_populates="group", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ModelGroup(name='{self.name}', strategy='{self.strategy}', active={self.is_active})>"


class ModelGroupMember(Base):
    """Member model within a model group"""
    __tablename__ = "model_group_members"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("model_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    model_display_name = Column(String(255), nullable=False, index=True)  # References ModelMapping.display_name
    capability_tags = Column(ARRAY(String), nullable=True)  # ["vision", "code", "reasoning"]
    weight = Column(Integer, default=1, nullable=False)  # For weighted strategy
    priority = Column(Integer, default=0, nullable=False)  # For priority strategy (lower = higher priority)
    is_fallback = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    group = relationship("ModelGroup", back_populates="members")
    preferred_nodes = relationship("OllamaNode", secondary=model_group_member_nodes)

    # Unique constraint: one model per group
    __table_args__ = (
        UniqueConstraint('group_id', 'model_display_name', name='uq_group_model'),
    )

    def __repr__(self):
        return f"<ModelGroupMember(group_id={self.group_id}, model='{self.model_display_name}', priority={self.priority}, fallback={self.is_fallback})>"

