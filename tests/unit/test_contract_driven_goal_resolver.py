from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from app.agent.generic import (
    GenericGoalResolver,
    _dynamic_goal_action_contract,
    _dynamic_goal_routing_action_catalog,
    _dynamic_goal_routing_state_catalog,
)
from app.agent.provider import (
    DynamicGoalActionRouting,
    DynamicGoalActionRoutingRequest,
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalFamilyRouting,
    DynamicGoalFamilyRoutingRequest,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    DynamicGoalOperationGrounding,
    DynamicGoalOperationGroundingRequest,
    GenericProviderError,
    OperationContractSlot,
)
from app.domain.scenario_v2 import (
    ActionDefinitionV2,
    ActionOperationBindingSource,
    ActionOperationBindingV2,
    ActionParameterType,
    ActionParameterV2,
    ActionSemanticReferenceType,
    ScenarioDefinitionV2,
)
from app.scenarios.validation import ScenarioDefinitionValidator
from tests.scenario_fixtures import LINJIANG_V2_TEST


def _slot(
    slot_key: str,
    expected_type: str,
    status: str,
    *,
    ref_type: str | None = None,
    key: str | None = None,
    value: object | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "slot_key": slot_key,
        "expected_type": expected_type,
        "status": status,
    }
    if ref_type is not None:
        result["ref_type"] = ref_type
    if key is not None:
        result["key"] = key
    if value is not None:
        result["value"] = value
    return result


def _operation(
    action_key: str,
    *,
    target: dict[str, object],
    actor: dict[str, object] | None = None,
    bindings: Iterable[dict[str, object]] = (),
    parameters: Iterable[dict[str, object]] = (),
    references: Iterable[dict[str, str]] = (),
) -> dict[str, object]:
    return {
        "status": "RESOLVED",
        "intent": {
            "action_key": action_key,
            "actor": actor or _slot("actor", "ACTOR", "NOT_SPECIFIED"),
            "target": target,
            "bindings": list(bindings),
            "parameters": list(parameters),
        },
        "supplementary_candidate_refs": list(references),
    }


class _ContractProvider:
    def __init__(
        self,
        *,
        action_key: str = "transport_resource",
        operation_results: Iterable[object] = (),
        family: str = "OPERATION",
        action_status: str = "GROUNDED",
    ) -> None:
        self.action_key = action_key
        self.operation_results = list(operation_results)
        self.family = family
        self.action_status = action_status
        self.operation_requests: list[DynamicGoalOperationGroundingRequest] = []

    def decide_dynamic_goal_family(
        self, request: DynamicGoalFamilyRoutingRequest
    ) -> DynamicGoalFamilyRouting:
        del request
        return DynamicGoalFamilyRouting(family=self.family)

    def route_dynamic_goal_action(
        self, request: DynamicGoalActionRoutingRequest
    ) -> DynamicGoalActionRouting:
        del request
        if self.action_status == "GROUNDED":
            return DynamicGoalActionRouting(action_match="MATCHED", action_key=self.action_key)
        return DynamicGoalActionRouting(
            action_match="NO_MATCH",
            no_match_reason="NO_SEMANTIC_ACTION",
        )

    def ground_dynamic_goal_operation(
        self, request: DynamicGoalOperationGroundingRequest
    ) -> object:
        self.operation_requests.append(request)
        return self.operation_results.pop(0)


@dataclass(frozen=True)
class _RoutingSpec:
    family: str
    action_match: str | None = None
    action_key: str | None = None
    candidate_keys: tuple[str, ...] = ()
    clarification_prompt: str | None = None


class _RoutingProvider(_ContractProvider):
    def __init__(
        self,
        *,
        routing: _RoutingSpec,
        operation_results: Iterable[object] = (),
    ) -> None:
        super().__init__(operation_results=operation_results)
        self.routing = routing
        self.family_requests: list[DynamicGoalFamilyRoutingRequest] = []
        self.routing_requests: list[DynamicGoalActionRoutingRequest] = []

    def decide_dynamic_goal_family(
        self, request: DynamicGoalFamilyRoutingRequest
    ) -> DynamicGoalFamilyRouting:
        self.family_requests.append(request)
        assert self.routing.family in {"STATE", "OPERATION", "AMBIGUOUS"}
        return DynamicGoalFamilyRouting(family=self.routing.family)

    def route_dynamic_goal_action(
        self, request: DynamicGoalActionRoutingRequest
    ) -> DynamicGoalActionRouting:
        self.routing_requests.append(request)
        assert self.routing.action_match is not None
        return DynamicGoalActionRouting(
            action_match=self.routing.action_match,
            action_key=self.routing.action_key,
            candidate_keys=self.routing.candidate_keys,
            clarification_prompt=self.routing.clarification_prompt,
            no_match_reason=(
                "NO_SEMANTIC_ACTION" if self.routing.action_match == "NO_MATCH" else None
            ),
        )

class _LLMAllRoutingProvider(_RoutingProvider):
    def __init__(
        self,
        *,
        grounding: DynamicGoalEntityGrounding,
        operation_results: Iterable[object] = (),
    ) -> None:
        super().__init__(
            routing=_RoutingSpec(
                family="OPERATION",
                action_match="MATCHED",
                action_key="transport_resource",
            ),
            operation_results=operation_results,
        )
        self.grounding = grounding
        self.grounding_requests: list[DynamicGoalEntityGroundingRequest] = []

    def ground_dynamic_goal_entities(
        self, request: DynamicGoalEntityGroundingRequest
    ) -> DynamicGoalEntityGrounding:
        self.grounding_requests.append(request)
        return self.grounding

class _StateRoutingProvider(_RoutingProvider):
    def __init__(self, interpretation: DynamicGoalInterpretation) -> None:
        super().__init__(routing=_RoutingSpec(family="STATE"))
        self.interpretation = interpretation
        self.interpretation_requests: list[DynamicGoalInterpretationRequest] = []
        self.grounding_requests: list[DynamicGoalEntityGroundingRequest] = []

    def ground_dynamic_goal_entities(
        self, request: DynamicGoalEntityGroundingRequest
    ) -> DynamicGoalEntityGrounding:
        self.grounding_requests.append(request)
        requirement = (
            self.interpretation.requirements[0] if self.interpretation.requirements else None
        )
        if requirement is None:
            return DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="NODE", key="central_telecom_hub"),
                )
            )
        if requirement.kind == "RESOURCE_AT_LEAST":
            return DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="REGION", key=requirement.region_key),
                    DynamicGoalCandidateReference(
                        ref_type="RESOURCE", key=requirement.resource_key
                    ),
                )
            )
        if requirement.kind == "FACT":
            return DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="NODE", key=requirement.node_key),
                )
            )
        return DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(
                    ref_type="DERIVED_STATE", key=requirement.derived_key
                ),
            )
        )

    def interpret_dynamic_goal(
        self, request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation:
        self.interpretation_requests.append(request)
        return self.interpretation


def _routing_request(goal: str) -> DynamicGoalActionRoutingRequest:
    provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="NO_MATCH",
        )
    )
    GenericGoalResolver(provider=provider).resolve(goal, LINJIANG_V2_TEST)
    return provider.routing_requests[0]


