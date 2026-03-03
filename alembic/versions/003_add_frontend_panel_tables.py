"""Add frontend panel tables (system_config, model_config, tool_sets, model_format_patterns, audit_logs)

Revision ID: 003
Revises: 1f3045a507f0
Create Date: 2026-02-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002_add_context_length'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create system_config table
    op.create_table(
        'system_config',
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('key')
    )

    # Create model_config table
    op.create_table(
        'model_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('model_prefix', sa.String(length=255), nullable=False),
        sa.Column('allowed_tools', ARRAY(sa.String()), nullable=True),
        sa.Column('unsupported_params', ARRAY(sa.String()), nullable=True),
        sa.Column('default_context_length', sa.Integer(), server_default=sa.text('32768'), nullable=True),
        sa.Column('max_context_length', sa.Integer(), nullable=True),
        sa.Column('requests_per_minute', sa.Integer(), nullable=True),
        sa.Column('tokens_per_minute', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('maintenance_mode', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('cost_multiplier', sa.Numeric(precision=6, scale=2), server_default=sa.text('1.0'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_config_id'), 'model_config', ['id'], unique=False)
    op.create_index(op.f('ix_model_config_model_prefix'), 'model_config', ['model_prefix'], unique=True)

    # Create tool_sets table
    op.create_table(
        'tool_sets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('tools', ARRAY(sa.String()), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tool_sets_id'), 'tool_sets', ['id'], unique=False)
    op.create_index(op.f('ix_tool_sets_name'), 'tool_sets', ['name'], unique=True)

    # Create model_format_patterns table
    op.create_table(
        'model_format_patterns',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('model_prefix', sa.String(length=255), nullable=False),
        sa.Column('format_type', sa.String(length=50), nullable=False),
        sa.Column('pattern_config', JSONB(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('model_prefix', 'format_type', name='uq_model_format')
    )
    op.create_index(op.f('ix_model_format_patterns_id'), 'model_format_patterns', ['id'], unique=False)
    op.create_index(op.f('ix_model_format_patterns_model_prefix'), 'model_format_patterns', ['model_prefix'], unique=False)

    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=100), nullable=True),
        sa.Column('entity_id', sa.String(length=255), nullable=True),
        sa.Column('details', JSONB(), nullable=True),
        sa.Column('admin_ip', sa.String(length=45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_id'), 'audit_logs', ['id'], unique=False)
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)

    # NOT: Seed verileri artık app/seeds/ dizinindeki seeder dosyaları tarafından yönetiliyor.
    # Seed verilerini eklemek için: make db-seed


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
    
    op.drop_index(op.f('ix_model_format_patterns_model_prefix'), table_name='model_format_patterns')
    op.drop_index(op.f('ix_model_format_patterns_id'), table_name='model_format_patterns')
    op.drop_table('model_format_patterns')
    
    op.drop_index(op.f('ix_tool_sets_name'), table_name='tool_sets')
    op.drop_index(op.f('ix_tool_sets_id'), table_name='tool_sets')
    op.drop_table('tool_sets')
    
    op.drop_index(op.f('ix_model_config_model_prefix'), table_name='model_config')
    op.drop_index(op.f('ix_model_config_id'), table_name='model_config')
    op.drop_table('model_config')
    
    op.drop_table('system_config')
