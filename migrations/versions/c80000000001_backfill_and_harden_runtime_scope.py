"""backfill and harden runtime scope

Revision ID: c80000000001
Revises: c60000000001
Create Date: 2026-08-12 00:00:00.000000

The canonical built-in Starfire snapshot is the compatibility anchor for rows
created before ScenarioVersion and GameInstance existed.  It is deliberately
resolved by content hash, never by the current/latest published pointer.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op

from app.scenarios.documents import SCENARIO_DOCUMENT_SCHEMA_VERSION
from app.scenarios.builtin import STARFIRE_V2
from app.scenarios.serialization import canonical_document, scenario_content_hash

revision: str = "c80000000001"
down_revision: str | None = "c60000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNED_TABLES = (
    "conversation_sessions",
    "memories",
    "agent_tasks",
    "world_operations",
    "player_decision_requests",
)
_LEGACY_CREATION_KEY = "legacy-default"


def upgrade() -> None:
    bind = op.get_bind()
    version_id = _bootstrap_legacy_starfire_version(bind)
    instance_by_player = _create_default_instances(bind, version_id)
    _backfill_instance_state(bind, instance_by_player)
    _backfill_direct_owners(bind, instance_by_player)
    _validate_runtime_graph(bind)
    _tighten_ownership_columns()
    _replace_instance_triggers()


def downgrade() -> None:
    # A downgrade preserves the compatibility instances and copied state.  It
    # only relaxes constraints so the C5/C6 application can read the database.
    _drop_instance_triggers()
    with op.batch_alter_table("game_instances") as batch_op:
        batch_op.alter_column(
            "creation_key",
            existing_type=sa.String(length=160),
            nullable=True,
        )
    for table_name in _OWNED_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "game_instance_id",
                existing_type=sa.Uuid(),
                nullable=True,
            )
    op.create_index(
        "uq_world_operations_legacy_idempotency",
        "world_operations",
        ["player_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text("game_instance_id IS NULL"),
        postgresql_where=sa.text("game_instance_id IS NULL"),
    )
    _replace_instance_triggers()


def _tables(bind: sa.Connection, *names: str) -> dict[str, sa.Table]:
    metadata = sa.MetaData()
    return {name: sa.Table(name, metadata, autoload_with=bind) for name in names}


def _bootstrap_legacy_starfire_version(bind: sa.Connection) -> UUID:
    tables = _tables(bind, "scenarios", "scenario_drafts", "scenario_versions")
    scenarios = tables["scenarios"]
    drafts = tables["scenario_drafts"]
    versions = tables["scenario_versions"]
    draft_payload = STARFIRE_V2.model_dump(mode="json")
    canonical = canonical_document(draft_payload).model_dump(mode="json")
    content_hash = scenario_content_hash(canonical)
    now = datetime.now(UTC)
    row = (
        bind.execute(sa.select(scenarios).where(scenarios.c.key == "starfire_command"))
        .mappings()
        .one_or_none()
    )
    if row is None:
        scenario_id = uuid5(NAMESPACE_URL, "journey-agent:scenario:starfire_command")
        bind.execute(
            scenarios.insert().values(
                id=_db_uuid(bind, scenario_id),
                key="starfire_command",
                name=STARFIRE_V2.metadata.name,
                status="PUBLISHED",
                version=1,
                current_published_version_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        bind.execute(
            drafts.insert().values(
                scenario_id=_db_uuid(bind, scenario_id),
                revision=1,
                definition_document=draft_payload,
                validation_status="PASSED",
                validation_errors=[],
                content_hash=content_hash,
                base_scenario_version_id=None,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        scenario_id = row["id"]

    existing = (
        bind.execute(
            sa.select(versions).where(
                versions.c.scenario_id == _db_uuid(bind, scenario_id),
                versions.c.content_hash == content_hash,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        next_number = (
            bind.scalar(
                sa.select(sa.func.max(versions.c.version_number)).where(
                    versions.c.scenario_id == _db_uuid(bind, scenario_id)
                )
            )
            or 0
        ) + 1
        version_id = uuid5(
            NAMESPACE_URL,
            f"journey-agent:scenario-version:starfire_command:{content_hash}",
        )
        bind.execute(
            versions.insert().values(
                id=_db_uuid(bind, version_id),
                scenario_id=_db_uuid(bind, scenario_id),
                version_number=next_number,
                schema_version=SCENARIO_DOCUMENT_SCHEMA_VERSION,
                snapshot_document=canonical,
                content_hash=content_hash,
                behavior_bundle_key=canonical["engine_contract"]["key"],
                behavior_bundle_version=canonical["engine_contract"]["version"],
                published_at=now,
                created_at=now,
            )
        )
    else:
        version_id = existing["id"]

    # The pointer is authoring metadata only. Runtime backfill uses version_id
    # resolved above by exact content hash.
    if row is None:
        bind.execute(
            scenarios.update()
            .where(scenarios.c.id == _db_uuid(bind, scenario_id))
            .values(current_published_version_id=_db_uuid(bind, version_id))
        )
        bind.execute(
            drafts.update()
            .where(drafts.c.scenario_id == _db_uuid(bind, scenario_id))
            .values(base_scenario_version_id=_db_uuid(bind, version_id))
        )
    return UUID(str(version_id))


def _create_default_instances(bind: sa.Connection, version_id: UUID) -> dict[UUID, UUID]:
    tables = _tables(bind, "players", "world_nodes", "game_instances")
    players = tables["players"]
    nodes = tables["world_nodes"]
    instances = tables["game_instances"]
    now = datetime.now(UTC)
    node_keys = dict(bind.execute(sa.select(nodes.c.id, nodes.c.key)).all())
    result: dict[UUID, UUID] = {}
    for player in bind.execute(sa.select(players)).mappings():
        player_id = UUID(str(player["id"]))
        existing = (
            bind.execute(
                sa.select(instances).where(
                    instances.c.player_id == _db_uuid(bind, player_id),
                    instances.c.creation_key == _LEGACY_CREATION_KEY,
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            instance_id = uuid5(
                NAMESPACE_URL,
                f"journey-agent:legacy-game-instance:{player_id}",
            )
            bind.execute(
                instances.insert().values(
                    id=_db_uuid(bind, instance_id),
                    player_id=_db_uuid(bind, player_id),
                    scenario_version_id=_db_uuid(bind, version_id),
                    status="ACTIVE",
                    current_node_key=node_keys.get(player["current_node_id"]),
                    creation_key=_LEGACY_CREATION_KEY,
                    runtime_revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            instance_id = UUID(str(existing["id"]))
            if UUID(str(existing["scenario_version_id"])) != version_id:
                raise RuntimeError(
                    "legacy default GameInstance has an incompatible version binding"
                )
        result[player_id] = instance_id

    # C4-created rows predate creation keys. Give each a stable audit key before
    # the column becomes mandatory; the immutable ScenarioVersion binding stays unchanged.
    for row in bind.execute(
        sa.select(instances.c.id).where(instances.c.creation_key.is_(None))
    ).mappings():
        bind.execute(
            instances.update()
            .where(instances.c.id == row["id"])
            .values(creation_key=f"pre-c6:{row['id']}")
        )
    return result


def _backfill_instance_state(bind: sa.Connection, owners: dict[UUID, UUID]) -> None:
    names = (
        "players",
        "world_nodes",
        "player_node_states",
        "player_world_fact_states",
        "player_domain_states",
        "player_world_facts",
        "officer_appointments",
        "game_instance_node_states",
        "game_instance_fact_states",
        "game_instance_resource_states",
        "game_instance_world_facts",
        "game_instance_officer_appointments",
    )
    t = _tables(bind, *names)
    node_keys = dict(bind.execute(sa.select(t["world_nodes"].c.id, t["world_nodes"].c.key)).all())
    player_gold = dict(bind.execute(sa.select(t["players"].c.id, t["players"].c.gold)).all())
    now = datetime.now(UTC)

    for row in bind.execute(sa.select(t["player_node_states"])).mappings():
        bind.execute(
            t["game_instance_node_states"]
            .insert()
            .values(
                game_instance_id=_db_uuid(bind, owners[UUID(str(row["player_id"]))]),
                node_key=node_keys[row["node_id"]],
                status=row["status"],
                visibility=row["visibility"],
                version=row["version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    for row in bind.execute(sa.select(t["player_world_fact_states"])).mappings():
        bind.execute(
            t["game_instance_fact_states"]
            .insert()
            .values(
                game_instance_id=_db_uuid(bind, owners[UUID(str(row["player_id"]))]),
                node_key=node_keys[row["node_id"]],
                fact_key=row["fact_key"],
                truth_value=row["truth_value"],
                visibility=row["visibility"],
                version=row["version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    for row in bind.execute(sa.select(t["player_domain_states"])).mappings():
        player_id = UUID(str(row["player_id"]))
        resources = (
            ("soldiers", row["soldiers_total"], row["soldiers_committed"]),
            ("food", row["food"], 0),
            ("morale", row["morale"], 0),
            ("gold", player_gold[row["player_id"]], 0),
        )
        for key, value, reserved in resources:
            bind.execute(
                t["game_instance_resource_states"]
                .insert()
                .values(
                    game_instance_id=_db_uuid(bind, owners[player_id]),
                    resource_key=key,
                    value=value,
                    reserved_value=reserved,
                    version=row["version"],
                    created_at=now,
                    updated_at=now,
                )
            )
    for row in bind.execute(sa.select(t["player_world_facts"])).mappings():
        bind.execute(
            t["game_instance_world_facts"]
            .insert()
            .values(
                game_instance_id=_db_uuid(bind, owners[UUID(str(row["player_id"]))]),
                key=row["key"],
                value=row["value"],
                version=row["version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    for row in bind.execute(sa.select(t["officer_appointments"])).mappings():
        bind.execute(
            t["game_instance_officer_appointments"]
            .insert()
            .values(
                game_instance_id=_db_uuid(bind, owners[UUID(str(row["player_id"]))]),
                npc_id=row["npc_id"],
                status=row["status"],
                authority_overrides=row["authority_overrides"],
                version=row["version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )


def _backfill_direct_owners(bind: sa.Connection, owners: dict[UUID, UUID]) -> None:
    for table_name in _OWNED_TABLES:
        table = _tables(bind, table_name)[table_name]
        for player_id, instance_id in owners.items():
            bind.execute(
                table.update()
                .where(
                    table.c.player_id == _db_uuid(bind, player_id),
                    table.c.game_instance_id.is_(None),
                )
                .values(game_instance_id=_db_uuid(bind, instance_id))
            )


def _validate_runtime_graph(bind: sa.Connection) -> None:
    for table_name in _OWNED_TABLES:
        table = _tables(bind, table_name)[table_name]
        missing = bind.scalar(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_instance_id.is_(None))
        )
        if missing:
            raise RuntimeError(f"{table_name} contains runtime rows without GameInstance ownership")
    invalid_checks = (
        """SELECT COUNT(*) FROM conversation_sessions s JOIN game_instances i
           ON i.id=s.game_instance_id WHERE i.player_id != s.player_id""",
        """SELECT COUNT(*) FROM agent_tasks t JOIN game_instances i
           ON i.id=t.game_instance_id JOIN conversation_sessions s
           ON s.id=t.origin_session_id WHERE i.player_id != t.player_id
           OR s.game_instance_id != t.game_instance_id""",
        """SELECT COUNT(*) FROM memories m JOIN game_instances i
           ON i.id=m.game_instance_id LEFT JOIN conversation_sessions s
           ON s.id=m.source_session_id WHERE i.player_id != m.player_id
           OR (m.source_session_id IS NOT NULL AND s.game_instance_id != m.game_instance_id)""",
        """SELECT COUNT(*) FROM world_operations o JOIN game_instances i
           ON i.id=o.game_instance_id LEFT JOIN agent_tasks t ON t.id=o.task_id
           WHERE i.player_id != o.player_id
           OR (o.task_id IS NOT NULL AND t.game_instance_id != o.game_instance_id)""",
        """SELECT COUNT(*) FROM player_decision_requests d JOIN game_instances i
           ON i.id=d.game_instance_id JOIN agent_tasks t ON t.id=d.task_id
           WHERE i.player_id != d.player_id OR t.game_instance_id != d.game_instance_id""",
    )
    if any(bind.scalar(sa.text(statement)) for statement in invalid_checks):
        raise RuntimeError("legacy runtime graph crosses a GameInstance ownership boundary")


def _tighten_ownership_columns() -> None:
    op.drop_index("uq_world_operations_legacy_idempotency", table_name="world_operations")
    for table_name in _OWNED_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "game_instance_id",
                existing_type=sa.Uuid(),
                nullable=False,
                server_default=None,
            )
    with op.batch_alter_table("game_instances") as batch_op:
        batch_op.alter_column(
            "creation_key",
            existing_type=sa.String(length=160),
            nullable=False,
        )


def _drop_instance_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS game_instances_reject_binding_update")
        op.execute("DROP TRIGGER IF EXISTS game_instances_reject_creation_key_update")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS game_instances_reject_binding_update ON game_instances")
        op.execute(
            "DROP TRIGGER IF EXISTS game_instances_reject_creation_key_update ON game_instances"
        )
    else:
        raise RuntimeError(f"Runtime hardening is unsupported on {dialect}")


def _replace_instance_triggers() -> None:
    dialect = op.get_bind().dialect.name
    _drop_instance_triggers()
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER game_instances_reject_binding_update
            BEFORE UPDATE OF player_id, scenario_version_id ON game_instances
            WHEN NEW.player_id != OLD.player_id
              OR NEW.scenario_version_id != OLD.scenario_version_id
            BEGIN SELECT RAISE(ABORT, 'GameInstance bindings are immutable'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER game_instances_reject_creation_key_update
            BEFORE UPDATE OF creation_key ON game_instances
            WHEN NEW.creation_key IS NOT OLD.creation_key
            BEGIN SELECT RAISE(ABORT, 'GameInstance creation binding is immutable'); END
            """
        )
    elif dialect == "postgresql":
        # Functions were created by C4/C6 and remain in place through batch alterations.
        op.execute(
            """CREATE TRIGGER game_instances_reject_binding_update BEFORE UPDATE ON game_instances
            FOR EACH ROW EXECUTE FUNCTION reject_game_instance_binding_drift()"""
        )
        op.execute(
            """CREATE TRIGGER game_instances_reject_creation_key_update
            BEFORE UPDATE ON game_instances
            FOR EACH ROW EXECUTE FUNCTION reject_game_instance_creation_key_drift()"""
        )
    else:
        raise RuntimeError(f"Runtime hardening is unsupported on {dialect}")


def _db_uuid(bind: sa.Connection, value: UUID) -> UUID | str:
    return value.hex if bind.dialect.name == "sqlite" else value
