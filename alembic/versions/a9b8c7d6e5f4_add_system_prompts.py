"""add system_prompts table

Revision ID: a9b8c7d6e5f4
Revises: 9d5a2e1f7c84
Create Date: 2026-07-01 00:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9b8c7d6e5f4'
down_revision = '9d5a2e1f7c84'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'system_prompts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope_type', sa.String(length=20), nullable=False),
        sa.Column('scope_value', sa.String(length=255), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scope_type', 'scope_value', name='uq_system_prompt_scope'),
    )
    op.create_index('ix_system_prompts_id', 'system_prompts', ['id'])
    op.create_index('ix_system_prompts_scope_type', 'system_prompts', ['scope_type'])
    op.create_index('ix_system_prompts_scope_value', 'system_prompts', ['scope_value'])


def downgrade() -> None:
    op.drop_index('ix_system_prompts_scope_value', table_name='system_prompts')
    op.drop_index('ix_system_prompts_scope_type', table_name='system_prompts')
    op.drop_index('ix_system_prompts_id', table_name='system_prompts')
    op.drop_table('system_prompts')
