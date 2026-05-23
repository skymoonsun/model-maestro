"""add is_fallback_413 to group members

Revision ID: 6a2d901be3f4
Revises: 5f1c890cdac0
Create Date: 2026-05-24 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a2d901be3f4'
down_revision = '5f1c890cdac0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'model_group_members',
        sa.Column('is_fallback_413', sa.Boolean(), nullable=False, server_default='false')
    )


def downgrade() -> None:
    op.drop_column('model_group_members', 'is_fallback_413')
