"""Canonical, human-readable definition of the Starfire world."""

from app.domain.world import (
    AccessState,
    FactDefinition,
    FactValueType,
    InteractionDefinition,
    NodeDefinition,
    RelationDefinition,
    RelationType,
    ResourceDefinition,
    Visibility,
    WorldDefinition,
    WorldNodeType,
)

NEGOTIATE_SUPPORT = InteractionDefinition(
    key="negotiate_support",
    name="NEGOTIATE_SUPPORT",
    description="Negotiate bounded village intelligence, guides, or supplies.",
)
RECONNAISSANCE = InteractionDefinition(
    key="reconnaissance",
    name="RECONNAISSANCE",
    description="Gather verified intelligence about a location.",
)
CLEAR_THREAT = InteractionDefinition(
    key="clear_threat",
    name="CLEAR_THREAT",
    description="Conduct a military operation to secure a threatened location.",
)
DISRUPT_SUPPLY = InteractionDefinition(
    key="disrupt_supply",
    name="DISRUPT_SUPPLY",
    description="Disrupt an identified enemy supply route.",
)
REPAIR = InteractionDefinition(
    key="repair",
    name="REPAIR",
    description="Commit bounded resources to restore a facility.",
)
TEST_TRADE_ROUTE = InteractionDefinition(
    key="test_trade_route",
    name="TEST_TRADE_ROUTE",
    description="Start a deterministic test of a trade route.",
)

CAPITAL_COUNCIL = NodeDefinition(
    key="capital_council",
    name="议事厅",
    description="主公下达军令, 部下制定方案并汇报结果。",
    node_type=WorldNodeType.HEADQUARTERS,
    initial_access=AccessState.AVAILABLE,
    initial_visibility=Visibility.KNOWN,
)

NORTH_VILLAGE = NodeDefinition(
    key="north_village",
    name="北境村落",
    description="可为北方行动提供向导、情报和补给。",
    node_type=WorldNodeType.SETTLEMENT,
    initial_access=AccessState.AVAILABLE,
    initial_visibility=Visibility.KNOWN,
    interactions=(NEGOTIATE_SUPPORT,),
    facts=(
        FactDefinition(
            key="village_support",
            name="村落支援",
            value_type=FactValueType.ENUM,
            initial_value="NONE",
            allowed_values=("NONE", "INTELLIGENCE", "GUIDE", "SUPPLIES"),
        ),
    ),
)

NORTHERN_VALLEY = NodeDefinition(
    key="northern_valley",
    name="北境山谷",
    description="通往星火前哨的北方山谷, 初始存在尚未公开的伏兵。",
    node_type=WorldNodeType.LOCATION,
    initial_access=AccessState.AVAILABLE,
    initial_visibility=Visibility.KNOWN,
    interactions=(RECONNAISSANCE, CLEAR_THREAT),
    facts=(
        FactDefinition(
            key="valley_intelligence",
            name="山谷情报",
            value_type=FactValueType.ENUM,
            initial_value="INCOMPLETE",
            allowed_values=("INCOMPLETE", "PARTIAL", "COMPLETE"),
        ),
        FactDefinition(
            key="valley_security",
            name="山谷安全",
            value_type=FactValueType.ENUM,
            initial_value="UNSAFE",
            allowed_values=("UNSAFE", "SAFE"),
        ),
        FactDefinition(
            key="ambush_status",
            name="伏兵状态",
            value_type=FactValueType.ENUM,
            initial_value="ACTIVE",
            initial_visibility=Visibility.HIDDEN,
            allowed_values=("ACTIVE", "CLEARED"),
        ),
    ),
)

