"""Phase D6 formal Play idempotency

Revision ID: d60000000001
Revises: d50000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d60000000001"
down_revision: str | None = "d50000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(sa.Column("submission_idempotency_key", sa.String(160), nullable=True))
        batch.create_unique_constraint(
            "uq_agent_tasks_instance_submission_key",
            ["game_instance_id", "submission_idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_constraint("uq_agent_tasks_instance_submission_key", type_="unique")
        batch.drop_column("submission_idempotency_key")