@pytest.mark.parametrize(
    ("goal", "expected_action"),
    [
        ("从南部滨水区运30个应急燃料到东南高地区", "transport_resource"),
        ("把30个应急燃料运到南部滨水区", "transport_resource"),
        ("修复中央河底隧道", "clear_transport"),
        ("修复中区到北区的路", "clear_transport"),
    ],
)
def test_routing_action_projection_conservatively_retains_relevant_action(
    goal: str,
    expected_action: str,
) -> None:
    request = _routing_request(goal)

    assert expected_action in {item["key"] for item in request.action_catalog}


def test_action_routing_enriches_direct_target_with_public_semantics() -> None:
    request = _routing_request("修复中央河底隧道")
    entities = {(item["ref_type"], item["key"]): item for item in request.relevant_public_entities}

    assert entities[("NODE", "central_river_tunnel")] == {
        "ref_type": "NODE",
        "key": "central_river_tunnel",
        "provenance": "EXACT_USER_MENTION",
        "name": "中央河底隧道",
        "description": "连接中央城区与东部居住区的河底通道。",
        "node_type_key": "transport",
    }


def test_action_routing_uses_same_enrichment_for_facility_target() -> None:
    request = _routing_request("修复中央通信枢纽")
    target = next(
        item for item in request.relevant_public_entities if item["key"] == "central_telecom_hub"
    )

    assert target["name"] == "中央通信枢纽"
    assert target["node_type_key"] == "facility"
    assert target["description"] == "汇聚并转接城市核心通信网络。"
    assert target["provenance"] == "EXACT_USER_MENTION"


def test_action_routing_enriches_topology_target_without_changing_topology() -> None:
    request = _routing_request("修复中区到北区的路")
    target = next(
        item for item in request.relevant_public_entities if item["key"] == "north_service_corridor"
    )

    assert target["name"] == "北部联络通道"
    assert target["node_type_key"] == "transport"
    assert target["provenance"] == "TOPOLOGY_ENRICHED"
    pair = request.public_topology["transport_endpoint_pairs"][0]
    assert pair["entity_key"] == "north_service_corridor"
    assert pair["derived_target"]["key"] == "north_service_corridor"


def test_action_routing_enriches_heterogeneous_public_entities() -> None:
    request = _routing_request("从南部滨水区运30个应急燃料到东南高地区")
    entities = {(item["ref_type"], item["key"]): item for item in request.relevant_public_entities}

    assert entities[("REGION", "south_waterfront_district")]["node_type_key"] == "region"
    assert entities[("REGION", "southeast_heights_district")]["node_type_key"] == "region"
    assert "南部" in entities[("REGION", "south_waterfront_district")]["public_references"]
    assert "东南区" in entities[("REGION", "southeast_heights_district")]["public_references"]
    assert entities[("RESOURCE", "emergency_fuel")]["name"] == "应急燃料"
    assert "node_type_key" not in entities[("RESOURCE", "emergency_fuel")]


def test_action_routing_semantic_entities_are_public_safe() -> None:
    request = _routing_request("修复中央河底隧道")
    forbidden = {
        "facts",
        "rules",
        "planning",
        "planning_hints",
        "runtime_preconditions",
        "inventory",
        "legality",
        "objective_completion",
        "behavior",
        "parameters",
        "operation_binding_contract",
    }

    assert request.relevant_public_entities
    assert all(forbidden.isdisjoint(item) for item in request.relevant_public_entities)


def test_routing_action_catalog_keeps_all_public_actions_for_transport_target() -> None:
    request = _routing_request("修复中央河底隧道")
    action_keys = {item["key"] for item in request.action_catalog}

    assert action_keys == {item.key for item in LINJIANG_V2_TEST.actions}


def test_routing_action_catalog_keeps_all_public_actions_for_facility_target() -> None:
    request = _routing_request("让中央通信枢纽恢复运行")
    action_keys = {item["key"] for item in request.action_catalog}

    assert action_keys == {item.key for item in LINJIANG_V2_TEST.actions}


def test_canonical_invocation_contract_preserves_sp1_and_a1_slot_semantics() -> None:
    actions = {item.key: item for item in LINJIANG_V2_TEST.actions}
    supply = _dynamic_goal_action_contract(actions["supply_power"])
    transport = _dynamic_goal_action_contract(actions["transport_resource"])

    supply_source = next(
        item
        for item in supply["parameters"]
        if item["slot_key"] == "source_key"  # type: ignore[union-attr]
    )
    assert supply_source == {
        "slot_key": "source_key",
        "name": "供电来源",
        "storage_channel": "parameter",
        "logical_role": "source",
        "scalar_value_type": "STRING",
        "semantic_reference_type": "NODE",
        "expected_type": "NODE",
        "goal_required": False,
        "runtime_required": True,
        "cardinality": "ONE",
        "minimum": None,
        "maximum": None,
        "allowed_values": [],
    }
    assert supply["relation_semantics"] == {
        "source_relation_type_key": "supplies_power_to",
        "source_storage_channel": "parameter",
        "source_slot_key": "source_key",
        "target_slot_key": "target",
        "direction": "SOURCE_TO_TARGET",
    }

    transport_slots = {
        item["slot_key"]: item
        for item in transport["slots"]  # type: ignore[union-attr]
    }
    assert transport_slots["source_region"]["semantic_reference_type"] == "REGION"
    assert transport_slots["source_region"]["logical_role"] == "source"
    assert transport_slots["resource_key"]["semantic_reference_type"] == "RESOURCE"
    assert transport_slots["amount"]["scalar_value_type"] == "INTEGER"
    assert transport_slots["amount"]["semantic_reference_type"] is None


def test_relation_source_requires_one_typed_source_slot() -> None:
    payload = LINJIANG_V2_TEST.model_dump(mode="json")
    supply = next(item for item in payload["actions"] if item["key"] == "supply_power")
    supply["parameters"][0].pop("semantic_reference_type")

    result = ScenarioDefinitionValidator().validate(payload)

    assert not result.passed
    assert "SCENARIO_ACTION_RELATION_SOURCE_SLOT_INVALID" in {item.code for item in result.issues}


