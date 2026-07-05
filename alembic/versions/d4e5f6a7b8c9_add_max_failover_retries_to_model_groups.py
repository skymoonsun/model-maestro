"""add max_failover_retries to model_groups

Revision ID: d4e5f6a7b8c9
Revises: c7d8e9f0a1b2
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c7d8e9f0a1b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'model_groups',
        sa.Column('max_failover_retries', sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('model_groups', 'max_failover_retries')
