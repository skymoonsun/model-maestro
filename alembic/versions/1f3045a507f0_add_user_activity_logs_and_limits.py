"""add_user_activity_logs_and_limits

Revision ID: 1f3045a507f0
Revises: 001
Create Date: 2025-10-25 00:37:18.738360

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '1f3045a507f0'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user_activity_logs table
    op.create_table(
        'user_activity_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('model_name', sa.String(length=255), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_tokens', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('request_type', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_activity_logs_id'), 'user_activity_logs', ['id'], unique=False)
    op.create_index(op.f('ix_user_activity_logs_user_id'), 'user_activity_logs', ['user_id'], unique=False)

    # Create user_limits table
    op.create_table(
        'user_limits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('request_limit', sa.Integer(), nullable=True),
        sa.Column('token_limit', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_user_limits_id'), 'user_limits', ['id'], unique=False)


def downgrade() -> None:
    # Drop user_limits table
    op.drop_index(op.f('ix_user_limits_id'), table_name='user_limits')
    op.drop_table('user_limits')
    
    # Drop user_activity_logs table
    op.drop_index(op.f('ix_user_activity_logs_user_id'), table_name='user_activity_logs')
    op.drop_index(op.f('ix_user_activity_logs_id'), table_name='user_activity_logs')
    op.drop_table('user_activity_logs')
