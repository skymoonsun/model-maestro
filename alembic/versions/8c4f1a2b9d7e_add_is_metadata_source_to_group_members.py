"""add is_metadata_source to group members

Revision ID: 8c4f1a2b9d7e
Revises: 7b3e012cf5a6
Create Date: 2026-06-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8c4f1a2b9d7e'
down_revision = '7b3e012cf5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'model_group_members',
        sa.Column('is_metadata_source', sa.Boolean(), nullable=False, server_default='false')
    )


def downgrade() -> None:
    op.drop_column('model_group_members', 'is_metadata_source')
