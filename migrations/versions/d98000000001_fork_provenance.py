"""Add immutable GameInstance Fork provenance.

Revision ID: d98000000001
Revises: r97000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d98000000001"
down_revision: str | None = "r97000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("game_instances", recreate="always") as batch_op:
            batch_op.add_column(
                sa.Column("forked_from_game_instance_id", sa.Uuid(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_game_instances_forked_from_game_instance",
                "game_instances",
                ["forked_from_game_instance_id"],
                ["id"],
                ondelete="SET NULL",
            )
        _restore_sqlite_game_instance_triggers()
    else:
        op.add_column(
            "game_instances",
            sa.Column("forked_from_game_instance_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_game_instances_forked_from_game_instance",
            "game_instances",
            "game_instances",
            ["forked_from_game_instance_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_game_instances_forked_from_game_instance_id",
        "game_instances",
        ["forked_from_game_instance_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_game_instances_forked_from_game_instance_id",
        table_name="game_instances",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("game_instances", recreate="always") as batch_op:
            batch_op.drop_column("forked_from_game_instance_id")
        _restore_sqlite_game_instance_triggers()
    else:
        op.drop_constraint(
            "fk_game_instances_forked_from_game_instance",
            "game_instances",
            type_="foreignkey",
        )
        op.drop_column("game_instances", "forked_from_game_instance_id")


def _restore_sqlite_game_instance_triggers() -> None:
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
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS game_instances_reject_creation_key_update
        BEFORE UPDATE OF creation_key ON game_instances
        WHEN OLD.creation_key IS NOT NULL AND NEW.creation_key IS NOT OLD.creation_key
        BEGIN
            SELECT RAISE(ABORT, 'GameInstance creation binding is immutable');
        END
        """
    )
