from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import NodeStatus, NodeType, NPCRole, ObjectiveType
from app.infrastructure.db.models import (
    NPC,
    EncounterDefinition,
    ItemDefinition,
    OfficerAppointment,
    Player,
    PlayerDomainState,
    PlayerNodeState,
    PlayerNPCRelationship,
    PlayerWorldFact,
    QuestTemplate,
    World,
    WorldNode,
    WorldNodeEdge,
)
from app.services.game import seed_id


def seed_demo_world(db: Session) -> World:
    existing = db.scalar(select(World).where(World.key == "fire_mountain"))
    if existing:
        _ensure_starfire_content(db, existing)
        return existing
    world = World(
        id=seed_id("world:fire_mountain"),
        key="fire_mountain",
        name="火焰山之旅",
        chapter=1,
    )
    db.add(world)
    node_specs = [
        ("journey_start", "旅途起点", NodeType.START, NodeStatus.AVAILABLE),
        ("guanyin_shrine", "观音指引", NodeType.NPC, NodeStatus.AVAILABLE),
        ("dragon_ruins", "龙宫遗迹", NodeType.EVENT, NodeStatus.AVAILABLE),
        ("fire_foothills", "火焰山脚", NodeType.ENCOUNTER, NodeStatus.LOCKED),
        ("red_boy_cave", "红孩儿洞府", NodeType.BOSS, NodeStatus.LOCKED),
        ("ember_road", "余烬之路", NodeType.EVENT, NodeStatus.LOCKED),
    ]
    nodes: dict[str, WorldNode] = {}
    for key, name, node_type, status in node_specs:
        node = WorldNode(
            id=seed_id(f"node:{key}"),
            world_id=world.id,
            key=key,
            name=name,
            description=f"{name}, a key location on the Journey to the West.",
            type=node_type,
            default_status=status,
        )
        nodes[key] = node
        db.add(node)
    for source, target in [
        ("journey_start", "guanyin_shrine"),
        ("journey_start", "dragon_ruins"),
        ("guanyin_shrine", "fire_foothills"),
        ("dragon_ruins", "fire_foothills"),
        ("fire_foothills", "red_boy_cave"),
        ("red_boy_cave", "ember_road"),
    ]:
        db.add(
            WorldNodeEdge(
                source_node_id=nodes[source].id,
                target_node_id=nodes[target].id,
            )
        )
    db.add_all(
        [
            NPC(
                id=seed_id("npc:guanyin"),
                key="guanyin",
                name="Guanyin",
                persona="A calm, compassionate guide who values promises.",
                current_node_id=nodes["guanyin_shrine"].id,
                role=NPCRole.QUEST_GIVER,
                permission_profile={"create_quest": True, "update_relationship": True},
            ),
            NPC(
                id=seed_id("npc:red_boy"),
                key="red_boy",
                name="Red Boy",
                persona="A proud, clever guardian who respects courage and honesty.",
                current_node_id=nodes["red_boy_cave"].id,
                role=NPCRole.BOSS,
                permission_profile={"update_relationship": True},
            ),
        ]
    )
    db.add_all(
        [
            ItemDefinition(
                id=seed_id("item:water_talisman"),
                key="water_talisman",
                name="避火水符",
                type="QUEST",
                max_stack=1,
            ),
            ItemDefinition(
                id=seed_id("item:ember"),
                key="ember",
                name="灵火余烬",
                type="MATERIAL",
                max_stack=20,
            ),
        ]
    )
    quest_specs = [
        (
            "clear_fire_foothills",
            "平定火焰山脚",
            ObjectiveType.COMPLETE_ENCOUNTER,
            "fire_foothills_guardians",
            {"gold": 50, "items": {"ember": 1}, "unlock_node": "red_boy_cave"},
            ["QUEST_GIVER"],
        ),
        (
            "visit_dragon_ruins",
            "探访龙宫遗迹",
            ObjectiveType.COMPLETE_NODE,
            "dragon_ruins",
            {"gold": 20, "items": {}},
            ["GUIDE", "QUEST_GIVER"],
        ),
        (
            "befriend_red_boy",
            "赢得红孩儿信任",
            ObjectiveType.REACH_RELATIONSHIP,
            "red_boy",
            {"gold": 80, "items": {}, "unlock_node": "ember_road"},
            ["BOSS"],
        ),
    ]
    for key, name, objective_type, target, reward, roles in quest_specs:
        db.add(
            QuestTemplate(
                id=seed_id(f"quest_template:{key}"),
                key=key,
                name=name,
                allowed_roles=roles,
                objective_type=objective_type,
                objective_target=target,
                objective_quantity=1,
                reward=reward,
            )
        )
    db.add(
        EncounterDefinition(
            id=seed_id("encounter:fire_foothills_guardians"),
            key="fire_foothills_guardians",
            node_id=nodes["fire_foothills"].id,
            difficulty=4,
            allowed_strategies=["CAUTIOUS", "AGGRESSIVE", "NEGOTIATE"],
            success_rules={
                "formula": "level + strategy_bonus + water_talisman_bonus >= difficulty"
            },
            reward_template={"quest_progress": "clear_fire_foothills"},
        )
    )
    db.flush()
    _ensure_starfire_content(db, world)
    db.flush()
    return world


