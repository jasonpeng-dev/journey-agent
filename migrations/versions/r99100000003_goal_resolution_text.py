"""Persist redacted Goal text for resolution diagnostics.

Revision ID: r99100000003
Revises: r99100000002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99100000003"
down_revision: str | None = "r99100000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "goal_resolution_attempts",
        sa.Column("original_goal_text", sa.String(length=4000), nullable=True),
    )
    op.add_column(
        "goal_resolution_attempts",
        sa.Column("normalized_goal_text", sa.String(length=4000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("goal_resolution_attempts", "normalized_goal_text")
    op.drop_column("goal_resolution_attempts", "original_goal_text")
