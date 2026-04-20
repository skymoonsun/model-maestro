"""Add model groups tables

Revision ID: 007_model_groups
Revises: 006_load_balancing
Create Date: 2026-03-11

Adds:
- model_groups: Dynamic model selection groups with fallback chains
- model_group_members: Member models within groups
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision = '007_model_groups'
down_revision = '006_load_balancing'
branch_labels = None
depends_on = None


def upgrade():
    # Create model_groups table
    op.create_table(
        'model_groups',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), unique=True, nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('strategy', sa.String(50), nullable=False, server_default='round_robin'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_model_groups_id', 'model_groups', ['id'])
    op.create_index('ix_model_groups_name', 'model_groups', ['name'], unique=True)

    # Create model_group_members table
    op.create_table(
        'model_group_members',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('model_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('model_display_name', sa.String(255), nullable=False),
        sa.Column('capability_tags', ARRAY(sa.String()), nullable=True),
        sa.Column('weight', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_fallback', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.UniqueConstraint('group_id', 'model_display_name', name='uq_group_model'),
    )
    op.create_index('ix_model_group_members_id', 'model_group_members', ['id'])
    op.create_index('ix_model_group_members_group_id', 'model_group_members', ['group_id'])
    op.create_index('ix_model_group_members_model_display_name', 'model_group_members', ['model_display_name'])


def downgrade():
    op.drop_index('ix_model_group_members_model_display_name', table_name='model_group_members')
    op.drop_index('ix_model_group_members_group_id', table_name='model_group_members')
    op.drop_index('ix_model_group_members_id', table_name='model_group_members')
    op.drop_table('model_group_members')

    op.drop_index('ix_model_groups_name', table_name='model_groups')
    op.drop_index('ix_model_groups_id', table_name='model_groups')
    op.drop_table('model_groups')