def test_routing_action_projection_assigns_supply_source_and_target_roles() -> None:
    request = _routing_request("从东南应急电源站向东部配电站送电")
    action_keys = {item["key"] for item in request.action_catalog}
    source = LINJIANG_V2_TEST.world.node("southeast_emergency_power_station")

    assert source is not None
    assert "power_targetable" in source.interaction_keys
    assert "supply_power" in action_keys


def test_linjiang_goal_required_slot_sets_are_exact_and_data_driven() -> None:
    expected = {
        "clear_transport": {"target"},
        "deploy_heavy_engineering_support": {"target"},
        "inspect": {"target"},
        "relay_message": {"target"},
        "repair_facility": {"target"},
        "supply_power": {"target"},
        "survey_resources": {"target"},
        "transport_resource": {"target", "source_region", "amount", "resource_key"},
        "travel": {"actor", "target"},
        "receive_external_relief_supplies": {"target"},
        "generate_power": {"target"},
    }

    assert {
        item.key: set(item.goal_required_slots) for item in LINJIANG_V2_TEST.actions
    } == expected


def test_canonical_invocation_contract_reflects_goal_required_slot_metadata() -> None:
    for action in LINJIANG_V2_TEST.actions:
        contract = _dynamic_goal_action_contract(action)
        expected = set(action.goal_required_slots)
        slots = contract["slots"]
        assert isinstance(slots, list)
        assert {
            str(item["slot_key"]) for item in slots if item["goal_required"] is True
        } == expected
        assert all(item["goal_required"] is (item["slot_key"] in expected) for item in slots)


def test_invalid_goal_required_slot_fails_scenario_validation() -> None:
    payload = LINJIANG_V2_TEST.model_dump(mode="json")
    action = next(item for item in payload["actions"] if item["key"] == "inspect")
    action["goal_required_slots"] = ["does_not_exist"]

    result = ScenarioDefinitionValidator().validate(payload)

    assert not result.passed
    assert any("Goal required slots" in item.message for item in result.issues)


def test_routing_action_projection_rejects_impossible_role_assignment() -> None:
    public_action_keys = {item.key for item in LINJIANG_V2_TEST.actions}
    catalog = _dynamic_goal_routing_action_catalog(
        LINJIANG_V2_TEST,
        public_action_keys,
        (
            DynamicGoalCandidateReference(
                ref_type="NODE",
                key="southeast_emergency_power_station",
                provenance="EXACT_USER_MENTION",
            ),
            DynamicGoalCandidateReference(
                ref_type="NODE",
                key="central_telecom_hub",
                provenance="EXACT_USER_MENTION",
            ),
        ),
    )

    assert "supply_power" not in {item["key"] for item in catalog}


def test_role_aware_projection_preserves_transport_topology_and_direct_targets() -> None:
    transport = _routing_request("从南部滨水区运30个应急燃料到东南高地区")
    topology = _routing_request("修复中区到北区的路")
    direct = _routing_request("修复中央河底隧道")

    assert "transport_resource" in {item["key"] for item in transport.action_catalog}
    assert "clear_transport" in {item["key"] for item in topology.action_catalog}
    assert topology.public_topology["transport_endpoint_pairs"]
    assert "clear_transport" in {item["key"] for item in direct.action_catalog}


def test_routing_action_projection_keeps_transport_without_resource_alias() -> None:
    request = _routing_request("从东南高地区运30个通用部件到南部滨水区")
    action_keys = {item["key"] for item in request.action_catalog}

    assert "transport_resource" in action_keys


def test_routing_action_projection_without_refs_does_not_aggressively_filter() -> None:
    public_action_keys = {item.key for item in LINJIANG_V2_TEST.actions}
    catalog = _dynamic_goal_routing_action_catalog(
        LINJIANG_V2_TEST,
        public_action_keys,
        (),
    )

    assert {item["key"] for item in catalog} == public_action_keys


def test_routing_action_projection_uses_semantic_description_not_planning_or_rules() -> None:
    public_action_keys = {item.key for item in LINJIANG_V2_TEST.actions}
    catalog = _dynamic_goal_routing_action_catalog(
        LINJIANG_V2_TEST,
        public_action_keys,
        (),
    )
    actions = {item.key: item for item in LINJIANG_V2_TEST.actions}

    assert all(item["description"] == actions[str(item["key"])].description for item in catalog)
    assert all("planning" not in item and "planning_hints" not in item for item in catalog)
    assert all("rules" not in item and "runtime_preconditions" not in item for item in catalog)


def test_routing_action_projection_has_no_goal_lexical_matcher() -> None:
    public_action_keys = {item.key for item in LINJIANG_V2_TEST.actions}
    catalog = _dynamic_goal_routing_action_catalog(
        LINJIANG_V2_TEST,
        public_action_keys,
        (
            DynamicGoalCandidateReference(
                ref_type="NODE",
                key="central_telecom_hub",
                provenance="EXACT_USER_MENTION",
            ),
        ),
    )
    contracts = {
        item["key"]: _dynamic_goal_action_contract(
            next(action for action in LINJIANG_V2_TEST.actions if action.key == item["key"])
        )
        for item in catalog
    }

    assert "repair_facility" in contracts
    assert "clear_transport" not in contracts


def test_routing_fact_catalog_uses_authored_goal_alias() -> None:
    catalog = _dynamic_goal_routing_state_catalog(
        "恢复中央通信能力", None, None, LINJIANG_V2_TEST, ()
    )

    assert any(
        item["kind"] == "FACT"
        and item["node_key"] == "central_telecom_hub"
        and item["fact_key"] == "operational"
        for item in catalog
    )


def test_routing_derived_state_catalog_uses_goal_addressable_state() -> None:
    catalog = _dynamic_goal_routing_state_catalog(
        "恢复北部基础工程支援", None, None, LINJIANG_V2_TEST, ()
    )

    assert any(
        item["kind"] == "DERIVED_STATE" and item["key"] == "north_basic_engineering_support"
        for item in catalog
    )


