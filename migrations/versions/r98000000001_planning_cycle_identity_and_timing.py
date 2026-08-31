"""Persist planning-cycle timing and accepted-plan association.

Revision ID: r98000000001
Revises: e00000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r98000000001"
down_revision: str | None = "e00000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "planning_cycles",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "planning_cycles",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_plans",
        sa.Column("planning_cycle_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_agent_plans_planning_cycle",
        "agent_plans",
        ["planning_cycle_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_plans_planning_cycle", table_name="agent_plans")
    op.drop_column("agent_plans", "planning_cycle_id")
    op.drop_column("planning_cycles", "finished_at")
    op.drop_column("planning_cycles", "started_at")
