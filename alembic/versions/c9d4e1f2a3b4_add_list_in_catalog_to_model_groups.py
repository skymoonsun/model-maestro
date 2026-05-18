"""Add list_in_catalog to model_groups

Revision ID: c9d4e1f2a3b4
Revises: 4c0abe20c3f4
Create Date: 2026-05-18

"""
from alembic import op
import sqlalchemy as sa


revision = "c9d4e1f2a3b4"
down_revision = "4c0abe20c3f4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "model_groups",
        sa.Column(
            "list_in_catalog",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade():
    op.drop_column("model_groups", "list_in_catalog")
