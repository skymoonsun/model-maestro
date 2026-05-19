"""add auto_sync_enabled to ollama_nodes

Revision ID: d1e2f3a4b5c6
Revises: c9d4e1f2a3b4
Create Date: 2026-05-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c9d4e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ollama_nodes",
        sa.Column(
            "auto_sync_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("ollama_nodes", "auto_sync_enabled")
