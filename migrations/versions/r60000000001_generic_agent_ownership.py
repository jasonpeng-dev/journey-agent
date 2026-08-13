"""generic agent ownership

Revision ID: r60000000001
Revises: r50000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r60000000001"
down_revision: str | None = "r50000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.alter_column("owner_npc_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column("owner_actor_key", sa.String(length=80), nullable=True))
    with op.batch_alter_table("agent_plans") as batch_op:
        batch_op.add_column(sa.Column("created_by_actor_key", sa.String(length=80), nullable=True))
    with op.batch_alter_table("agent_steps") as batch_op:
        batch_op.add_column(sa.Column("assigned_actor_key", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_steps") as batch_op:
        batch_op.drop_column("assigned_actor_key")
    with op.batch_alter_table("agent_plans") as batch_op:
        batch_op.drop_column("created_by_actor_key")
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.drop_column("owner_actor_key")
        batch_op.alter_column("owner_npc_id", existing_type=sa.Uuid(), nullable=False)
