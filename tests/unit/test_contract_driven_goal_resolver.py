from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.agent.generic import (
    GenericGoalResolver,
    _dynamic_goal_action_contract,
    _dynamic_goal_routing_action_catalog,
)
from app.agent.provider import (
    DynamicGoalActionMatch,
    DynamicGoalActionMatchRequest,
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    DynamicGoalOperationGrounding,
    DynamicGoalOperationGroundingRequest,
    DynamicGoalSemanticRouting,
    DynamicGoalSemanticRoutingRequest,
    GenericProviderError,
    GoalFamilyMatch,
    GoalFamilyMatchRequest,
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
    bindings: Iterable[dict[str, object]] = (),
    parameters: Iterable[dict[str, object]] = (),
    references: Iterable[dict[str, str]] = (),
) -> dict[str, object]:
    return {
        "status": "RESOLVED",
        "intent": {
            "action_key": action_key,
            "actor": _slot("actor", "ACTOR", "NOT_SPECIFIED"),
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

    def match_dynamic_goal_family(self, request: GoalFamilyMatchRequest) -> GoalFamilyMatch:
        del request
        return GoalFamilyMatch(family=self.family)

    def match_dynamic_goal_action(
        self, request: DynamicGoalActionMatchRequest
    ) -> DynamicGoalActionMatch:
        assert request.frozen_family == "OPERATION"
        return DynamicGoalActionMatch(
            status=self.action_status,
            action_key=self.action_key if self.action_status == "GROUNDED" else None,
        )

    def ground_dynamic_goal_operation(
        self, request: DynamicGoalOperationGroundingRequest
    ) -> object:
        self.operation_requests.append(request)
        return self.operation_results.pop(0)


class _RoutingProvider(_ContractProvider):
    def __init__(
        self,
        *,
        routing: DynamicGoalSemanticRouting,
        operation_results: Iterable[object] = (),
    ) -> None:
        super().__init__(operation_results=operation_results)
        self.routing = routing
        self.routing_requests: list[DynamicGoalSemanticRoutingRequest] = []

    def route_dynamic_goal(
        self, request: DynamicGoalSemanticRoutingRequest
    ) -> DynamicGoalSemanticRouting:
        self.routing_requests.append(request)
        return self.routing


class _StateRoutingProvider(_RoutingProvider):
    def __init__(self, interpretation: DynamicGoalInterpretation) -> None:
        super().__init__(routing=DynamicGoalSemanticRouting(family="STATE"))
        self.interpretation = interpretation
        self.interpretation_requests: list[DynamicGoalInterpretationRequest] = []

    def ground_dynamic_goal_entities(
        self, _request: DynamicGoalEntityGroundingRequest
    ) -> DynamicGoalEntityGrounding:
        raise AssertionError("frozen STATE must skip generic Entity Grounding")

    def interpret_dynamic_goal(
        self, request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation:
        self.interpretation_requests.append(request)
        return self.interpretation


def _routing_request(goal: str) -> DynamicGoalSemanticRoutingRequest:
    provider = _RoutingProvider(
        routing=DynamicGoalSemanticRouting(
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


def test_routing_action_projection_filters_incompatible_target_capability() -> None:
    request = _routing_request("修复中央河底隧道")
    action_keys = {item["key"] for item in request.action_catalog}

    assert {"clear_transport", "inspect"} <= action_keys
    assert "repair_communications" not in action_keys
    assert "supply_power" not in action_keys


def test_routing_action_projection_filters_facility_incompatible_actions() -> None:
    request = _routing_request("让中央通信枢纽恢复运行")
    action_keys = {item["key"] for item in request.action_catalog}

    assert {"inspect", "repair_communications"} <= action_keys
    assert "clear_transport" not in action_keys
    assert "supply_power" not in action_keys
    assert "generate_power" not in action_keys


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

    assert "repair_communications" in contracts
    assert "clear_transport" not in contracts


def test_routing_fact_catalog_uses_authored_goal_alias() -> None:
    request = _routing_request("恢复中央通信能力")

    assert any(
        item["kind"] == "FACT"
        and item["node_key"] == "central_telecom_hub"
        and item["fact_key"] == "operational"
        for item in request.state_catalog
    )


def test_routing_derived_state_catalog_uses_goal_addressable_state() -> None:
    request = _routing_request("恢复北部基础工程支援")

    assert any(
        item["kind"] == "DERIVED_STATE"
        and item["key"] == "north_basic_engineering_support"
        for item in request.state_catalog
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


def test_routing_topology_reference_remains_non_exact_candidate_evidence() -> None:
    request = _routing_request("从南部滨水区运30个应急燃料到东南高地区")
    transport = next(
        item for item in request.action_catalog if item["key"] == "transport_resource"
    )

    assert {
        (item["ref_type"], item["key"], item["provenance"])
        for item in transport["candidate_refs"]
    } >= {
        ("NODE", "southeast_access_corridor", "TOPOLOGY_ENRICHED"),
    }


def test_routing_rejects_action_outside_projected_catalog() -> None:
    provider = _RoutingProvider(
        routing=DynamicGoalSemanticRouting(
            family="OPERATION",
            action_match="MATCHED",
            action_key="clear_transport",
        )
    )

    with pytest.raises(GenericProviderError, match="outside its candidate catalog"):
        GenericGoalResolver(provider=provider).resolve(
            "从南部滨水区运30个应急燃料到东南高地区",
            LINJIANG_V2_TEST,
        )


def _transport_operation(*, amount: object = 12) -> dict[str, object]:
    return _operation(
        "transport_resource",
        target=_slot("target", "REGION", "GROUNDED", ref_type="REGION", key="central_district"),
        bindings=[_slot("source_region", "REGION", "NOT_SPECIFIED")],
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
    assert requirement.binding_constraints == ()
    assert requirement.parameter_constraints == {
        "resources": [{"amount": 12, "resource_key": "emergency_fuel"}]
    }
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["frozen_family"] == "OPERATION"
    assert resolution.provider_observation["action_key"] == "transport_resource"
    assert resolution.provider_observation["recovery_used"] is False
    assert resolution.provider_observation["stage_2_skipped"] is True


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
    assert resolution.provider_observation["recovery_used"] is True


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


def test_topology_can_compose_one_transport_target_from_region_pair() -> None:
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


def test_grounded_target_different_from_unique_topology_keeps_identity_conflict() -> None:
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


def test_grounded_target_without_unique_topology_cannot_consume_regions() -> None:
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
def test_topology_consumption_does_not_hide_unrelated_exact_identity(
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

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "ACTION_UNRESOLVED"
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


def test_exact_canonical_resource_cannot_be_replaced_by_semantic_grounding() -> None:
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


def test_canonical_a1_preserves_only_exact_mentions_and_filters_operation_context() -> None:
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
        routing=DynamicGoalSemanticRouting(
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
    assert {(item["ref_type"], item["key"]) for item in request.public_references} == {
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
        routing=DynamicGoalSemanticRouting(
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
        routing=DynamicGoalSemanticRouting(family="OPERATION", action_match="NO_MATCH")
    )
    no_match_resolution = GenericGoalResolver(provider=no_match).resolve(
        "执行场景没有的操作", LINJIANG_V2_TEST
    )
    assert no_match_resolution.status == "UNSUPPORTED"
    assert no_match_resolution.source == "ACTION_NO_MATCH"


def test_semantic_routing_schema_failure_gets_one_structural_recovery() -> None:
    class _RecoveringRoutingProvider(_RoutingProvider):
        def route_dynamic_goal(
            self, request: DynamicGoalSemanticRoutingRequest
        ) -> DynamicGoalSemanticRouting:
            self.routing_requests.append(request)
            if len(self.routing_requests) == 1:
                raise GenericProviderError(
                    "PROVIDER_SCHEMA_INVALID",
                    "invalid routing shape",
                    validation_diagnostics=({"code": "missing", "location": "action_match"},),
                )
            return self.routing

    provider = _RecoveringRoutingProvider(
        routing=DynamicGoalSemanticRouting(
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
        def route_dynamic_goal(
            self, request: DynamicGoalSemanticRoutingRequest
        ) -> DynamicGoalSemanticRouting:
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
            return DynamicGoalSemanticRouting(family="OPERATION", action_match="NO_MATCH")

    provider = _RegressingRoutingProvider(
        routing=DynamicGoalSemanticRouting(family="OPERATION", action_match="NO_MATCH")
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


def test_routed_transport_without_source_keeps_source_unspecified() -> None:
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
        routing=DynamicGoalSemanticRouting(
            family="OPERATION",
            action_match="MATCHED",
            action_key="transport_resource",
        ),
        operation_results=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个应急燃料运到南部滨水区", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert requirement.binding_constraints == ()
    assert requirement.target_key == "south_waterfront_district"


def test_routed_region_pair_uses_only_local_topology_for_unique_node() -> None:
    provider = _RoutingProvider(
        routing=DynamicGoalSemanticRouting(
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

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].target_key == "north_service_corridor"
    topology = provider.operation_requests[0].public_topology
    assert len(topology["transport_endpoint_pairs"]) == 1
    assert len(topology["relations"]) == 2
    assert topology["transport_endpoint_pairs"][0]["entity_key"] == "north_service_corridor"


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
    expected_action = "transport_resource" if "运" in goal else "clear_transport"
    action = next(item for item in request.action_catalog if item["key"] == expected_action)
    actual = {(item["ref_type"], item["key"]) for item in action["candidate_refs"]}

    assert expected_refs <= actual
    assert ("NODE", topology_key) in actual


@pytest.mark.parametrize(
    ("goal", "resource_key"),
    [
        ("从东南高地区运30个通用部件到南部滨水区", "general_engineering_parts"),
        ("从东南高地区运30个电力部件到南部滨水区", "electrical_repair_parts"),
    ],
)
def test_specific_resource_reference_is_deterministic(
    goal: str, resource_key: str
) -> None:
    request = _routing_request(goal)
    transport = next(item for item in request.action_catalog if item["key"] == "transport_resource")

    assert ("RESOURCE", resource_key, "EXACT_USER_MENTION") in {
        (item["ref_type"], item["key"], item["provenance"])
        for item in transport["candidate_refs"]
    }


def test_ambiguous_public_resource_reference_clarifies_without_provider_call() -> None:
    provider = _RoutingProvider(
        routing=DynamicGoalSemanticRouting(family="OPERATION", action_match="NO_MATCH")
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "运30个部件到南部", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "PUBLIC_REFERENCE_AMBIGUOUS"
    assert set(resolution.candidate_keys) == {
        "general_engineering_parts",
        "electrical_repair_parts",
    }
    assert provider.routing_requests == []
    assert provider.operation_requests == []


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
        routing=DynamicGoalSemanticRouting(
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
    assert not any(
        item.ref_type == "RESOURCE" for item in request.deterministic_candidate_refs
    )
    resource = next(
        item
        for item in request.public_references
        if item["ref_type"] == "RESOURCE"
        and item["key"] == "electrical_repair_parts"
    )
    assert set(resource["public_references"]) == {"电力部件", "部件"}
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
