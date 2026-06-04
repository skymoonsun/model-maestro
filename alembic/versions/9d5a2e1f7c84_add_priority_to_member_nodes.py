"""add priority to model_group_member_nodes

Revision ID: 9d5a2e1f7c84
Revises: 8c4f1a2b9d7e
Create Date: 2026-06-05 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d5a2e1f7c84'
down_revision = '8c4f1a2b9d7e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'model_group_member_nodes',
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0')
    )


def downgrade() -> None:
    op.drop_column('model_group_member_nodes', 'priority')
