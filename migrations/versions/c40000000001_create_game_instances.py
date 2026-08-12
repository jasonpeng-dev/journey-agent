"""create game instances

Revision ID: c40000000001
Revises: c30000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c40000000001"
down_revision: str | None = "c30000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_instances",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_INITIALIZATION",
                "ACTIVE",
                "SUSPENDED",
                "COMPLETED",
                "FAILED",
                name="gameinstancestatus",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("current_node_key", sa.String(length=80), nullable=True),
        sa.Column("runtime_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "runtime_revision >= 0",
            name="ck_game_instance_runtime_revision",
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["scenario_version_id"],
            ["scenario_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_game_instances_player_status",
        "game_instances",
        ["player_id", "status"],
        unique=False,
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER game_instances_reject_binding_update
            BEFORE UPDATE OF player_id, scenario_version_id ON game_instances
            WHEN NEW.player_id != OLD.player_id
              OR NEW.scenario_version_id != OLD.scenario_version_id
            BEGIN
                SELECT RAISE(ABORT, 'GameInstance bindings are immutable');
            END
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_game_instance_binding_drift()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.player_id != OLD.player_id
                   OR NEW.scenario_version_id != OLD.scenario_version_id THEN
                    RAISE EXCEPTION 'GameInstance bindings are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER game_instances_reject_binding_update
            BEFORE UPDATE ON game_instances
            FOR EACH ROW EXECUTE FUNCTION reject_game_instance_binding_drift()
            """
        )
    else:
        raise RuntimeError(f"GameInstance binding protection is unsupported on {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER game_instances_reject_binding_update")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER game_instances_reject_binding_update ON game_instances")
        op.execute("DROP FUNCTION reject_game_instance_binding_drift()")
    else:
        raise RuntimeError(f"GameInstance binding protection is unsupported on {dialect}")
    op.drop_index("ix_game_instances_player_status", table_name="game_instances")
    op.drop_table("game_instances")
