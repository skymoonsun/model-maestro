"""add bedrock support to ollama nodes

Revision ID: 3445758d1006
Revises: b13a5d7b00f7
Create Date: 2026-05-12 20:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3445758d1006'
down_revision = 'b13a5d7b00f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ollama_nodes', sa.Column('aws_secret_key', sa.String(length=2000), nullable=True))
    op.add_column('ollama_nodes', sa.Column('aws_region', sa.String(length=50), nullable=True))
    op.add_column('ollama_nodes', sa.Column('aws_session_token', sa.String(length=4000), nullable=True))


def downgrade() -> None:
    op.drop_column('ollama_nodes', 'aws_session_token')
    op.drop_column('ollama_nodes', 'aws_region')
    op.drop_column('ollama_nodes', 'aws_secret_key')
