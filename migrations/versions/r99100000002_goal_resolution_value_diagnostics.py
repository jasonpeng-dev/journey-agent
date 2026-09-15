"""Persist safe Dynamic Goal candidate type diagnostics.

Revision ID: r99100000002
Revises: r99100000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99100000002"
down_revision: str | None = "r99100000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "goal_resolution_attempts",
        sa.Column(
            "value_type_diagnostics",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("goal_resolution_attempts", "value_type_diagnostics")
