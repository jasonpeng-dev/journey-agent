"""Persist generic Actor command reachability.

Revision ID: r92000000001
Revises: r91000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r92000000001"
down_revision: str | None = "r91000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game_instance_actors",
        sa.Column(
            "command_reachability",
            sa.String(length=20),
            server_default="ONLINE",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("game_instance_actors", "command_reachability")