def _ensure_starfire_content(db: Session, world: World) -> None:
    node_specs = [
        (
            "capital_council",
            "Capital Council Hall",
            "The lord issues commands and receives reports from appointed officers.",
            NodeType.NPC,
            NodeStatus.AVAILABLE,
        ),
        (
            "north_village",
            "Northern Village",
            "A farming settlement whose guides and supplies can support the northern road.",
            NodeType.NPC,
            NodeStatus.AVAILABLE,
        ),
        (
            "valley_entrance",
            "Valley Entrance",
            "The staging ground for reconnaissance and limited military operations.",
            NodeType.EVENT,
            NodeStatus.AVAILABLE,
        ),
        (
            "ambush_valley",
            "Ambush Valley",
            "A concealed raider position guarding the approach to Starfire Outpost.",
            NodeType.ENCOUNTER,
            NodeStatus.LOCKED,
        ),
        (
            "starfire_crossroads",
            "Starfire Crossroads",
            "A caravan junction where Captain Aria coordinates recovery efforts.",
            NodeType.NPC,
            NodeStatus.AVAILABLE,
        ),
        (
            "starfire_road",
            "Broken Lantern Road",
            "A dangerous road controlled by raiders between the crossroads and the outpost.",
            NodeType.ENCOUNTER,
            NodeStatus.LOCKED,
        ),
        (
            "starfire_outpost",
            "Starfire Outpost",
            "A strategic relay station awaiting safe roads and operational clearance.",
            NodeType.EVENT,
            NodeStatus.LOCKED,
        ),
        (
            "northern_trade_route",
            "Northern Trade Route",
            "The commercial corridor unlocked only after a verified successful route test.",
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
    start = db.scalar(select(WorldNode).where(WorldNode.key == "journey_start"))
    assert start is not None
    for source, target in [
        (start, nodes["starfire_crossroads"]),
        (nodes["starfire_crossroads"], nodes["starfire_road"]),
        (nodes["starfire_road"], nodes["starfire_outpost"]),
        (start, nodes["capital_council"]),
        (nodes["capital_council"], nodes["north_village"]),
        (nodes["capital_council"], nodes["valley_entrance"]),
        (nodes["valley_entrance"], nodes["ambush_valley"]),
        (nodes["ambush_valley"], nodes["starfire_outpost"]),
        (nodes["starfire_outpost"], nodes["northern_trade_route"]),
    ]:
        edge = db.scalar(
            select(WorldNodeEdge).where(
                WorldNodeEdge.source_node_id == source.id,
                WorldNodeEdge.target_node_id == target.id,
            )
        )
        if edge is None:
            db.add(WorldNodeEdge(source_node_id=source.id, target_node_id=target.id))
    captain = db.scalar(select(NPC).where(NPC.key == "captain_aria"))
    if captain is None:
        captain = NPC(
            id=seed_id("npc:captain_aria"),
            key="captain_aria",
            name="Captain Aria",
            persona=(
                "A pragmatic expedition captain who plans carefully, verifies evidence, "
                "and never claims that an unsafe road is secure."
            ),
            current_node_id=nodes["starfire_crossroads"].id,
            role=NPCRole.GUIDE,
            permission_profile={
                "create_task_plan": True,
                "replan_task": True,
                "create_quest": True,
                "request_npc_assistance": True,
                "prepare_starfire_route": True,
                "restore_outpost": True,
                "grant_access": True,
                "update_relationship": True,
            },
        )
        db.add(captain)
    else:
        captain.doctrine = captain.doctrine or {"risk": "CAUTIOUS"}
        captain.authority_limits = captain.authority_limits or {}

    officer_specs: list[dict[str, Any]] = [
        {
            "key": "shen_ce",
            "name": "Shen Ce",
            "role": NPCRole.STRATEGIST,
            "persona": (
                "A cautious chief strategist who values complete intelligence, "
                "low casualties, and explicit responsibility."
            ),
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
            "name": "Han Lie",
            "role": NPCRole.GENERAL,
            "persona": (
                "A decisive field general who values speed and morale while respecting "
                "the troop ceiling delegated by the lord."
            ),
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
            "name": "Lu Ning",
            "role": NPCRole.STEWARD,
            "persona": (
                "A frugal steward who protects public support and prefers durable trade "
                "and infrastructure over coercion."
            ),
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
    officers: list[NPC] = []
    for spec in officer_specs:
        officer = db.scalar(select(NPC).where(NPC.key == spec["key"]))
        if officer is None:
            officer = NPC(
                id=seed_id(f"npc:{spec['key']}"),
                key=str(spec["key"]),
                name=str(spec["name"]),
                persona=str(spec["persona"]),
                doctrine=dict(spec["doctrine"]),
                authority_limits=dict(spec["authority_limits"]),
                current_node_id=nodes["capital_council"].id,
                role=spec["role"],
                permission_profile=dict(spec["permissions"]),
            )
            db.add(officer)
        else:
            officer.name = str(spec["name"])
            officer.persona = str(spec["persona"])
            officer.doctrine = dict(spec["doctrine"])
            officer.authority_limits = dict(spec["authority_limits"])
            officer.current_node_id = nodes["capital_council"].id
            officer.role = spec["role"]
            officer.permission_profile = dict(spec["permissions"])
        officers.append(officer)
    template = db.scalar(select(QuestTemplate).where(QuestTemplate.key == "secure_starfire_road"))
    if template is None:
        db.add(
            QuestTemplate(
                id=seed_id("quest_template:secure_starfire_road"),
                key="secure_starfire_road",
                name="Secure the Broken Lantern Road",
                allowed_roles=["GUIDE", "QUEST_GIVER"],
                objective_type=ObjectiveType.COMPLETE_ENCOUNTER,
                objective_target="starfire_road_raiders",
                objective_quantity=1,
                reward={"gold": 30, "items": {}},
            )
        )
    encounter = db.scalar(
        select(EncounterDefinition).where(EncounterDefinition.key == "starfire_road_raiders")
    )
    if encounter is None:
        db.add(
            EncounterDefinition(
                id=seed_id("encounter:starfire_road_raiders"),
                key="starfire_road_raiders",
                node_id=nodes["starfire_road"].id,
                difficulty=4,
                allowed_strategies=["CAUTIOUS", "AGGRESSIVE", "NEGOTIATE"],
                success_rules={
                    "formula": ("level + strategy_bonus + assistance_bonus >= difficulty")
                },
                reward_template={"quest_progress": "secure_starfire_road"},
            )
        )
    db.flush()
    db.flush()
    for player in db.scalars(select(Player)).all():
        for node in nodes.values():
            if db.get(PlayerNodeState, (player.id, node.id)) is None:
                db.add(
                    PlayerNodeState(
                        player_id=player.id,
                        node_id=node.id,
                        status=node.default_status,
                    )
                )
        if db.get(PlayerNPCRelationship, (player.id, captain.id)) is None:
            db.add(PlayerNPCRelationship(player_id=player.id, npc_id=captain.id))
        for officer in officers:
            if db.get(PlayerNPCRelationship, (player.id, officer.id)) is None:
                db.add(PlayerNPCRelationship(player_id=player.id, npc_id=officer.id))
            if db.get(OfficerAppointment, (player.id, officer.id)) is None:
                db.add(
                    OfficerAppointment(
                        player_id=player.id,
                        npc_id=officer.id,
                        status="ACTIVE",
                    )
                )
        if db.get(PlayerDomainState, player.id) is None:
            db.add(
                PlayerDomainState(
                    player_id=player.id,
                    soldiers_total=300,
                    soldiers_committed=0,
                    food=100,
                    morale=60,
                )
            )
        initial_facts: dict[str, dict[str, object]] = {
            "valley_intelligence": {"status": "INCOMPLETE"},
            "enemy_supply_route": {"status": "UNKNOWN"},
            "valley_security": {"status": "UNSAFE"},
            "village_support": {"status": "NONE"},
            "starfire_outpost_status": {"status": "DAMAGED"},
            "northern_trade_route_status": {"status": "CLOSED"},
        }
        for key, value in initial_facts.items():
            if db.get(PlayerWorldFact, (player.id, key)) is None:
                db.add(PlayerWorldFact(player_id=player.id, key=key, value=value))
