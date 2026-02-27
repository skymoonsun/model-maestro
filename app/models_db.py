"""SQLAlchemy database models"""

from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, UniqueConstraint, Numeric
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


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

class UserActivityLog(Base):
    """User activity log for tracking token usage and model access"""
    __tablename__ = "user_activity_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    model_name = Column(String(255), nullable=False)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    request_type = Column(String(50), nullable=False)  # generate, chat, embeddings, etc.
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