@pytest.mark.parametrize(
    ("goal", "requirement", "expected_kind"),
    [
        (
            "让南部滨水区至少有30个应急燃料",
            {
                "kind": "RESOURCE_AT_LEAST",
                "region_key": "south_waterfront_district",
                "resource_key": "emergency_fuel",
                "minimum": 30,
            },
            "RESOURCE_AT_LEAST",
        ),
        (
            "让中央通信枢纽恢复运行",
            {
                "kind": "FACT",
                "node_key": "central_telecom_hub",
                "fact_key": "operational",
                "accepted_values": [True],
            },
            "FACT",
        ),
        (
            "恢复北部基础工程支援",
            {
                "kind": "DERIVED_STATE",
                "derived_key": "north_basic_engineering_support",
                "accepted_values": ["AVAILABLE"],
            },
            "DERIVED_STATE",
        ),
    ],
)
def test_frozen_state_routes_directly_to_typed_state_interpretation(
    goal: str,
    requirement: dict[str, object],
    expected_kind: str,
) -> None:
    provider = _StateRoutingProvider(
        DynamicGoalInterpretation.model_validate(
            {"status": "RESOLVED", "requirements": [requirement]}
        )
    )

    resolution = GenericGoalResolver(provider=provider).resolve(goal, LINJIANG_V2_TEST)

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].kind == expected_kind
    assert resolution.provider_observation is not None
    assert resolution.provider_observation.get("rejection_code") is None
    request = provider.interpretation_requests[0]
    assert request.frozen_family == "STATE"
    assert request.intent is None
    assert request.grounded_operation is None
    assert request.ontology["world"]["actions"] == []
    assert "ACTION_COMPLETED" not in request.ontology["goal_language"]["requirement_kinds"]
    assert len(provider.family_requests) == 1
    assert provider.routing_requests == []


def test_naturally_dual_reading_completes_along_either_frozen_branch() -> None:
    goal = "让中区到东区的路恢复通行"
    state_provider = _StateRoutingProvider(
        DynamicGoalInterpretation.model_validate(
            {
                "status": "RESOLVED",
                "requirements": [
                    {
                        "kind": "FACT",
                        "node_key": "central_river_tunnel",
                        "fact_key": "passable",
                        "accepted_values": [True],
                    }
                ],
            }
        )
    )
    operation_provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="clear_transport",
        ),
        operation_results=[_clear_transport_operation("central_river_tunnel")],
    )

    state = GenericGoalResolver(provider=state_provider).resolve(goal, LINJIANG_V2_TEST)
    operation = GenericGoalResolver(provider=operation_provider).resolve(goal, LINJIANG_V2_TEST)

    assert state.status == "RESOLVED"
    assert state.dynamic_requirements[0].kind == "FACT"
    assert state_provider.routing_requests == []
    assert operation.status == "RESOLVED"
    assert operation.dynamic_requirements[0].kind == "ACTION_COMPLETED"
    assert operation.dynamic_requirements[0].action_key == "clear_transport"
    assert len(operation_provider.routing_requests) == 1


def test_true_state_ambiguity_clarifies_inside_frozen_state_branch() -> None:
    provider = _StateRoutingProvider(
        DynamicGoalInterpretation(
            status="NEEDS_CLARIFICATION",
            clarification_prompt="Which public state should hold?",
        )
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "让中央通信枢纽恢复到某种状态", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.clarification_prompt == "Which public state should hold?"
    assert provider.routing_requests == []


def test_routing_topology_reference_remains_non_exact_candidate_evidence() -> None:
    request = _routing_request("从南部滨水区运30个应急燃料到东南高地区")

    assert {
        (item.ref_type, item.key, item.provenance) for item in request.deterministic_candidate_refs
    } >= {
        ("NODE", "southeast_access_corridor", "TOPOLOGY_ENRICHED"),
    }


def test_action_routing_exposes_only_goal_relevant_topology_relationship() -> None:
    request = _routing_request("修复中区到北区的路")

    assert len(request.public_topology["transport_endpoint_pairs"]) == 1
    pair = request.public_topology["transport_endpoint_pairs"][0]
    assert pair["entity_key"] == "north_service_corridor"
    assert set(pair["endpoint_region_keys"]) == {
        "central_district",
        "north_industrial_district",
    }
    assert pair["derived_target"] == {
        "ref_type": "NODE",
        "key": "north_service_corridor",
        "name": "北部联络通道",
        "node_type_key": "transport",
        "description": "连接北部工业区与中央城区的区域联络通道。",
        "provenance": "TOPOLOGY_ENRICHED",
    }
    assert len(request.public_topology["relations"]) == 2
    assert {item["target_node_key"] for item in request.public_topology["relations"]} == {
        "central_district",
        "north_industrial_district",
    }


def test_action_routing_without_derived_pair_has_empty_topology_context() -> None:
    request = _routing_request("修复中央河底隧道")

    assert request.public_topology == {"relations": [], "transport_endpoint_pairs": []}


def test_lexical_candidates_do_not_reject_semantically_grounded_operation() -> None:
    provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="clear_transport",
        ),
        operation_results=[_clear_transport_operation("southeast_access_corridor")],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从南部滨水区运30个应急燃料到东南高地区",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].target_key == "southeast_access_corridor"


def _transport_operation(*, amount: object = 12) -> dict[str, object]:
    return _operation(
        "transport_resource",
        target=_slot("target", "REGION", "GROUNDED", ref_type="REGION", key="central_district"),
        bindings=[
            _slot(
                "source_region",
                "REGION",
                "GROUNDED",
                ref_type="REGION",
                key="west_logistics_district",
            )
        ],
        parameters=[
            _slot("amount", "INTEGER", "GROUNDED", value=amount),
            _slot(
                "resource_key",
                "RESOURCE",
                "GROUNDED",
                ref_type="RESOURCE",
                key="emergency_fuel",
            ),
        ],
        references=[
            {"ref_type": "REGION", "key": "central_district"},
            {"ref_type": "RESOURCE", "key": "emergency_fuel"},
        ],
    )


