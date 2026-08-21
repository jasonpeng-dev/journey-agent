"""Persist PlanSegment stop reason and Planner step identity.

Revision ID: r96000000001
Revises: r95000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r96000000001"
down_revision: str | None = "r95000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_plans",
        sa.Column(
            "stop_reason",
            sa.String(length=40),
            nullable=False,
            server_default="OBJECTIVE_COMPLETION",
        ),
    )
    op.add_column(
        "agent_steps",
        sa.Column("planner_step_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_steps", "planner_step_id")
    op.drop_column("agent_plans", "stop_reason")
