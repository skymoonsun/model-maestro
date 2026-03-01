"""add is_exact_match

Revision ID: 005
Revises: 004
Create Date: 2026-03-01 01:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('model_config', sa.Column('is_exact_match', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('model_config', 'is_exact_match')
