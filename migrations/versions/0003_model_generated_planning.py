"""add model-generated planning metadata

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(
            sa.Column(
                "planning_mode",
                sa.String(length=40),
                nullable=False,
                server_default="DETERMINISTIC_BASELINE",
            )
        )
    with op.batch_alter_table("agent_plans") as batch:
        batch.add_column(
            sa.Column(
                "source",
                sa.String(length=40),
                nullable=False,
                server_default="DETERMINISTIC_BASELINE",
            )
        )
        batch.add_column(sa.Column("planner_model", sa.String(length=100), nullable=True))
        batch.add_column(
            sa.Column(
                "validation_status",
                sa.String(length=30),
                nullable=False,
                server_default="PASSED",
            )
        )
        batch.add_column(
            sa.Column(
                "validation_errors",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(
            sa.Column(
                "purpose",
                sa.String(length=30),
                nullable=False,
                server_default="CONVERSATION",
            )
        )
        batch.add_column(sa.Column("structured_output", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("validation_status", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("validation_errors", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("validation_errors")
        batch.drop_column("validation_status")
        batch.drop_column("structured_output")
        batch.drop_column("purpose")
    with op.batch_alter_table("agent_plans") as batch:
        batch.drop_column("validation_errors")
        batch.drop_column("validation_status")
        batch.drop_column("planner_model")
        batch.drop_column("source")
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_column("planning_mode")
