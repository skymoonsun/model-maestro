"""add bedrock_auth_mode to ollama_nodes

Revision ID: e7b2c4d8a1f0
Revises: d1e2f3a4b5c6
Create Date: 2026-05-19

"""
from alembic import op
import sqlalchemy as sa


revision = "e7b2c4d8a1f0"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ollama_nodes",
        sa.Column("bedrock_auth_mode", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ollama_nodes", "bedrock_auth_mode")