def test_vnext_semantic_grounding_keeps_semantic_refs_separate_from_exact_refs() -> None:
    grounding = DynamicGoalEntityGrounding(
        candidate_refs=(
            DynamicGoalCandidateReference(ref_type="REGION", key="central_district"),
            DynamicGoalCandidateReference(ref_type="REGION", key="south_waterfront_district"),
            DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
        )
    )
    operation = _operation(
        "transport_resource",
        target=_slot("target", "REGION", "GROUNDED", ref_type="REGION", key="central_district"),
        bindings=[
            _slot(
                "source_region",
                "REGION",
                "GROUNDED",
                ref_type="REGION",
                key="south_waterfront_district",
            )
        ],
        parameters=[
            _slot("amount", "INTEGER", "GROUNDED", value=30),
            _slot(
                "resource_key",
                "RESOURCE",
                "GROUNDED",
                ref_type="RESOURCE",
                key="emergency_fuel",
            ),
        ],
    )
    provider = _LLMAllRoutingProvider(
        grounding=grounding,
        operation_results=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "unregistered semantic transport wording", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert provider.grounding_requests[0].deterministic_candidate_refs == ()
    assert provider.family_requests[0].deterministic_candidate_refs == ()
    assert {
        (item.ref_type, item.key) for item in provider.family_requests[0].semantic_candidate_refs
    } >= {
        ("REGION", "central_district"),
        ("REGION", "south_waterfront_district"),
        ("RESOURCE", "emergency_fuel"),
    }
    assert provider.operation_requests[0].deterministic_candidate_refs == ()
    assert {
        (item.ref_type, item.key) for item in provider.operation_requests[0].semantic_candidate_refs
    } >= {
        ("REGION", "central_district"),
        ("REGION", "south_waterfront_district"),
        ("RESOURCE", "emergency_fuel"),
    }


def test_llm_all_grounding_rejects_invented_public_identity() -> None:
    provider = _LLMAllRoutingProvider(
        grounding=DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(ref_type="REGION", key="invented_region"),
            )
        )
    )

    with pytest.raises(GenericProviderError, match="exact-Version validation"):
        GenericGoalResolver(provider=provider).resolve(
            "an unregistered semantic reference", LINJIANG_V2_TEST
        )
    assert [item.recovery_attempt for item in provider.grounding_requests] == [0, 1]


def test_semantic_grounding_status_cannot_short_circuit_product_resolution() -> None:
    provider = _LLMAllRoutingProvider(
        grounding=DynamicGoalEntityGrounding(
            status="NEEDS_CLARIFICATION",
            clarification_prompt="Which public reference?",
        ),
        operation_results=[DynamicGoalOperationGrounding(status="UNSUPPORTED")],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "an ambiguous public reference", LINJIANG_V2_TEST
    )

    assert resolution.status == "UNSUPPORTED"
    assert len(provider.family_requests) == 1
    assert len(provider.routing_requests) == 1
    assert len(provider.operation_requests) == 1


def test_llm_all_grounding_does_not_add_unmentioned_refs() -> None:
    provider = _LLMAllRoutingProvider(
        grounding=DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
            )
        ),
        operation_results=[_transport_operation()],
    )

    GenericGoalResolver(provider=provider).resolve(
        "a goal mentioning only emergency fuel", LINJIANG_V2_TEST
    )

    request = provider.family_requests[0]
    assert request.deterministic_candidate_refs == ()
    refs = {(item.ref_type, item.key) for item in request.semantic_candidate_refs}
    assert refs == {("RESOURCE", "emergency_fuel")}


def test_operation_family_resolves_from_selected_action_contract() -> None:
    provider = _ContractProvider(operation_results=[_transport_operation()])

    resolution = GenericGoalResolver(provider=provider).resolve(
        "执行这项运输任务", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert requirement.kind == "ACTION_COMPLETED"
    assert requirement.action_key == "transport_resource"
    assert requirement.target_key == "central_district"
    assert [(item.role, item.value) for item in requirement.binding_constraints] == [
        ("source_region", "west_logistics_district")
    ]
    assert requirement.parameter_constraints == {
        "resources": [{"amount": 12, "resource_key": "emergency_fuel"}]
    }
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["frozen_family"] == "OPERATION"
    assert any(
        item.get("stage") == "ACTION_ROUTING"
        and item.get("action_key") == "transport_resource"
        and item.get("result") == "MATCHED"
        for item in resolution.provider_observation["stages"]
    )
    assert resolution.provider_observation["attempt"] == 0


def test_supply_power_source_is_a_canonical_node_through_formal_goal() -> None:
    operation = _operation(
        "supply_power",
        target=_slot(
            "target",
            "NODE",
            "GROUNDED",
            ref_type="NODE",
            key="east_distribution_station",
        ),
        parameters=[
            _slot(
                "source_key",
                "NODE",
                "GROUNDED",
                ref_type="NODE",
                key="southeast_emergency_power_station",
            )
        ],
    )
    provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="supply_power",
        ),
        operation_results=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从东南应急电源站向东部配电站送电",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert requirement.action_key == "supply_power"
    assert requirement.target_key == "east_distribution_station"
    assert requirement.parameter_constraints == {"source_key": "southeast_emergency_power_station"}
    source_contract = provider.operation_requests[0].action_contract["parameters"][0]  # type: ignore[index]
    assert source_contract["scalar_value_type"] == "STRING"  # type: ignore[index]
    assert source_contract["semantic_reference_type"] == "NODE"  # type: ignore[index]
    assert source_contract["expected_type"] == "NODE"  # type: ignore[index]


def test_supply_power_source_display_value_is_not_a_valid_grounded_node() -> None:
    with pytest.raises(ValidationError):
        OperationContractSlot.model_validate(
            _slot(
                "source_key",
                "NODE",
                "GROUNDED",
                value="东南应急电源站",
            )
        )


def test_invalid_scalar_shape_gets_one_bounded_recovery() -> None:
    malformed = _transport_operation(amount=12)
    malformed["intent"]["parameters"][0]["value"] = {"amount": 12}  # type: ignore[index]
    provider = _ContractProvider(operation_results=[malformed, _transport_operation(amount=12)])

    resolution = GenericGoalResolver(provider=provider).resolve(
        "执行这项运输任务", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.operation_requests) == 2
    assert provider.operation_requests[1].recovery_attempt == 1
    assert provider.operation_requests[1].recovery_feedback
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["attempt"] == 1
    assert any(
        item.get("stage") == "OPERATION_GROUNDING"
        and item.get("result") == "RESOLVED"
        for item in resolution.provider_observation["stages"]
    )


def test_explicit_source_binding_is_preserved() -> None:
    operation = _transport_operation()
    operation["intent"]["bindings"] = [  # type: ignore[index]
        _slot(
            "source_region",
            "REGION",
            "GROUNDED",
            ref_type="REGION",
            key="north_industrial_district",
        )
    ]
    operation["supplementary_candidate_refs"].append(  # type: ignore[union-attr]
        {"ref_type": "REGION", "key": "north_industrial_district"}
    )
    provider = _ContractProvider(operation_results=[operation])

    resolution = GenericGoalResolver(provider=provider).resolve(
        "执行这项运输任务", LINJIANG_V2_TEST
    )

    requirement = resolution.dynamic_requirements[0]
    assert [(item.role, item.value) for item in requirement.binding_constraints] == [
        ("source_region", "north_industrial_district")
    ]


