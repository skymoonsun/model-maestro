"""add node_code to ollama_nodes

Revision ID: e4a7f2c1d8b3
Revises: d3a5c7e8f4b1
Create Date: 2026-05-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4a7f2c1d8b3'
down_revision = 'd3a5c7e8f4b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ollama_nodes', sa.Column('code', sa.String(length=30), nullable=True))
    op.create_index(op.f('ix_ollama_nodes_code'), 'ollama_nodes', ['code'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_ollama_nodes_code'), table_name='ollama_nodes')
    op.drop_column('ollama_nodes', 'code')
