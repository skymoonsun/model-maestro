"""add user_nodes and user_node_models tables

Revision ID: b13a5d7b00f7
Revises: f6c9b3e4f2d5
Create Date: 2026-05-11 20:26:29.230814

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b13a5d7b00f7'
down_revision = 'f6c9b3e4f2d5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user_nodes table
    op.create_table(
        'user_nodes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['node_id'], ['ollama_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'node_id', name='uq_user_node')
    )
    op.create_index(op.f('ix_user_nodes_id'), 'user_nodes', ['id'], unique=False)

    # Create user_node_models table
    op.create_table(
        'user_node_models',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('model_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['node_id'], ['ollama_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'node_id', 'model_name', name='uq_user_node_model')
    )
    op.create_index(op.f('ix_user_node_models_id'), 'user_node_models', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_node_models_id'), table_name='user_node_models')
    op.drop_table('user_node_models')
    op.drop_index(op.f('ix_user_nodes_id'), table_name='user_nodes')
    op.drop_table('user_nodes')