def _obsolete_test_topology_can_compose_one_transport_target_from_region_pair() -> None:
    operation = _operation(
        "clear_transport",
        target=_slot("target", "NODE", "UNRESOLVED"),
        references=[
            {"ref_type": "REGION", "key": "central_district"},
            {"ref_type": "REGION", "key": "north_industrial_district"},
        ],
    )
    provider = _ContractProvider(action_key="clear_transport", operation_results=[operation])

    resolution = GenericGoalResolver(provider=provider).resolve(
        "打通中央城区与北部工业区之间的道路", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].action_key == "clear_transport"
    assert resolution.dynamic_requirements[0].target_key == "north_service_corridor"


def _clear_transport_operation(target_key: str) -> dict[str, object]:
    return _operation(
        "clear_transport",
        target=_slot(
            "target",
            "NODE",
            "GROUNDED",
            ref_type="NODE",
            key=target_key,
        ),
    )


def test_grounded_target_equal_to_unique_topology_consumes_endpoint_regions() -> None:
    provider = _ContractProvider(
        action_key="clear_transport",
        operation_results=[_clear_transport_operation("central_river_tunnel")],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "repair central_district to east_residential_district",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].target_key == "central_river_tunnel"


def _obsolete_test_grounded_target_different_from_unique_topology_keeps_identity_conflict() -> None:
    provider = _ContractProvider(
        action_key="clear_transport",
        operation_results=[_clear_transport_operation("north_service_corridor")],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "repair central_district to east_residential_district",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "CANONICAL_IDENTITY_CONFLICT"


def _obsolete_test_grounded_target_without_unique_topology_cannot_consume_regions() -> None:
    provider = _ContractProvider(
        action_key="clear_transport",
        operation_results=[_clear_transport_operation("central_river_tunnel")],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "repair central_district to southeast_heights_district",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "CANONICAL_IDENTITY_CONFLICT"


@pytest.mark.parametrize(
    "extra_identity",
    ["north_industrial_district", "emergency_fuel"],
)
def _obsolete_test_topology_consumption_does_not_hide_unrelated_exact_identity(
    extra_identity: str,
) -> None:
    provider = _ContractProvider(
        action_key="clear_transport",
        operation_results=[_clear_transport_operation("central_river_tunnel")],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "repair central_district to east_residential_district " + extra_identity,
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "CANONICAL_IDENTITY_CONFLICT"


def test_direct_grounded_node_does_not_require_topology_consumption() -> None:
    provider = _ContractProvider(
        action_key="clear_transport",
        operation_results=[_clear_transport_operation("central_river_tunnel")],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "repair central_river_tunnel", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].target_key == "central_river_tunnel"


def test_unresolved_action_returns_typed_clarification_without_grounding() -> None:
    provider = _ContractProvider(action_status="UNRESOLVED")

    resolution = GenericGoalResolver(provider=provider).resolve("做一下那个任务", LINJIANG_V2_TEST)

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "ACTION_NO_MATCH"
    assert provider.operation_requests == []


def test_unresolved_explicit_resource_returns_typed_clarification() -> None:
    operation = _operation(
        "transport_resource",
        target=_slot("target", "REGION", "NOT_SPECIFIED"),
        bindings=[_slot("source_region", "REGION", "NOT_SPECIFIED")],
        parameters=[
            _slot("amount", "INTEGER", "NOT_SPECIFIED"),
            _slot("resource_key", "RESOURCE", "UNRESOLVED"),
        ],
    )
    provider = _ContractProvider(operation_results=[operation])

    resolution = GenericGoalResolver(provider=provider).resolve(
        "运输某种指定但无法识别的物资", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "EXPLICIT_CONSTRAINT_UNRESOLVED"


def _obsolete_test_exact_canonical_resource_cannot_be_replaced_by_semantic_grounding() -> None:
    operation = _transport_operation()
    operation["intent"]["parameters"][1]["key"] = "general_engineering_parts"  # type: ignore[index]
    operation["supplementary_candidate_refs"] = [
        {"ref_type": "REGION", "key": "central_district"},
        {"ref_type": "RESOURCE", "key": "general_engineering_parts"},
    ]
    provider = _ContractProvider(operation_results=[operation])

    resolution = GenericGoalResolver(provider=provider).resolve(
        "transport_resource emergency_fuel", LINJIANG_V2_TEST
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "CANONICAL_IDENTITY_CONFLICT"


def test_advisory_candidates_do_not_narrow_operation_public_context() -> None:
    operation = _operation(
        "transport_resource",
        target=_slot(
            "target",
            "REGION",
            "GROUNDED",
            ref_type="REGION",
            key="southeast_heights_district",
        ),
        bindings=[
            _slot(
                "source_region",
                "REGION",
                "GROUNDED",
                ref_type="REGION",
                key="south_waterfront_district",
            )
        ],
        parameters=[
            _slot("amount", "INTEGER", "GROUNDED", value=30),
            _slot(
                "resource_key",
                "RESOURCE",
                "GROUNDED",
                ref_type="RESOURCE",
                key="emergency_fuel",
            ),
        ],
    )
    provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="transport_resource",
        ),
        operation_results=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从南部滨水区运30个应急燃料到东南高地区", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert requirement.action_key == "transport_resource"
    assert requirement.target_key == "southeast_heights_district"
    assert [(item.role, item.value) for item in requirement.binding_constraints] == [
        ("source_region", "south_waterfront_district")
    ]
    assert requirement.parameter_constraints == {
        "resources": [{"amount": 30, "resource_key": "emergency_fuel"}]
    }
    assert len(provider.routing_requests) == 1
    assert len(provider.operation_requests) == 1
    request = provider.operation_requests[0]
    provenance = {
        (item.ref_type, item.key): item.provenance for item in request.deterministic_candidate_refs
    }
    assert provenance == {
        ("NODE", "southeast_access_corridor"): "TOPOLOGY_ENRICHED",
        ("REGION", "south_waterfront_district"): "EXACT_USER_MENTION",
        ("REGION", "southeast_heights_district"): "EXACT_USER_MENTION",
        ("RESOURCE", "emergency_fuel"): "EXACT_USER_MENTION",
    }
    assert {item["ref_type"] for item in request.public_references} == {
        "REGION",
        "RESOURCE",
    }
    assert {(item["ref_type"], item["key"]) for item in request.public_references} >= {
        ("REGION", "south_waterfront_district"),
        ("REGION", "southeast_heights_district"),
        ("RESOURCE", "emergency_fuel"),
    }
    assert not any(
        item["ref_type"] in {"ACTION", "ACTOR", "DERIVED_STATE", "NODE"}
        for item in request.public_references
    )
    assert request.public_topology == {"relations": [], "transport_endpoint_pairs": []}


def test_semantic_routing_distinguishes_action_ambiguity_from_no_match() -> None:
    ambiguous = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="AMBIGUOUS",
            candidate_keys=("inspect", "survey_resources"),
        )
    )
    ambiguous_resolution = GenericGoalResolver(provider=ambiguous).resolve(
        "检查一下", LINJIANG_V2_TEST
    )
    assert ambiguous_resolution.status == "NEEDS_CLARIFICATION"
    assert ambiguous_resolution.source == "ACTION_AMBIGUOUS"
    assert ambiguous_resolution.candidate_keys == ("inspect", "survey_resources")

    no_match = _RoutingProvider(
        routing=_RoutingSpec(family="OPERATION", action_match="NO_MATCH")
    )
    no_match_resolution = GenericGoalResolver(provider=no_match).resolve(
        "执行场景没有的操作", LINJIANG_V2_TEST
    )
    assert no_match_resolution.status == "UNSUPPORTED"
    assert no_match_resolution.source == "ACTION_NO_MATCH"
    assert no_match_resolution.provider_observation is not None
    assert no_match_resolution.provider_observation["no_match_reason"] == ("NO_SEMANTIC_ACTION")


def test_semantic_routing_schema_failure_gets_one_structural_recovery() -> None:
    class _RecoveringRoutingProvider(_RoutingProvider):
        def route_dynamic_goal_action(
            self, request: DynamicGoalActionRoutingRequest
        ) -> DynamicGoalActionRouting:
            self.routing_requests.append(request)
            if len(self.routing_requests) == 1:
                raise GenericProviderError(
                    "PROVIDER_SCHEMA_INVALID",
                    "invalid routing shape",
                    validation_diagnostics=({"code": "missing", "location": "action_match"},),
                )
            assert self.routing.action_match is not None
            return DynamicGoalActionRouting(
                action_match=self.routing.action_match,
                action_key=self.routing.action_key,
                candidate_keys=self.routing.candidate_keys,
            )

    provider = _RecoveringRoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="transport_resource",
        ),
        operation_results=[_transport_operation()],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "run transport_resource", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.routing_requests) == 2
    assert provider.routing_requests[1].recovery_attempt == 1
    assert provider.routing_requests[1].recovery_feedback == (
        {"code": "missing", "location": "action_match"},
    )


