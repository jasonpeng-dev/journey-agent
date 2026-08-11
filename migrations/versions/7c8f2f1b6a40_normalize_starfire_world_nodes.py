"""normalize Starfire world nodes

Revision ID: 7c8f2f1b6a40
Revises: d0b3e5ceb9a2
Create Date: 2026-08-11 00:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "7c8f2f1b6a40"
down_revision: str | None = "d0b3e5ceb9a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_NAMESPACE = UUID("3e16a11d-9cf5-4981-af7a-152c28331300")

worlds = sa.table(
    "worlds",
    sa.column("id", sa.Uuid()),
    sa.column("key", sa.String()),
)
world_nodes = sa.table(
    "world_nodes",
    sa.column("id", sa.Uuid()),
    sa.column("world_id", sa.Uuid()),
    sa.column("key", sa.String()),
    sa.column("name", sa.String()),
    sa.column("description", sa.Text()),
    sa.column("type", sa.String()),
    sa.column("default_status", sa.String()),
    sa.column("version", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
players = sa.table(
    "players",
    sa.column("id", sa.Uuid()),
    sa.column("current_node_id", sa.Uuid()),
)
npcs = sa.table(
    "npcs",
    sa.column("id", sa.Uuid()),
    sa.column("current_node_id", sa.Uuid()),
)
player_node_states = sa.table(
    "player_node_states",
    sa.column("player_id", sa.Uuid()),
    sa.column("node_id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("version", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
player_world_facts = sa.table(
    "player_world_facts",
    sa.column("player_id", sa.Uuid()),
    sa.column("key", sa.String()),
    sa.column("value", sa.JSON()),
)


def _seed_id(name: str) -> UUID:
    return uuid5(SEED_NAMESPACE, name)


def _node_id(bind: Any, key: str) -> UUID | None:
    return bind.execute(sa.select(world_nodes.c.id).where(world_nodes.c.key == key)).scalar_one_or_none()


def _ensure_node(
    bind: Any,
    *,
    world_id: UUID,
    key: str,
    name: str,
    description: str,
    node_type: str,
    default_status: str,
) -> UUID:
    node_id = _node_id(bind, key)
    now = datetime.now(UTC)
    values = {
        "world_id": world_id,
        "key": key,
        "name": name,
        "description": description,
        "type": node_type,
        "default_status": default_status,
        "updated_at": now,
    }
    if node_id is None:
        node_id = _seed_id(f"node:{key}")
        bind.execute(
            world_nodes.insert().values(
                id=node_id,
                version=1,
                created_at=now,
                **values,
            )
        )
    else:
        bind.execute(world_nodes.update().where(world_nodes.c.id == node_id).values(**values))
    return node_id


def _upsert_player_node_state(
    bind: Any,
    *,
    player_id: UUID,
    node_id: UUID,
    status: str,
    source_versions: list[int] | None = None,
) -> None:
    existing = bind.execute(
        sa.select(player_node_states).where(
            player_node_states.c.player_id == player_id,
            player_node_states.c.node_id == node_id,
        )
    ).mappings().one_or_none()
    now = datetime.now(UTC)
    versions = list(source_versions or [])
    if existing is None:
        bind.execute(
            player_node_states.insert().values(
                player_id=player_id,
                node_id=node_id,
                status=status,
                version=max(versions, default=0) + 1,
                created_at=now,
                updated_at=now,
            )
        )
        return
    versions.append(int(existing["version"]))
    bind.execute(
        player_node_states.update()
        .where(
            player_node_states.c.player_id == player_id,
            player_node_states.c.node_id == node_id,
        )
        .values(
            status=status,
            version=max(versions) + 1,
            updated_at=now,
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    world_id = bind.execute(
        sa.select(worlds.c.id).where(worlds.c.key == "starfire_command")
    ).scalar_one_or_none()
    if world_id is None:
        return

    northern_valley_id = _ensure_node(
        bind,
        world_id=world_id,
        key="northern_valley",
        name="北境山谷",
        description="通往星火前哨的北方山谷，初始存在尚未公开的伏兵。",
        node_type="EVENT",
        default_status="AVAILABLE",
    )
    supply_route_id = _ensure_node(
        bind,
        world_id=world_id,
        key="enemy_north_supply_route",
        name="敌军北方补给线",
        description="为山谷守军提供补给的隐蔽路线，发现后可以实施破袭。",
        node_type="EVENT",
        default_status="LOCKED",
    )
    legacy_ids = [
        node_id
        for node_id in (_node_id(bind, "valley_entrance"), _node_id(bind, "ambush_valley"))
        if node_id is not None
    ]
    if legacy_ids:
        bind.execute(
            players.update()
            .where(players.c.current_node_id.in_(legacy_ids))
            .values(current_node_id=northern_valley_id)
        )
        bind.execute(
            npcs.update()
            .where(npcs.c.current_node_id.in_(legacy_ids))
            .values(current_node_id=northern_valley_id)
        )

    player_ids = list(bind.execute(sa.select(players.c.id)).scalars())
    for player_id in player_ids:
        legacy_states = (
            bind.execute(
                sa.select(player_node_states.c.version).where(
                    player_node_states.c.player_id == player_id,
                    player_node_states.c.node_id.in_(legacy_ids),
                )
            ).scalars().all()
            if legacy_ids
            else []
        )
        _upsert_player_node_state(
            bind,
            player_id=player_id,
            node_id=northern_valley_id,
            status="AVAILABLE",
            source_versions=[int(version) for version in legacy_states],
        )
        legacy_supply = bind.execute(
            sa.select(player_world_facts.c.value).where(
                player_world_facts.c.player_id == player_id,
                player_world_facts.c.key == "enemy_supply_route",
            )
        ).scalar_one_or_none()
        supply_status = (
            str(legacy_supply.get("status")) if isinstance(legacy_supply, dict) else "UNKNOWN"
        )
        _upsert_player_node_state(
            bind,
            player_id=player_id,
            node_id=supply_route_id,
            status="AVAILABLE" if supply_status in {"ACTIVE", "DISRUPTED"} else "LOCKED",
        )

    if legacy_ids:
        bind.execute(player_node_states.delete().where(player_node_states.c.node_id.in_(legacy_ids)))
        bind.execute(world_nodes.delete().where(world_nodes.c.id.in_(legacy_ids)))


def downgrade() -> None:
    bind = op.get_bind()
    world_id = bind.execute(
        sa.select(worlds.c.id).where(worlds.c.key == "starfire_command")
    ).scalar_one_or_none()
    if world_id is None:
        return

    valley_entrance_id = _ensure_node(
        bind,
        world_id=world_id,
        key="valley_entrance",
        name="山谷入口",
        description="侦察与有限军事行动的集结点。",
        node_type="EVENT",
        default_status="AVAILABLE",
    )
    ambush_valley_id = _ensure_node(
        bind,
        world_id=world_id,
        key="ambush_valley",
        name="伏击谷",
        description="控制星火前哨通路的敌军据点。",
        node_type="ENCOUNTER",
        default_status="LOCKED",
    )
    canonical_ids = [
        node_id
        for node_id in (
            _node_id(bind, "northern_valley"),
            _node_id(bind, "enemy_north_supply_route"),
        )
        if node_id is not None
    ]
    if canonical_ids:
        bind.execute(
            players.update()
            .where(players.c.current_node_id.in_(canonical_ids))
            .values(current_node_id=valley_entrance_id)
        )
        bind.execute(
            npcs.update()
            .where(npcs.c.current_node_id.in_(canonical_ids))
            .values(current_node_id=valley_entrance_id)
        )

    player_ids = list(bind.execute(sa.select(players.c.id)).scalars())
    for player_id in player_ids:
        _upsert_player_node_state(
            bind,
            player_id=player_id,
            node_id=valley_entrance_id,
            status="AVAILABLE",
        )
        intelligence = bind.execute(
            sa.select(player_world_facts.c.value).where(
                player_world_facts.c.player_id == player_id,
                player_world_facts.c.key == "valley_intelligence",
            )
        ).scalar_one_or_none()
        intelligence_status = (
            str(intelligence.get("status")) if isinstance(intelligence, dict) else "INCOMPLETE"
        )
        _upsert_player_node_state(
            bind,
            player_id=player_id,
            node_id=ambush_valley_id,
            status=(
                "AVAILABLE"
                if intelligence_status in {"PARTIAL", "COMPLETE"}
                else "LOCKED"
            ),
        )

    if canonical_ids:
        bind.execute(
            player_node_states.delete().where(player_node_states.c.node_id.in_(canonical_ids))
        )
        bind.execute(world_nodes.delete().where(world_nodes.c.id.in_(canonical_ids)))
