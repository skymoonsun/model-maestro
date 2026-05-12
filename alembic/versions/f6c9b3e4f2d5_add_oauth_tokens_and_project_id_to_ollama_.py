"""add oauth_tokens and project_id to ollama_nodes for antigravity support

Revision ID: f6c9b3e4f2d5
Revises: f5b8a2d3e1c4
Create Date: 2026-05-11 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f6c9b3e4f2d5'
down_revision = 'f5b8a2d3e1c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ollama_nodes', sa.Column('oauth_tokens', postgresql.JSONB(), nullable=True))
    op.add_column('ollama_nodes', sa.Column('project_id', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('ollama_nodes', 'project_id')
    op.drop_column('ollama_nodes', 'oauth_tokens')
