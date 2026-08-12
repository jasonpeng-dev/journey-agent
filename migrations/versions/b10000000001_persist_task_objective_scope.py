"""persist task objective scope

Revision ID: b10000000001
Revises: 9f3c2d8a4b71
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "b10000000001"
down_revision: str | None = "9f3c2d8a4b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column(
            "objective_resolution_status",
            sa.String(length=30),
            nullable=False,
            server_default="UNRESOLVED",
        ),
    )
    op.add_column("agent_tasks", sa.Column("objective_scope_keys", sa.JSON(), nullable=True))
    op.add_column(
        "agent_tasks", sa.Column("objective_catalog_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("objective_resolver_source", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("objective_resolver_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("objective_resolution_metadata", sa.JSON(), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("objective_resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("objective_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_tasks",
        sa.Column("objective_confirmation_source", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "agent_tasks", sa.Column("objective_frozen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_tasks", sa.Column("objective_freeze_source", sa.String(length=100), nullable=True)
    )

    # Only rows that predate this migration receive the compatibility scope.
    now = datetime.now(UTC)
    op.execute(
        sa.text(
            """
            UPDATE agent_tasks
            SET objective_resolution_status = 'CONFIRMED',
                objective_scope_keys = :scope_keys,
                objective_catalog_version = 'starfire-objectives-v1',
                objective_resolver_source = 'LEGACY_MIGRATION',
                objective_resolver_version = :migration_version,
                objective_resolution_metadata = :metadata,
                objective_resolved_at = :now,
                objective_confirmed_at = :now,
                objective_confirmation_source = 'LEGACY_MIGRATION',
                objective_frozen_at = :now,
                objective_freeze_source = 'LEGACY_MIGRATION'
            WHERE scenario_key = 'starfire_command'
            """
        ).bindparams(
            sa.bindparam(
                "scope_keys",
                value=["FULL_NORTHERN_RECOVERY"],
                type_=sa.JSON(),
            ),
            sa.bindparam("migration_version", value=revision, type_=sa.String()),
            sa.bindparam(
                "metadata",
                value={"legacy_compatibility": True},
                type_=sa.JSON(),
            ),
            sa.bindparam("now", value=now, type_=sa.DateTime(timezone=True)),
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.drop_column("objective_freeze_source")
        batch_op.drop_column("objective_frozen_at")
        batch_op.drop_column("objective_confirmation_source")
        batch_op.drop_column("objective_confirmed_at")
        batch_op.drop_column("objective_resolved_at")
        batch_op.drop_column("objective_resolution_metadata")
        batch_op.drop_column("objective_resolver_version")
        batch_op.drop_column("objective_resolver_source")
        batch_op.drop_column("objective_catalog_version")
        batch_op.drop_column("objective_scope_keys")
        batch_op.drop_column("objective_resolution_status")
