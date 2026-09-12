"""Add durable Goal parse idempotency and presentation.

Revision ID: r99100000005
Revises: r99100000004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99100000005"
down_revision: str | None = "r99100000004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "goal_resolution_attempts",
        sa.Column("submission_idempotency_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "goal_resolution_attempts",
        sa.Column("presentation_text", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_goal_resolution_attempts_instance_submission_key",
        "goal_resolution_attempts",
        ["game_instance_id", "submission_idempotency_key"],
        unique=True,
        sqlite_where=sa.text("submission_idempotency_key IS NOT NULL"),
        postgresql_where=sa.text("submission_idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_goal_resolution_attempts_instance_submission_key",
        table_name="goal_resolution_attempts",
    )
    op.drop_column("goal_resolution_attempts", "presentation_text")
    op.drop_column("goal_resolution_attempts", "submission_idempotency_key")
