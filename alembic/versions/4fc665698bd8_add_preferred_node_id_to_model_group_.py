"""add preferred_node_id to model_group_members

Revision ID: 4fc665698bd8
Revises: 008_activity_log_enhancements
Create Date: 2026-05-03 01:28:00.258003

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4fc665698bd8'
down_revision = '008_activity_log_enhancements'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add preferred_node_id column to model_group_members
    op.add_column('model_group_members', sa.Column('preferred_node_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_model_group_members_preferred_node_id'), 'model_group_members', ['preferred_node_id'], unique=False)
    op.create_foreign_key(None, 'model_group_members', 'ollama_nodes', ['preferred_node_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # Remove preferred_node_id column from model_group_members
    op.drop_constraint(None, 'model_group_members', type_='foreignkey')
    op.drop_index(op.f('ix_model_group_members_preferred_node_id'), table_name='model_group_members')
    op.drop_column('model_group_members', 'preferred_node_id')
