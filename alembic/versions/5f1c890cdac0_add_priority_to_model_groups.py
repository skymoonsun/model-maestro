"""add priority to model groups

Revision ID: 5f1c890cdac0
Revises: e7b2c4d8a1f0
Create Date: 2026-05-22 19:26:57.858723

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f1c890cdac0'
down_revision = 'e7b2c4d8a1f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'model_groups',
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0')
    )


def downgrade() -> None:
    op.drop_column('model_groups', 'priority')

