"""multi-node for model group members and model mappings

Revision ID: a1b2c3d4e5f6
Revises: aa95609e00c5
Create Date: 2026-05-13

"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "aa95609e00c5"
branch_labels = None
depends_on = None


def _drop_foreign_key_on_column(table_name: str, column_name: str) -> None:
    """Drop a single-column FK when the constraint was created without a name (PostgreSQL)."""
    conn = op.get_bind()
    res = conn.execute(
        sa.text(
            """
            SELECT tc.constraint_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name = :table
              AND kcu.column_name = :column
            """
        ),
        {"table": table_name, "column": column_name},
    )
    row = res.fetchone()
    if row:
        cname = row[0]
        conn.execute(
            sa.text(
                f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{cname}"'
            )
        )


def upgrade() -> None:
    op.create_table(
        "model_group_member_nodes",
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["model_group_members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["ollama_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("member_id", "node_id"),
    )
    op.create_index(
        op.f("ix_model_group_member_nodes_node_id"),
        "model_group_member_nodes",
        ["node_id"],
        unique=False,
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO model_group_member_nodes (member_id, node_id)
            SELECT id, preferred_node_id
            FROM model_group_members
            WHERE preferred_node_id IS NOT NULL
            """
        )
    )

    _drop_foreign_key_on_column("model_group_members", "preferred_node_id")
    op.drop_index(op.f("ix_model_group_members_preferred_node_id"), table_name="model_group_members")
    op.drop_column("model_group_members", "preferred_node_id")

    op.create_table(
        "model_mapping_nodes",
        sa.Column("mapping_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["mapping_id"], ["model_mappings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["ollama_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("mapping_id", "node_id"),
    )
    op.create_index(
        op.f("ix_model_mapping_nodes_node_id"),
        "model_mapping_nodes",
        ["node_id"],
        unique=False,
    )

    conn.execute(
        sa.text(
            """
            INSERT INTO model_mapping_nodes (mapping_id, node_id)
            SELECT id, node_id
            FROM model_mappings
            WHERE node_id IS NOT NULL
            """
        )
    )

    op.drop_constraint(
        "fk_model_mappings_node_id_ollama_nodes",
        "model_mappings",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_model_mappings_node_id"), table_name="model_mappings")
    op.drop_column("model_mappings", "node_id")


def downgrade() -> None:
    op.add_column(
        "model_mappings",
        sa.Column("node_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_model_mappings_node_id_ollama_nodes",
        "model_mappings",
        "ollama_nodes",
        ["node_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_model_mappings_node_id"), "model_mappings", ["node_id"], unique=False
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE model_mappings AS m
            SET node_id = sub.node_id
            FROM (
                SELECT DISTINCT ON (mapping_id) mapping_id, node_id
                FROM model_mapping_nodes
                ORDER BY mapping_id, node_id
            ) AS sub
            WHERE m.id = sub.mapping_id
            """
        )
    )

    op.drop_index(op.f("ix_model_mapping_nodes_node_id"), table_name="model_mapping_nodes")
    op.drop_table("model_mapping_nodes")

    op.add_column(
        "model_group_members",
        sa.Column("preferred_node_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_model_group_members_preferred_node_id_ollama_nodes",
        "model_group_members",
        "ollama_nodes",
        ["preferred_node_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_model_group_members_preferred_node_id"),
        "model_group_members",
        ["preferred_node_id"],
        unique=False,
    )

    conn.execute(
        sa.text(
            """
            UPDATE model_group_members AS mgm
            SET preferred_node_id = sub.node_id
            FROM (
                SELECT DISTINCT ON (member_id) member_id, node_id
                FROM model_group_member_nodes
                ORDER BY member_id, node_id
            ) AS sub
            WHERE mgm.id = sub.member_id
            """
        )
    )

    op.drop_index(op.f("ix_model_group_member_nodes_node_id"), table_name="model_group_member_nodes")
    op.drop_table("model_group_member_nodes")
