"""Phase D5 game product lifecycle

Revision ID: d50000000001
Revises: r90000000001
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "d50000000001"
down_revision: str | None = "r90000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_agent_tasks_instance_active",
        "agent_tasks",
        ["game_instance_id"],
        unique=True,
        sqlite_where=text(
            "status IN ('ACTIVE','REQUIRES_PLAYER_DECISION',"
            "'WAITING_FOR_PLAYER_ACTION','WAITING_FOR_WORLD_EVENT')"
        ),
        postgresql_where=text(
            "status IN ('ACTIVE','REQUIRES_PLAYER_DECISION',"
            "'WAITING_FOR_PLAYER_ACTION','WAITING_FOR_WORLD_EVENT')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_tasks_instance_active", table_name="agent_tasks")
