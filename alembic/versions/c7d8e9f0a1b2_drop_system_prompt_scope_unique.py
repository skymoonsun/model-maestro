"""allow multiple system prompts per (scope_type, scope_value)

Drops the uq_system_prompt_scope unique constraint so several prompts can
target the same scope and stack in priority order (drag-and-drop reorder).

Revision ID: c7d8e9f0a1b2
Revises: a9b8c7d6e5f4
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c7d8e9f0a1b2'
down_revision = 'a9b8c7d6e5f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('uq_system_prompt_scope', 'system_prompts', type_='unique')


def downgrade() -> None:
    op.create_unique_constraint(
        'uq_system_prompt_scope', 'system_prompts', ['scope_type', 'scope_value']
    )
