from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import NodeStatus, NodeType, NPCRole
from app.infrastructure.db.models import NPC, World, WorldNode
from app.services.game import seed_id


def seed_demo_world(db: Session) -> World:
    """Seed only the strategic Starfire command domain."""
    world = db.scalar(select(World).where(World.key == "starfire_command"))
    if world is None:
        world = World(
            id=seed_id("world:starfire_command"),
            key="starfire_command",
            name="星火前哨战略领地",
            chapter=1,
        )
        db.add(world)
        db.flush()

    node_specs = [
        (
            "capital_council",
            "议事厅",
            "主公下达军令。部下制定方案并汇报结果。",
            NodeType.START,
            NodeStatus.AVAILABLE,
        ),
        (
            "north_village",
            "北境村落",
            "可为北方行动提供向导、情报和补给。",
            NodeType.NPC,
            NodeStatus.AVAILABLE,
        ),
        (
            "valley_entrance",
            "山谷入口",
            "侦察与有限军事行动的集结点。",
            NodeType.EVENT,
            NodeStatus.AVAILABLE,
        ),
        (
            "ambush_valley",
            "伏击谷",
            "控制星火前哨通路的敌军据点。",
            NodeType.ENCOUNTER,
            NodeStatus.LOCKED,
        ),
        (
            "starfire_outpost",
            "星火前哨",
            "需要安全通路与资源投入才能恢复运作。",
            NodeType.EVENT,
            NodeStatus.LOCKED,
        ),
        (
            "northern_trade_route",
            "北方商路",
            "通过确定性商路测试后才会开放。",
            NodeType.EVENT,
            NodeStatus.LOCKED,
        ),
    ]
    nodes: dict[str, WorldNode] = {}
    for key, name, description, node_type, status in node_specs:
        node = db.scalar(select(WorldNode).where(WorldNode.key == key))
        if node is None:
            node = WorldNode(
                id=seed_id(f"node:{key}"),
                world_id=world.id,
                key=key,
                name=name,
                description=description,
                type=node_type,
                default_status=status,
            )
            db.add(node)
        nodes[key] = node
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
    for spec in officer_specs:
        officer = db.scalar(select(NPC).where(NPC.key == spec["key"]))
        values = {
            "name": str(spec["name"]),
            "persona": str(spec["persona"]),
            "doctrine": dict(spec["doctrine"]),
            "authority_limits": dict(spec["authority_limits"]),
            "current_node_id": nodes["capital_council"].id,
            "role": spec["role"],
            "permission_profile": dict(spec["permissions"]),
        }
        if officer is None:
            db.add(
                NPC(
                    id=seed_id(f"npc:{spec['key']}"),
                    key=str(spec["key"]),
                    **values,
                )
            )
        else:
            for field, value in values.items():
                setattr(officer, field, value)
    db.flush()
    return world
