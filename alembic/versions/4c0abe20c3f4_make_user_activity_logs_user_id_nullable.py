"""make user_activity_logs user_id nullable for anonymous error logging

Revision ID: 4c0abe20c3f4
Revises: b13a5d7b00f7
Create Date: 2026-05-18

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4c0abe20c3f4'
down_revision = 'b8afba3b73f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('user_activity_logs', 'user_id',
                    existing_type=sa.Integer(),
                    nullable=True)


def downgrade() -> None:
    op.alter_column('user_activity_logs', 'user_id',
                    existing_type=sa.Integer(),
                    nullable=False)
