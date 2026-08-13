"""add generic memory events

Revision ID: r40000000001
Revises: r30000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r40000000001"
down_revision: str | None = "r30000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_instance_memory_events",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("actor_key", sa.String(length=80), nullable=True),
        sa.Column("event_key", sa.String(length=80), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_rule_key", sa.String(length=80), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_instance_memory_actor_created",
        "game_instance_memory_events",
        ["game_instance_id", "actor_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_instance_memory_actor_created", table_name="game_instance_memory_events")
    op.drop_table("game_instance_memory_events")
