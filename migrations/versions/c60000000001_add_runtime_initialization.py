"""add runtime initialization idempotency

Revision ID: c60000000001
Revises: c50000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c60000000001"
down_revision: str | None = "c50000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("game_instances") as batch_op:
        batch_op.add_column(sa.Column("creation_key", sa.String(length=160), nullable=True))
    op.create_index(
        "uq_game_instances_player_creation_key",
        "game_instances",
        ["player_id", "creation_key"],
        unique=True,
        sqlite_where=sa.text("creation_key IS NOT NULL"),
        postgresql_where=sa.text("creation_key IS NOT NULL"),
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _create_sqlite_binding_trigger()
        op.execute(
            """
            CREATE TRIGGER game_instances_reject_creation_key_update
            BEFORE UPDATE OF creation_key ON game_instances
            WHEN OLD.creation_key IS NOT NULL AND NEW.creation_key IS NOT OLD.creation_key
            BEGIN
                SELECT RAISE(ABORT, 'GameInstance creation binding is immutable');
            END
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_game_instance_creation_key_drift()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.creation_key IS NOT NULL
                   AND NEW.creation_key IS DISTINCT FROM OLD.creation_key THEN
                    RAISE EXCEPTION 'GameInstance creation binding is immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER game_instances_reject_creation_key_update
            BEFORE UPDATE ON game_instances
            FOR EACH ROW EXECUTE FUNCTION reject_game_instance_creation_key_drift()
            """
        )
    else:
        raise RuntimeError(f"Runtime initialization is unsupported on {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER game_instances_reject_creation_key_update")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER game_instances_reject_creation_key_update ON game_instances")
        op.execute("DROP FUNCTION reject_game_instance_creation_key_drift()")
    else:
        raise RuntimeError(f"Runtime initialization is unsupported on {dialect}")
    op.drop_index(
        "uq_game_instances_player_creation_key",
        table_name="game_instances",
    )
    with op.batch_alter_table("game_instances") as batch_op:
        batch_op.drop_column("creation_key")
    if dialect == "sqlite":
        _create_sqlite_binding_trigger()


def _create_sqlite_binding_trigger() -> None:
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS game_instances_reject_binding_update
        BEFORE UPDATE OF player_id, scenario_version_id ON game_instances
        WHEN NEW.player_id != OLD.player_id
          OR NEW.scenario_version_id != OLD.scenario_version_id
        BEGIN
            SELECT RAISE(ABORT, 'GameInstance bindings are immutable');
        END
        """
    )
