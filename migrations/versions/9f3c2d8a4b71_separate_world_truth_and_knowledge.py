"""separate world truth and player knowledge

Revision ID: 9f3c2d8a4b71
Revises: 7c8f2f1b6a40
Create Date: 2026-08-11 00:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "9f3c2d8a4b71"
down_revision: str | None = "7c8f2f1b6a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FACT_REFS = {
    "village_support": ("north_village", "village_support", "NONE"),
    "valley_intelligence": ("northern_valley", "valley_intelligence", "INCOMPLETE"),
    "valley_security": ("northern_valley", "valley_security", "UNSAFE"),
    "starfire_outpost_status": ("starfire_outpost", "outpost_status", "DAMAGED"),
    "northern_trade_route_status": (
        "northern_trade_route",
        "trade_route_status",
        "CLOSED",
    ),
}

player_world_facts = sa.table(
    "player_world_facts",
    sa.column("player_id", sa.Uuid()),
    sa.column("key", sa.String()),
    sa.column("value", sa.JSON()),
    sa.column("version", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
players = sa.table("players", sa.column("id", sa.Uuid()))
world_nodes = sa.table(
    "world_nodes",
    sa.column("id", sa.Uuid()),
    sa.column("key", sa.String()),
)
player_node_states = sa.table(
    "player_node_states",
    sa.column("player_id", sa.Uuid()),
    sa.column("node_id", sa.Uuid()),
    sa.column("visibility", sa.String()),
)
player_world_fact_states = sa.table(
    "player_world_fact_states",
    sa.column("player_id", sa.Uuid()),
    sa.column("node_id", sa.Uuid()),
    sa.column("fact_key", sa.String()),
    sa.column("truth_value", sa.JSON()),
    sa.column("visibility", sa.String()),
    sa.column("version", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _status(value: Any, default: str) -> str:
    if isinstance(value, dict) and value.get("status") is not None:
        return str(value["status"])
    return default


def _legacy_facts(bind: Any, player_id: Any) -> dict[str, dict[str, Any]]:
    rows = bind.execute(
        sa.select(player_world_facts.c.key, player_world_facts.c.value).where(
            player_world_facts.c.player_id == player_id
        )
    ).mappings()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row["value"]
        if isinstance(value, str):
            import json

            value = json.loads(value)
        result[str(row["key"])] = value if isinstance(value, dict) else {}
    return result


def upgrade() -> None:
    op.add_column(
        "player_node_states",
        sa.Column(
            "visibility",
            sa.Enum("HIDDEN", "KNOWN", name="visibility", native_enum=False),
            nullable=False,
            server_default="KNOWN",
        ),
    )
    op.create_table(
        "player_world_fact_states",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("fact_key", sa.String(length=80), nullable=False),
        sa.Column("truth_value", sa.JSON(), nullable=False),
        sa.Column(
            "visibility",
            sa.Enum("HIDDEN", "KNOWN", name="visibility", native_enum=False),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["world_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_id", "node_id", "fact_key"),
    )

    bind = op.get_bind()
    node_ids = dict(bind.execute(sa.select(world_nodes.c.key, world_nodes.c.id)).all())
    player_ids = list(bind.execute(sa.select(players.c.id)).scalars())
    now = datetime.now(UTC)

    for player_id in player_ids:
        legacy = _legacy_facts(bind, player_id)
        supply_legacy = _status(legacy.get("enemy_supply_route"), "UNKNOWN")
        supply_truth = "DISRUPTED" if supply_legacy == "DISRUPTED" else "ACTIVE"
        supply_visibility = "KNOWN" if supply_legacy in {"ACTIVE", "DISRUPTED"} else "HIDDEN"
        intelligence = _status(legacy.get("valley_intelligence"), "INCOMPLETE")
        security = _status(legacy.get("valley_security"), "UNSAFE")
        explicit_ambush = legacy.get("ambush_status")
        ambush_truth = _status(
            explicit_ambush,
            "CLEARED" if security == "SAFE" else "ACTIVE",
        )
        ambush_visibility = (
            "KNOWN"
            if explicit_ambush is not None
            or intelligence in {"PARTIAL", "COMPLETE"}
            or security == "SAFE"
            else "HIDDEN"
        )

        supply_node_id = node_ids.get("enemy_north_supply_route")
        if supply_node_id is not None:
            bind.execute(
                player_node_states.update()
                .where(
                    player_node_states.c.player_id == player_id,
                    player_node_states.c.node_id == supply_node_id,
                )
                .values(visibility=supply_visibility)
            )

        facts = [
            (
                node_key,
                fact_key,
                _status(legacy.get(legacy_key), default),
                "KNOWN",
            )
            for legacy_key, (node_key, fact_key, default) in FACT_REFS.items()
        ]
        facts.extend(
            [
                ("northern_valley", "ambush_status", ambush_truth, ambush_visibility),
                (
                    "enemy_north_supply_route",
                    "supply_status",
                    supply_truth,
                    supply_visibility,
                ),
            ]
        )
        for node_key, fact_key, truth_value, visibility in facts:
            node_id = node_ids.get(node_key)
            if node_id is None:
                continue
            bind.execute(
                player_world_fact_states.insert().values(
                    player_id=player_id,
                    node_id=node_id,
                    fact_key=fact_key,
                    truth_value=truth_value,
                    visibility=visibility,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )


def _upsert_legacy_fact(bind: Any, player_id: Any, key: str, status: str) -> None:
    existing = bind.execute(
        sa.select(player_world_facts.c.version).where(
            player_world_facts.c.player_id == player_id,
            player_world_facts.c.key == key,
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    value = {"status": status}
    if existing is None:
        bind.execute(
            player_world_facts.insert().values(
                player_id=player_id,
                key=key,
                value=value,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        bind.execute(
            player_world_facts.update()
            .where(
                player_world_facts.c.player_id == player_id,
                player_world_facts.c.key == key,
            )
            .values(value=value, version=int(existing) + 1, updated_at=now)
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            player_world_fact_states.c.player_id,
            world_nodes.c.key.label("node_key"),
            player_world_fact_states.c.fact_key,
            player_world_fact_states.c.truth_value,
            player_world_fact_states.c.visibility,
        ).select_from(
            player_world_fact_states.join(
                world_nodes,
                world_nodes.c.id == player_world_fact_states.c.node_id,
            )
        )
    ).mappings()
    reverse_refs = {
        (node_key, fact_key): legacy_key
        for legacy_key, (node_key, fact_key, _default) in FACT_REFS.items()
    }
    for row in rows:
        value = row["truth_value"]
        node_fact = (str(row["node_key"]), str(row["fact_key"]))
        if node_fact == ("enemy_north_supply_route", "supply_status"):
            status = str(value) if row["visibility"] == "KNOWN" else "UNKNOWN"
            _upsert_legacy_fact(bind, row["player_id"], "enemy_supply_route", status)
        elif node_fact == ("northern_valley", "ambush_status"):
            if row["visibility"] == "KNOWN":
                _upsert_legacy_fact(bind, row["player_id"], "ambush_status", str(value))
        elif legacy_key := reverse_refs.get(node_fact):
            _upsert_legacy_fact(bind, row["player_id"], legacy_key, str(value))

    op.drop_table("player_world_fact_states")
    with op.batch_alter_table("player_node_states") as batch_op:
        batch_op.drop_column("visibility")
