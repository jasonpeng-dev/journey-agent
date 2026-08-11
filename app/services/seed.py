from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import NPCRole
from app.infrastructure.db.models import NPC, World, WorldNode
from app.scenarios.starfire.definition import STARFIRE_WORLD
from app.scenarios.starfire.persistence import persisted_node_specs
from app.services.game import seed_id


def seed_demo_world(db: Session) -> World:
    """Seed only the strategic Starfire command domain."""
    world = db.scalar(select(World).where(World.key == "starfire_command"))
    if world is None:
        world = World(
            id=seed_id("world:starfire_command"),
            key=STARFIRE_WORLD.key,
            name=STARFIRE_WORLD.name,
            chapter=1,
        )
        db.add(world)
        db.flush()
    else:
        world.name = STARFIRE_WORLD.name

    nodes: dict[str, WorldNode] = {}
    for node_spec in persisted_node_specs():
        node = db.scalar(select(WorldNode).where(WorldNode.key == node_spec.key))
        if node is None:
            node = WorldNode(
                id=seed_id(f"node:{node_spec.key}"),
                world_id=world.id,
                key=node_spec.key,
                name=node_spec.name,
                description=node_spec.description,
                type=node_spec.node_type,
                default_status=node_spec.default_status,
            )
            db.add(node)
        else:
            node.name = node_spec.name
            node.description = node_spec.description
            node.type = node_spec.node_type
            node.default_status = node_spec.default_status
        nodes[node_spec.key] = node
    db.flush()

    officer_specs: list[dict[str, Any]] = [
        {
            "key": "shen_ce",
            "name": "沈策",
            "role": NPCRole.STRATEGIST,
            "persona": "谨慎的统筹军师。重视完整情报、低伤亡和明确责任。",
            "doctrine": {
                "risk_preference": "LOW",
                "priorities": ["INTELLIGENCE", "LOW_CASUALTIES", "COORDINATION"],
            },
            "authority_limits": {"max_intelligence_gold": 10},
            "permissions": {
                "create_task_plan": True,
                "replan_task": True,
                "inspect_command_state": True,
            },
        },
        {
            "key": "han_lie",
            "name": "韩烈",
            "role": NPCRole.GENERAL,
            "persona": "果断的武将。重视行动速度与士气。遵守主公授予的兵力上限。",
            "doctrine": {
                "risk_preference": "MEDIUM",
                "priorities": ["MOMENTUM", "MORALE", "DECISIVE_ACTION"],
            },
            "authority_limits": {"max_troops": 200},
            "permissions": {
                "inspect_command_state": True,
                "start_recon_operation": True,
                "start_military_operation": True,
            },
        },
        {
            "key": "lu_ning",
            "name": "陆宁",
            "role": NPCRole.STEWARD,
            "persona": "节制的内政官。保护民心。偏好可持续的商贸与建设方案。",
            "doctrine": {
                "risk_preference": "LOW",
                "priorities": ["RESOURCE_EFFICIENCY", "PUBLIC_SUPPORT", "LONG_TERM_TRADE"],
            },
            "authority_limits": {"max_food": 30, "max_gold": 40},
            "permissions": {
                "inspect_command_state": True,
                "negotiate_village_support": True,
                "start_outpost_repair": True,
                "start_trade_route_test": True,
            },
        },
    ]
    for officer_spec in officer_specs:
        officer = db.scalar(select(NPC).where(NPC.key == officer_spec["key"]))
        values = {
            "name": str(officer_spec["name"]),
            "persona": str(officer_spec["persona"]),
            "doctrine": dict(officer_spec["doctrine"]),
            "authority_limits": dict(officer_spec["authority_limits"]),
            "current_node_id": nodes["capital_council"].id,
            "role": officer_spec["role"],
            "permission_profile": dict(officer_spec["permissions"]),
        }
        if officer is None:
            db.add(
                NPC(
                    id=seed_id(f"npc:{officer_spec['key']}"),
                    key=str(officer_spec["key"]),
                    **values,
                )
            )
        else:
            for field, value in values.items():
                setattr(officer, field, value)
    db.flush()
    return world
