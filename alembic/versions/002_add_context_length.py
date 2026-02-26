"""add context_length to model_mappings

Revision ID: 002_add_context_length
Revises: 1f3045a507f0
Create Date: 2026-02-26 23:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002_add_context_length'
down_revision = '1f3045a507f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add context_length column to model_mappings table
    op.add_column('model_mappings', sa.Column('context_length', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('model_mappings', 'context_length')
