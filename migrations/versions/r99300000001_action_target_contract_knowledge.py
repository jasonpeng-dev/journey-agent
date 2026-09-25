"""Persist generic Action target contract Knowledge per Game Instance.

Revision ID: r99300000001
Revises: r99200000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99300000001"
down_revision: str | None = "r99200000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_instance_action_target_knowledge",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("action_key", sa.String(length=80), nullable=False),
        sa.Column("target_key", sa.String(length=80), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("game_instance_id", "action_key", "target_key"),
    )


def downgrade() -> None:
    op.drop_table("game_instance_action_target_knowledge")
