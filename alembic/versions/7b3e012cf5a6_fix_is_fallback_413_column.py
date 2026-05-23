"""fix is_fallback_413 column

Revision ID: 7b3e012cf5a6
Revises: 6a2d901be3f4
Create Date: 2026-05-24 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7b3e012cf5a6'
down_revision = '6a2d901be3f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the wrong column if it exists (string fallback_413_model)
    # and add the correct column (boolean is_fallback_413)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('model_group_members')]
    
    if 'fallback_413_model' in cols:
        op.drop_column('model_group_members', 'fallback_413_model')
    
    if 'is_fallback_413' not in cols:
        op.add_column(
            'model_group_members',
            sa.Column('is_fallback_413', sa.Boolean(), nullable=False, server_default='false')
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('model_group_members')]
    
    if 'is_fallback_413' in cols:
        op.drop_column('model_group_members', 'is_fallback_413')
    
    if 'fallback_413_model' not in cols:
        op.add_column(
            'model_group_members',
            sa.Column('fallback_413_model', sa.String(255), nullable=True)
        )
