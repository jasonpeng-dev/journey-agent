"""generic world operations

Revision ID: r50000000001
Revises: r40000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r50000000001"
down_revision: str | None = "r40000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("world_operations") as batch_op:
        batch_op.alter_column("officer_npc_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column("actor_key", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("action_key", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("execution_mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("world_operations") as batch_op:
        batch_op.drop_column("execution_mode")
        batch_op.drop_column("action_key")
        batch_op.drop_column("actor_key")
        batch_op.alter_column("officer_npc_id", existing_type=sa.Uuid(), nullable=False)
