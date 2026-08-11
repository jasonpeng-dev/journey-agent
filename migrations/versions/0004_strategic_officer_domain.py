"""add strategic officer assignments, decisions, and world operations

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("npcs") as batch:
        batch.add_column(
            sa.Column(
                "doctrine",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "authority_limits",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1")
        )

    op.create_table(
        "officer_appointments",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("npc_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "authority_overrides",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["npc_id"], ["npcs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_id", "npc_id"),
    )
    op.create_table(
        "player_domain_states",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("soldiers_total", sa.Integer(), nullable=False),
        sa.Column("soldiers_committed", sa.Integer(), nullable=False),
        sa.Column("food", sa.Integer(), nullable=False),
        sa.Column("morale", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("food >= 0", name="ck_domain_food"),
        sa.CheckConstraint(
            "morale >= 0 AND morale <= 100",
            name="ck_domain_morale",
        ),
        sa.CheckConstraint(
            "soldiers_committed >= 0 AND soldiers_committed <= soldiers_total",
            name="ck_domain_soldiers_committed",
        ),
        sa.CheckConstraint("soldiers_total >= 0", name="ck_domain_soldiers_total"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_id"),
    )

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("actor_npc_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("officer_profile_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("authority_policy_version", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_agent_runs_actor_npc_id",
            "npcs",
            ["actor_npc_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("agent_tasks") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=30),
            existing_nullable=False,
        )

    with op.batch_alter_table("agent_plans") as batch:
        batch.add_column(sa.Column("created_by_npc_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_agent_plans_created_by_npc_id",
            "npcs",
            ["created_by_npc_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("agent_steps") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=30),
            existing_nullable=False,
        )
        batch.alter_column(
            "execution_type",
            existing_type=sa.String(length=13),
            type_=sa.String(length=30),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("assigned_npc_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("action_intent", sa.String(length=100), nullable=True))
        batch.add_column(
            sa.Column(
                "constraints",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "allowed_tool_names",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.create_foreign_key(
            "fk_agent_steps_assigned_npc_id",
            "npcs",
            ["assigned_npc_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_agent_steps_assigned_npc_id", ["assigned_npc_id"])

    # Preserve the actor attribution of records created before the officer model
    # existed. Sessions remain owned by the commanding NPC; assigned officers only
    # switch internally at step execution time.
    op.execute(
        sa.text(
            """
            UPDATE agent_runs
            SET actor_npc_id = (
                SELECT conversation_sessions.npc_id
                FROM conversation_sessions
                WHERE conversation_sessions.id = agent_runs.session_id
            )
            WHERE actor_npc_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_steps
            SET assigned_npc_id = (
                SELECT agent_tasks.owner_npc_id
                FROM agent_plans
                JOIN agent_tasks ON agent_tasks.id = agent_plans.task_id
                WHERE agent_plans.id = agent_steps.plan_id
            )
            WHERE assigned_npc_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_plans
            SET created_by_npc_id = COALESCE(
                (
                    SELECT agent_runs.actor_npc_id
                    FROM agent_runs
                    WHERE agent_runs.id = agent_plans.created_by_run_id
                ),
                (
                    SELECT agent_tasks.owner_npc_id
                    FROM agent_tasks
                    WHERE agent_tasks.id = agent_plans.task_id
                )
            )
            WHERE created_by_npc_id IS NULL
            """
        )
    )

    with op.batch_alter_table("tool_executions") as batch:
        batch.add_column(sa.Column("authority_details", sa.JSON(), nullable=True))

    op.create_table(
        "world_operations",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("source_step_id", sa.Uuid(), nullable=True),
        sa.Column("officer_npc_id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("target_key", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RESOLVED", name="worldoperationstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("resolution_key", sa.String(length=160), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["officer_npc_id"], ["npcs.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_step_id"], ["agent_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "idempotency_key"),
    )
    op.create_index(
        "ix_world_operations_task_status",
        "world_operations",
        ["task_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_world_operations_source_step_id",
        "world_operations",
        ["source_step_id"],
        unique=False,
    )
    op.create_index(
        "ix_world_operations_task_id",
        "world_operations",
        ["task_id"],
        unique=False,
    )

    op.create_table(
        "player_decision_requests",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_npc_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                "CONSUMED",
                name="decisionstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("decision_kind", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("action_tool_name", sa.String(length=100), nullable=False),
        sa.Column("action_arguments", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("selected_option", sa.String(length=80), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_npc_id"],
            ["npcs.id"],
        ),
        sa.ForeignKeyConstraint(["step_id"], ["agent_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_decisions_task_status",
        "player_decision_requests",
        ["task_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_decisions_task_status", table_name="player_decision_requests")
    op.drop_table("player_decision_requests")
    op.drop_index("ix_world_operations_task_id", table_name="world_operations")
    op.drop_index("ix_world_operations_source_step_id", table_name="world_operations")
    op.drop_index("ix_world_operations_task_status", table_name="world_operations")
    op.drop_table("world_operations")

    with op.batch_alter_table("tool_executions") as batch:
        batch.drop_column("authority_details")
    with op.batch_alter_table("agent_steps") as batch:
        batch.drop_index("ix_agent_steps_assigned_npc_id")
        batch.drop_constraint("fk_agent_steps_assigned_npc_id", type_="foreignkey")
        batch.drop_column("allowed_tool_names")
        batch.drop_column("constraints")
        batch.drop_column("action_intent")
        batch.drop_column("assigned_npc_id")
        batch.alter_column(
            "execution_type",
            existing_type=sa.String(length=30),
            type_=sa.String(length=13),
            existing_nullable=False,
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=30),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
    with op.batch_alter_table("agent_plans") as batch:
        batch.drop_constraint("fk_agent_plans_created_by_npc_id", type_="foreignkey")
        batch.drop_column("created_by_npc_id")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("fk_agent_runs_actor_npc_id", type_="foreignkey")
        batch.drop_column("authority_policy_version")
        batch.drop_column("officer_profile_version")
        batch.drop_column("actor_npc_id")
    with op.batch_alter_table("agent_tasks") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=30),
            type_=sa.String(length=16),
            existing_nullable=False,
        )

    op.drop_table("player_domain_states")
    op.drop_table("officer_appointments")
    with op.batch_alter_table("npcs") as batch:
        batch.drop_column("profile_version")
        batch.drop_column("authority_limits")
        batch.drop_column("doctrine")
