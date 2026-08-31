"""Persist Provider planning cycles and individual attempts.

Revision ID: r97000000001
Revises: r96000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r97000000001"
down_revision: str | None = "r96000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_cycles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("base_call_type", sa.String(length=20), nullable=False),
        sa.Column("replan_reason", sa.String(length=160), nullable=True),
        sa.Column("frozen_objective_scope", sa.JSON(), nullable=False),
        sa.Column("planner_input", sa.JSON(), nullable=False),
        sa.Column("planner_input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="RUNNING"),
        sa.Column("current_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_segment", sa.JSON(), nullable=True),
        sa.Column("current_violations", sa.JSON(), nullable=False),
        sa.Column("anti_regression_memory", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_planning_cycles_task_status", "planning_cycles", ["task_id", "status"])
    op.create_index("ix_planning_cycles_task_created", "planning_cycles", ["task_id", "created_at"])
    op.create_table(
        "planning_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("call_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="RUNNING"),
        sa.Column("provider_payload", sa.JSON(), nullable=True),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("rejected_segment", sa.JSON(), nullable=True),
        sa.Column("validator_violations", sa.JSON(), nullable=False),
        sa.Column("anti_regression_memory", sa.JSON(), nullable=False),
        sa.Column("stop_reason", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("finish_reason", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["cycle_id"], ["planning_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "attempt_index"),
    )
    op.create_index(
        "ix_planning_attempts_cycle_attempt",
        "planning_attempts",
        ["cycle_id", "attempt_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_planning_attempts_cycle_attempt", table_name="planning_attempts")
    op.drop_table("planning_attempts")
    op.drop_index("ix_planning_cycles_task_created", table_name="planning_cycles")
    op.drop_index("ix_planning_cycles_task_status", table_name="planning_cycles")
    op.drop_table("planning_cycles")
