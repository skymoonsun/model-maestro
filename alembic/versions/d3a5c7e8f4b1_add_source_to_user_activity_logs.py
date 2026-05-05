"""add source to user_activity_logs

Revision ID: d3a5c7e8f4b1
Revises: c8e2f4a9b1d2
Create Date: 2026-05-05 11:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3a5c7e8f4b1'
down_revision = 'c8e2f4a9b1d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_activity_logs', sa.Column('source', sa.String(length=100), nullable=True))
    op.add_column('user_activity_logs', sa.Column('url_path', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('user_activity_logs', 'url_path')
    op.drop_column('user_activity_logs', 'source')
