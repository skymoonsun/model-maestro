"""add scoped_models to ollama_nodes

Revision ID: aa95609e00c5
Revises: 3445758d1006
Create Date: 2026-05-12 20:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'aa95609e00c5'
down_revision = '3445758d1006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ollama_nodes', sa.Column('scoped_models', sa.Boolean(), nullable=True, server_default='false'))


def downgrade() -> None:
    op.drop_column('ollama_nodes', 'scoped_models')
