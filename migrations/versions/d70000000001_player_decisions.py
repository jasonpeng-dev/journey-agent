"""Phase D7 player decision constraints

Revision ID: d70000000001
Revises: d60000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d70000000001"
down_revision: str | None = "d60000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(
            sa.Column(
                "rejected_proposal_signatures",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_column("rejected_proposal_signatures")
