"""scope runtime persistence to game instances

Revision ID: c50000000001
Revises: c40000000001
Create Date: 2026-08-12 00:00:00.000000

Legacy ownership columns intentionally remain nullable until the C8 backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c50000000001"
down_revision: str | None = "c40000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNED_TABLES = (
    "conversation_sessions",
    "memories",
    "agent_tasks",
    "world_operations",
    "player_decision_requests",
)


def upgrade() -> None:
    _create_instance_state_tables()
    for table_name in _OWNED_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("game_instance_id", sa.Uuid(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table_name}_game_instance",
                "game_instances",
                ["game_instance_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_index(
                f"ix_{table_name}_game_instance_id",
                ["game_instance_id"],
                unique=False,
            )
    op.create_index(
        "ix_agent_tasks_instance_status",
        "agent_tasks",
        ["game_instance_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_memories_instance_npc_importance",
        "memories",
        ["game_instance_id", "npc_id", "importance"],
        unique=False,
    )
    _replace_operation_idempotency_constraint()


def downgrade() -> None:
    op.drop_index("uq_world_operations_instance_idempotency", table_name="world_operations")
    op.drop_index("uq_world_operations_legacy_idempotency", table_name="world_operations")
    with op.batch_alter_table(
        "world_operations",
        naming_convention={
            "uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s",
        },
    ) as batch_op:
        batch_op.create_unique_constraint(
            "uq_world_operations_player_id_idempotency_key",
            ["player_id", "idempotency_key"],
        )
    op.drop_index("ix_memories_instance_npc_importance", table_name="memories")
    op.drop_index("ix_agent_tasks_instance_status", table_name="agent_tasks")
    for table_name in reversed(_OWNED_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"ix_{table_name}_game_instance_id")
            batch_op.drop_constraint(
                f"fk_{table_name}_game_instance",
                type_="foreignkey",
            )
            batch_op.drop_column("game_instance_id")
    op.drop_table("game_instance_officer_appointments")
    op.drop_table("game_instance_world_facts")
    op.drop_table("game_instance_resource_states")
    op.drop_table("game_instance_fact_states")
    op.drop_table("game_instance_node_states")


def _create_instance_state_tables() -> None:
    op.create_table(
        "game_instance_node_states",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("node_key", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("visibility", sa.String(length=30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_instance_id", "node_key"),
    )
    op.create_table(
        "game_instance_fact_states",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("node_key", sa.String(length=80), nullable=False),
        sa.Column("fact_key", sa.String(length=80), nullable=False),
        sa.Column("truth_value", sa.JSON(), nullable=False),
        sa.Column("visibility", sa.String(length=30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_instance_id", "node_key", "fact_key"),
    )
    op.create_table(
        "game_instance_resource_states",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("resource_key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("reserved_value", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("value >= 0", name="ck_instance_resource_value"),
        sa.CheckConstraint(
            "reserved_value >= 0 AND reserved_value <= value",
            name="ck_instance_resource_reserved",
        ),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_instance_id", "resource_key"),
    )
    op.create_table(
        "game_instance_world_facts",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_instance_id", "key"),
    )
    op.create_table(
        "game_instance_officer_appointments",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("npc_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("authority_overrides", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["npc_id"], ["npcs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_instance_id", "npc_id"),
    )


def _replace_operation_idempotency_constraint() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(
            "world_operations",
            naming_convention={
                "uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s",
            },
        ) as batch_op:
            batch_op.drop_constraint(
                "uq_world_operations_player_id_idempotency_key",
                type_="unique",
            )
    elif dialect == "postgresql":
        op.drop_constraint(
            "world_operations_player_id_idempotency_key_key",
            "world_operations",
            type_="unique",
        )
    else:
        raise RuntimeError(f"Runtime idempotency migration is unsupported on {dialect}")
    op.create_index(
        "uq_world_operations_legacy_idempotency",
        "world_operations",
        ["player_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text("game_instance_id IS NULL"),
        postgresql_where=sa.text("game_instance_id IS NULL"),
    )
    op.create_index(
        "uq_world_operations_instance_idempotency",
        "world_operations",
        ["game_instance_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text("game_instance_id IS NOT NULL"),
        postgresql_where=sa.text("game_instance_id IS NOT NULL"),
    )
