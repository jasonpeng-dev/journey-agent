"""add persistent task plan step hierarchy

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_world_facts",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_id", "key"),
    )
    op.create_table(
        "agent_tasks",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("owner_npc_id", sa.Uuid(), nullable=False),
        sa.Column("origin_session_id", sa.Uuid(), nullable=False),
        sa.Column("last_session_id", sa.Uuid(), nullable=False),
        sa.Column("goal_description", sa.Text(), nullable=False),
        sa.Column("scenario_key", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "WAITING_FOR_USER",
                "SUCCEEDED",
                "FAILED",
                "BLOCKED",
                name="agenttaskstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("current_plan_version", sa.Integer(), nullable=False),
        sa.Column("replan_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_session_id"], ["conversation_sessions.id"]),
        sa.ForeignKeyConstraint(["origin_session_id"], ["conversation_sessions.id"]),
        sa.ForeignKeyConstraint(["owner_npc_id"], ["npcs.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_tasks_player_status",
        "agent_tasks",
        ["player_id", "status"],
        unique=False,
    )
    op.create_table(
        "agent_plans",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "SUPERSEDED",
                "SUCCEEDED",
                "FAILED",
                name="agentplanstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("strategy_summary", sa.Text(), nullable=False),
        sa.Column("replan_reason", sa.String(length=160), nullable=True),
        sa.Column("supersedes_plan_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_run_id"],
            ["agent_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_plan_id"],
            ["agent_plans.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version"),
    )
    op.create_index(
        "ix_agent_plans_task_version",
        "agent_plans",
        ["task_id", "version"],
        unique=False,
    )
    op.create_table(
        "agent_steps",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "execution_type",
            sa.Enum("TOOL", "WAIT_FOR_USER", name="stepexecutiontype", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "IN_PROGRESS",
                "SUCCEEDED",
                "FAILED",
                "BLOCKED",
                "WAITING_FOR_USER",
                "SKIPPED",
                name="agentstepstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("selected_tool_name", sa.String(length=100), nullable=True),
        sa.Column("tool_arguments", sa.JSON(), nullable=False),
        sa.Column("expected_outcome", sa.JSON(), nullable=False),
        sa.Column("actual_result", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("resume_condition", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["agent_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "sequence"),
    )
    op.create_index(
        "ix_agent_steps_plan_sequence",
        "agent_steps",
        ["plan_id", "sequence"],
        unique=False,
    )
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("task_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("plan_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("step_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_agent_runs_task_id",
            "agent_tasks",
            ["task_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_agent_runs_plan_id",
            "agent_plans",
            ["plan_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_agent_runs_step_id",
            "agent_steps",
            ["step_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_agent_runs_task_id", ["task_id"], unique=False)
    with op.batch_alter_table("tool_executions") as batch:
        batch.add_column(sa.Column("step_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_tool_executions_step_id",
            "agent_steps",
            ["step_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_tool_executions_step_id", ["step_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("tool_executions") as batch:
        batch.drop_index("ix_tool_executions_step_id")
        batch.drop_constraint("fk_tool_executions_step_id", type_="foreignkey")
        batch.drop_column("step_id")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_task_id")
        batch.drop_constraint("fk_agent_runs_step_id", type_="foreignkey")
        batch.drop_constraint("fk_agent_runs_plan_id", type_="foreignkey")
        batch.drop_constraint("fk_agent_runs_task_id", type_="foreignkey")
        batch.drop_column("step_id")
        batch.drop_column("plan_id")
        batch.drop_column("task_id")
    op.drop_index("ix_agent_steps_plan_sequence", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index("ix_agent_plans_task_version", table_name="agent_plans")
    op.drop_table("agent_plans")
    op.drop_index("ix_agent_tasks_player_status", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_table("player_world_facts")
