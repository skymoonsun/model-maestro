"""add activity log enhancements (status_code, duration_ms, error_message)

Revision ID: 008_activity_log_enhancements
Revises: b5af99d808ad
Create Date: 2026-04-25

"""
from alembic import op
import sqlalchemy as sa

revision = '008_activity_log_enhancements'
down_revision = 'b5af99d808ad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_activity_logs', sa.Column('status_code', sa.Integer(), nullable=True))
    op.add_column('user_activity_logs', sa.Column('duration_ms', sa.Integer(), nullable=True))
    op.add_column('user_activity_logs', sa.Column('error_message', sa.Text(), nullable=True))

    op.create_index('ix_user_activity_logs_status_code', 'user_activity_logs', ['status_code'])
    op.create_index('ix_user_activity_logs_created_at', 'user_activity_logs', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_user_activity_logs_created_at', table_name='user_activity_logs')
    op.drop_index('ix_user_activity_logs_status_code', table_name='user_activity_logs')

    op.drop_column('user_activity_logs', 'error_message')
    op.drop_column('user_activity_logs', 'duration_ms')
    op.drop_column('user_activity_logs', 'status_code')