ENEMY_NORTH_SUPPLY_ROUTE = NodeDefinition(
    key="enemy_north_supply_route",
    name="敌军北方补给线",
    description="为山谷守军提供补给的隐蔽路线, 发现后可以实施破袭。",
    node_type=WorldNodeType.ROUTE,
    initial_access=AccessState.LOCKED,
    initial_visibility=Visibility.HIDDEN,
    interactions=(DISRUPT_SUPPLY,),
    facts=(
        FactDefinition(
            key="supply_status",
            name="补给线状态",
            value_type=FactValueType.ENUM,
            initial_value="ACTIVE",
            initial_visibility=Visibility.HIDDEN,
            allowed_values=("ACTIVE", "DISRUPTED"),
        ),
    ),
)

STARFIRE_OUTPOST = NodeDefinition(
    key="starfire_outpost",
    name="星火前哨",
    description="需要安全通路与资源投入才能恢复运作。",
    node_type=WorldNodeType.FACILITY,
    initial_access=AccessState.LOCKED,
    initial_visibility=Visibility.KNOWN,
    interactions=(REPAIR,),
    facts=(
        FactDefinition(
            key="outpost_status",
            name="前哨状态",
            value_type=FactValueType.ENUM,
            initial_value="DAMAGED",
            allowed_values=("DAMAGED", "OPERATIONAL", "RESTORED"),
        ),
    ),
)

NORTHERN_TRADE_ROUTE = NodeDefinition(
    key="northern_trade_route",
    name="北方商路",
    description="满足安全、设施和村落支援条件后可以进行通行测试。",
    node_type=WorldNodeType.ROUTE,
    initial_access=AccessState.LOCKED,
    initial_visibility=Visibility.KNOWN,
    interactions=(TEST_TRADE_ROUTE,),
    facts=(
        FactDefinition(
            key="trade_route_status",
            name="商路状态",
            value_type=FactValueType.ENUM,
            initial_value="CLOSED",
            allowed_values=("CLOSED", "OPEN"),
        ),
    ),
)

STARFIRE_WORLD = WorldDefinition(
    key="starfire_command",
    name="星火前哨战略领地",
    interactions=(
        NEGOTIATE_SUPPORT,
        RECONNAISSANCE,
        CLEAR_THREAT,
        DISRUPT_SUPPLY,
        REPAIR,
        TEST_TRADE_ROUTE,
    ),
    nodes=(
        CAPITAL_COUNCIL,
        NORTH_VILLAGE,
        NORTHERN_VALLEY,
        ENEMY_NORTH_SUPPLY_ROUTE,
        STARFIRE_OUTPOST,
        NORTHERN_TRADE_ROUTE,
    ),
    relations=(
        RelationDefinition(
            source_node_key="north_village",
            relation_type=RelationType.SUPPORTS,
            target_node_key="northern_valley",
        ),
        RelationDefinition(
            source_node_key="north_village",
            relation_type=RelationType.SUPPORTS,
            target_node_key="northern_trade_route",
        ),
        RelationDefinition(
            source_node_key="enemy_north_supply_route",
            relation_type=RelationType.SUPPORTS,
            target_node_key="northern_valley",
        ),
        RelationDefinition(
            source_node_key="northern_valley",
            relation_type=RelationType.REVEALS,
            target_node_key="enemy_north_supply_route",
        ),
        RelationDefinition(
            source_node_key="northern_valley",
            relation_type=RelationType.UNLOCKS,
            target_node_key="starfire_outpost",
        ),
        RelationDefinition(
            source_node_key="northern_valley",
            relation_type=RelationType.ENABLES,
            target_node_key="northern_trade_route",
        ),
        RelationDefinition(
            source_node_key="starfire_outpost",
            relation_type=RelationType.ENABLES,
            target_node_key="northern_trade_route",
        ),
    ),
    resources=(
        ResourceDefinition(key="soldiers", name="兵力", initial_value=300),
        ResourceDefinition(key="food", name="粮草", initial_value=100),
        ResourceDefinition(key="gold", name="金币", initial_value=80),
        ResourceDefinition(
            key="morale",
            name="士气",
            initial_value=60,
            minimum=0,
            maximum=100,
        ),
    ),
)
