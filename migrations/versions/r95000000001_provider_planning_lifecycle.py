"""Persist provider planning failure detail on AgentTask.

Revision ID: r95000000001
Revises: r94000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r95000000001"
down_revision: str | None = "r94000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("last_error_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_tasks", "last_error_detail")
