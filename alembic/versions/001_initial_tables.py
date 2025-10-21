"""Initial tables for users, model_mappings, and user_models

Revision ID: 001
Revises: 
Create Date: 2024-10-21 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    # Create model_mappings table
    op.create_table(
        'model_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('real_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_mappings_id'), 'model_mappings', ['id'], unique=False)
    op.create_index(op.f('ix_model_mappings_display_name'), 'model_mappings', ['display_name'], unique=True)

    # Create user_models table
    op.create_table(
        'user_models',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('model_display_name', sa.String(length=255), nullable=True),
        sa.Column('has_all_models', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'model_display_name', name='uq_user_model')
    )
    op.create_index(op.f('ix_user_models_id'), 'user_models', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_models_id'), table_name='user_models')
    op.drop_table('user_models')
    op.drop_index(op.f('ix_model_mappings_display_name'), table_name='model_mappings')
    op.drop_index(op.f('ix_model_mappings_id'), table_name='model_mappings')
    op.drop_table('model_mappings')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_table('users')