def test_semantic_routing_recovery_cannot_change_preserved_match() -> None:
    class _RegressingRoutingProvider(_RoutingProvider):
        def route_dynamic_goal_action(
            self, request: DynamicGoalActionRoutingRequest
        ) -> DynamicGoalActionRouting:
            self.routing_requests.append(request)
            if len(self.routing_requests) == 1:
                raise GenericProviderError(
                    "PROVIDER_SCHEMA_INVALID",
                    "invalid routing shape",
                    validation_diagnostics=(
                        {
                            "code": "MATCHED_HAS_CANDIDATE_KEYS",
                            "field_path": "candidate_keys",
                            "expected_candidate_keys": [],
                            "preserve": {
                                "family": "OPERATION",
                                "action_match": "MATCHED",
                                "action_key": "transport_resource",
                            },
                            "fix_only": ["candidate_keys"],
                        },
                    ),
                )
            return DynamicGoalActionRouting(
                action_match="NO_MATCH",
                no_match_reason="NO_SEMANTIC_ACTION",
            )

    provider = _RegressingRoutingProvider(
        routing=_RoutingSpec(family="OPERATION", action_match="NO_MATCH")
    )

    with pytest.raises(GenericProviderError) as captured:
        GenericGoalResolver(provider=provider).resolve("run transport_resource", LINJIANG_V2_TEST)

    assert captured.value.validation_diagnostics == (
        {
            "code": "RECOVERY_CHANGED_PRESERVED_FIELD",
            "field_path": "action_match",
        },
        {
            "code": "RECOVERY_CHANGED_PRESERVED_FIELD",
            "field_path": "action_key",
        },
    )
    assert len(provider.routing_requests) == 2


def test_routed_transport_without_required_source_clarifies() -> None:
    operation = _operation(
        "transport_resource",
        target=_slot(
            "target",
            "REGION",
            "GROUNDED",
            ref_type="REGION",
            key="south_waterfront_district",
        ),
        bindings=[_slot("source_region", "REGION", "NOT_SPECIFIED")],
        parameters=[
            _slot("amount", "INTEGER", "GROUNDED", value=30),
            _slot(
                "resource_key",
                "RESOURCE",
                "GROUNDED",
                ref_type="RESOURCE",
                key="emergency_fuel",
            ),
        ],
    )
    provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="transport_resource",
        ),
        operation_results=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个应急燃料运到南部滨水区", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["diagnostics"]["missing_slot_keys"] == ["source_region"]


