"""add auto_cookie_refresh to ollama_nodes

Revision ID: b8afba3b73f5
Revises: a1b2c3d4e5f6
Create Date: 2026-05-14 12:35:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8afba3b73f5'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ollama_nodes', sa.Column('auto_cookie_refresh', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('ollama_nodes', 'auto_cookie_refresh')
