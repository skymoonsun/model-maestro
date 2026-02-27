"""Add capabilities to model_mappings

Revision ID: 004
Revises: 003
Create Date: 2026-02-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'model_mappings',
        sa.Column('capabilities', ARRAY(sa.String()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('model_mappings', 'capabilities')
