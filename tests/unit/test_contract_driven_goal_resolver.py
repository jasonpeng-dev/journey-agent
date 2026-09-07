from __future__ import annotations

from collections.abc import Iterable

from app.agent.generic import GenericGoalResolver
from app.agent.provider import (
    DynamicGoalActionMatch,
    DynamicGoalActionMatchRequest,
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    DynamicGoalOperationGroundingRequest,
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


def _transport_operation(*, amount: object = 12) -> dict[str, object]:
    return _operation(
        "transport_resource",
        target=_slot(
            "target", "REGION", "GROUNDED", ref_type="REGION", key="central_district"
        ),
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
    provider = _ContractProvider(
        operation_results=[malformed, _transport_operation(amount=12)]
    )

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
    provider = _ContractProvider(
        action_key="clear_transport", operation_results=[operation]
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "打通中央城区与北部工业区之间的道路", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].action_key == "clear_transport"
    assert resolution.dynamic_requirements[0].target_key == "north_service_corridor"


def test_unresolved_action_returns_typed_clarification_without_grounding() -> None:
    provider = _ContractProvider(action_status="UNRESOLVED")

    resolution = GenericGoalResolver(provider=provider).resolve(
        "做一下那个任务", LINJIANG_V2_TEST
    )

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
                DynamicGoalCandidateReference(
                    ref_type="NODE", key="central_telecom_hub"
                ),
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
    return LINJIANG_V2_TEST.model_copy(
        update={"actions": (*LINJIANG_V2_TEST.actions, action)}
    )


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
    provider = _ContractProvider(
        action_key="calibrate_facility", operation_results=[operation]
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "执行设施校准", definition
    )

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
