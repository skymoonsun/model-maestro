"""add headers to ollama_nodes

Revision ID: f5b8a2d3e1c4
Revises: e4a7f2c1d8b3
Create Date: 2026-05-09 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f5b8a2d3e1c4'
down_revision = 'e4a7f2c1d8b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ollama_nodes', sa.Column('headers', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('ollama_nodes', 'headers')