def test_advisory_region_pair_does_not_create_authoritative_topology_target() -> None:
    provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="clear_transport",
        ),
        operation_results=[
            _operation(
                "clear_transport",
                target=_slot("target", "NODE", "UNRESOLVED"),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "repair central_district to north_industrial_district", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    topology = provider.operation_requests[0].public_topology
    assert topology["transport_endpoint_pairs"] == []
    assert topology["relations"] == []


@pytest.mark.parametrize(
    ("goal", "expected_refs", "topology_key"),
    [
        (
            "从东南区运30个应急燃料到南部",
            {
                ("REGION", "southeast_heights_district"),
                ("REGION", "south_waterfront_district"),
                ("RESOURCE", "emergency_fuel"),
            },
            "southeast_access_corridor",
        ),
        (
            "修复中区到北区的路",
            {("REGION", "central_district"), ("REGION", "north_industrial_district")},
            "north_service_corridor",
        ),
        (
            "修复中区到东区的路",
            {("REGION", "central_district"), ("REGION", "east_residential_district")},
            "central_river_tunnel",
        ),
    ],
)
def test_approved_references_feed_routing_and_existing_topology(
    goal: str,
    expected_refs: set[tuple[str, str]],
    topology_key: str,
) -> None:
    request = _routing_request(goal)
    actual = {(item.ref_type, item.key) for item in request.deterministic_candidate_refs}

    assert expected_refs <= actual
    assert ("NODE", topology_key) in actual


@pytest.mark.parametrize(
    ("goal", "resource_key"),
    [
        ("从东南高地区运30个通用部件到南部滨水区", "general_engineering_parts"),
        ("从东南高地区运30个电力部件到南部滨水区", "electrical_repair_parts"),
    ],
)
def test_specific_resource_reference_is_deterministic(goal: str, resource_key: str) -> None:
    request = _routing_request(goal)

    assert ("RESOURCE", resource_key, "EXACT_USER_MENTION") in {
        (item.ref_type, item.key, item.provenance) for item in request.deterministic_candidate_refs
    }


def test_unregistered_resource_expression_keeps_llm_semantic_grounding_open() -> None:
    operation = _operation(
        "transport_resource",
        target=_slot(
            "target",
            "REGION",
            "GROUNDED",
            ref_type="REGION",
            key="south_waterfront_district",
        ),
        bindings=[
            _slot(
                "source_region",
                "REGION",
                "GROUNDED",
                ref_type="REGION",
                key="southeast_heights_district",
            )
        ],
        parameters=[
            _slot("amount", "INTEGER", "GROUNDED", value=30),
            _slot(
                "resource_key",
                "RESOURCE",
                "GROUNDED",
                ref_type="RESOURCE",
                key="electrical_repair_parts",
            ),
        ],
        references=[
            {"ref_type": "RESOURCE", "key": "electrical_repair_parts"},
        ],
    )
    provider = _RoutingProvider(
        routing=_RoutingSpec(
            family="OPERATION",
            action_match="MATCHED",
            action_key="transport_resource",
        ),
        operation_results=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从东南高地区运30个电力修理零件到南部滨水区", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    request = provider.operation_requests[0]
    assert not any(item.ref_type == "RESOURCE" for item in request.deterministic_candidate_refs)
    resource = next(
        item
        for item in request.public_references
        if item["ref_type"] == "RESOURCE" and item["key"] == "electrical_repair_parts"
    )
    assert set(resource["public_references"]) == {
        "电力部件",
        "电力维修零件",
        "电力维修材料",
    }
    supplemented = DynamicGoalOperationGrounding.model_validate(operation)
    assert supplemented.supplementary_candidate_refs[0].provenance == "LLM_SUPPLEMENTED"
    assert len(provider.routing_requests) == 1


class _StateProvider(_ContractProvider):
    def __init__(self) -> None:
        super().__init__(family="STATE")
        self.interpretation_requests: list[DynamicGoalInterpretationRequest] = []

    def ground_dynamic_goal_entities(
        self, request: DynamicGoalEntityGroundingRequest
    ) -> DynamicGoalEntityGrounding:
        del request
        return DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(ref_type="NODE", key="central_telecom_hub"),
            )
        )

    def interpret_dynamic_goal(
        self, request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation:
        self.interpretation_requests.append(request)
        return DynamicGoalInterpretation(
            requirements=(
                {
                    "kind": "FACT",
                    "node_key": "central_telecom_hub",
                    "fact_key": "operational",
                    "accepted_values": [True],
                },
            )
        )


def test_state_family_stays_in_state_pipeline() -> None:
    provider = _StateProvider()

    resolution = GenericGoalResolver(provider=provider).resolve(
        "让中央通信枢纽恢复运行", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].kind == "FACT"
    assert provider.operation_requests == []
    assert len(provider.interpretation_requests) == 1


def _definition_with_generic_action() -> ScenarioDefinitionV2:
    base = next(item for item in LINJIANG_V2_TEST.actions if item.key == "clear_transport")
    action = ActionDefinitionV2.model_validate(
        {
            **base.model_dump(mode="json"),
            "key": "calibrate_facility",
            "name": "校准设施",
            "description": "按声明的参数和来源区域校准一个设施。",
            "behavior": "RULE",
            "target_semantic_reference_type": "FACILITY",
            "target_node_type_keys": ["facility"],
            "operation_bindings": [
                ActionOperationBindingV2(
                    role="origin_region",
                    value_type=ActionSemanticReferenceType.REGION,
                    source=ActionOperationBindingSource.EXPLICIT,
                ).model_dump(mode="json")
            ],
            "parameters": [
                ActionParameterV2(
                    key="resource_key",
                    name="校准资源",
                    value_type=ActionParameterType.STRING,
                    semantic_reference_type=ActionSemanticReferenceType.RESOURCE,
                ).model_dump(mode="json"),
                ActionParameterV2(
                    key="cycles",
                    name="循环次数",
                    value_type=ActionParameterType.INTEGER,
                    minimum=1,
                    maximum=10,
                ).model_dump(mode="json"),
                ActionParameterV2(
                    key="mode",
                    name="模式",
                    value_type=ActionParameterType.ENUM,
                    allowed_values=("SAFE", "FAST"),
                ).model_dump(mode="json"),
                ActionParameterV2(
                    key="verify",
                    name="验证",
                    value_type=ActionParameterType.BOOLEAN,
                ).model_dump(mode="json"),
            ],
        }
    )
    return LINJIANG_V2_TEST.model_copy(update={"actions": (*LINJIANG_V2_TEST.actions, action)})


def test_non_transport_action_uses_same_generic_contract_pipeline() -> None:
    definition = _definition_with_generic_action()
    operation = _operation(
        "calibrate_facility",
        target=_slot(
            "target",
            "FACILITY",
            "GROUNDED",
            ref_type="NODE",
            key="central_telecom_hub",
        ),
        bindings=[
            _slot(
                "origin_region",
                "REGION",
                "GROUNDED",
                ref_type="REGION",
                key="central_district",
            )
        ],
        parameters=[
            _slot(
                "resource_key",
                "RESOURCE",
                "GROUNDED",
                ref_type="RESOURCE",
                key="emergency_fuel",
            ),
            _slot("cycles", "INTEGER", "GROUNDED", value=3),
            _slot("mode", "ENUM", "GROUNDED", value="SAFE"),
            _slot("verify", "BOOLEAN", "GROUNDED", value=True),
        ],
        references=[
            {"ref_type": "NODE", "key": "central_telecom_hub"},
            {"ref_type": "REGION", "key": "central_district"},
            {"ref_type": "RESOURCE", "key": "emergency_fuel"},
        ],
    )
    provider = _ContractProvider(action_key="calibrate_facility", operation_results=[operation])

    resolution = GenericGoalResolver(provider=provider).resolve("执行设施校准", definition)

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert requirement.target_key == "central_telecom_hub"
    assert [(item.role, item.value) for item in requirement.binding_constraints] == [
        ("origin_region", "central_district")
    ]
    assert requirement.parameter_constraints == {
        "cycles": 3,
        "mode": "SAFE",
        "resource_key": "emergency_fuel",
        "verify": True,
    }
