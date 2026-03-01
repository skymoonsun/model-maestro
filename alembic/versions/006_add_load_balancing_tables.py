"""Add load balancing tables

Revision ID: 006_load_balancing
Revises: 005_add_is_exact_match
Create Date: 2026-03-01

Adds:
- ollama_nodes: Multi-node Ollama server management
- node_models: Auto-discovered models per node
- model_routing_rules: Manual routing override rules
- node_load_metrics: Real-time load tracking
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


# revision identifiers, used by Alembic.
revision = '006_load_balancing'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade():
    # ============================================================================
    # OLLAMA NODES - Multi-node server management
    # ============================================================================
    op.create_table(
        'ollama_nodes',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('name', sa.String(255), unique=True, nullable=False, index=True,
                  comment='Friendly name: "main-server", "backup-1"'),
        sa.Column('base_url', sa.String(500), nullable=False,
                  comment='Ollama server URL: http://194.87.188.8:11434'),
        sa.Column('api_key', sa.String(500), nullable=True,
                  comment='Optional API key for authentication'),
        sa.Column('priority', sa.Integer(), default=0,
                  comment='Higher = preferred for fallback (100=primary, 50=backup)'),
        sa.Column('weight', sa.Integer(), default=100,
                  comment='Load balancing weight (higher = more traffic)'),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('health_check_url', sa.String(500), nullable=True,
                  comment='Custom health check endpoint'),
        sa.Column('health_status', sa.String(50), default='unknown', nullable=False,
                  comment='healthy, unhealthy, unknown'),
        sa.Column('last_health_check', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_model_sync', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata', JSONB, nullable=True,
                  comment='Additional node metadata'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_ollama_nodes_status', 'ollama_nodes', ['is_active', 'health_status'])
    
    # ============================================================================
    # NODE MODELS - Auto-discovered models per node
    # ============================================================================
    op.create_table(
        'node_models',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('node_id', sa.Integer(), sa.ForeignKey('ollama_nodes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('model_name', sa.String(255), nullable=False, index=True,
                  comment='Real model name from Ollama (e.g., glm-5:cloud)'),
        sa.Column('model_size', sa.BigInteger(), nullable=True,
                  comment='Model size in bytes'),
        sa.Column('model_family', sa.String(100), nullable=True,
                  comment='Model family (glm, qwen, llama, etc.)'),
        sa.Column('model_capabilities', JSONB, nullable=True,
                  comment='{"completion": true, "vision": true, "tools": true}'),
        sa.Column('model_digest', sa.String(255), nullable=True,
                  comment='Model SHA256 digest'),
        sa.Column('modified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  comment='When last detected by discovery'),
        sa.Column('is_available', sa.Boolean(), default=True, nullable=False,
                  comment='Manual availability override'),
        sa.Column('request_count', sa.Integer(), default=0,
                  comment='Total requests served for this model'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    
    # Unique constraint: one model per node
    op.create_unique_constraint('uq_node_model', 'node_models', ['node_id', 'model_name'])
    
    # Index for finding nodes that have a specific model
    op.create_index('ix_node_models_available', 'node_models', 
                    ['model_name', 'is_available', 'node_id'])
    
    # ============================================================================
    # MODEL ROUTING RULES - Manual override rules (optional)
    # ============================================================================
    op.create_table(
        'model_routing_rules',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('model_pattern', sa.String(255), nullable=False, index=True,
                  comment='Glob pattern: "glm-*", "qwen3-coder:*", "deepseek-*"'),
        sa.Column('preferred_node_id', sa.Integer(), sa.ForeignKey('ollama_nodes.id', ondelete='SET NULL'), nullable=True),
        sa.Column('fallback_node_ids', ARRAY(sa.Integer), nullable=True,
                  comment='Array of fallback node IDs in order: [2, 3, 4]'),
        sa.Column('load_balance_strategy', sa.String(50), default='least_loaded', nullable=False,
                  comment='round_robin, weighted, least_loaded, priority'),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_routing_rules_active', 'model_routing_rules', ['model_pattern', 'is_active'])
    
    # ============================================================================
    # NODE LOAD METRICS - Real-time load tracking
    # ============================================================================
    op.create_table(
        'node_load_metrics',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('node_id', sa.Integer(), sa.ForeignKey('ollama_nodes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('active_requests', sa.Integer(), default=0,
                  comment='Currently active requests'),
        sa.Column('total_requests_today', sa.Integer(), default=0,
                  comment='Total requests served today'),
        sa.Column('avg_response_time_ms', sa.Integer(), nullable=True,
                  comment='Rolling average response time'),
        sa.Column('last_5_min_requests', sa.Integer(), default=0,
                  comment='Requests in last 5 minutes for rate limiting'),
        sa.Column('cpu_usage', sa.Float(), nullable=True,
                  comment='CPU usage percentage (if available from Ollama)'),
        sa.Column('memory_usage', sa.Float(), nullable=True,
                  comment='Memory usage percentage (if available)'),
        sa.Column('gpu_usage', sa.Float(), nullable=True,
                  comment='GPU usage percentage (if available)'),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    
    op.create_index('ix_node_load_metrics_node_time', 'node_load_metrics', ['node_id', 'recorded_at'])


def downgrade():
    # Drop tables in reverse order (foreign key dependencies)
    op.drop_table('node_load_metrics')
    op.drop_table('model_routing_rules')
    op.drop_table('node_models')
    op.drop_table('ollama_nodes')