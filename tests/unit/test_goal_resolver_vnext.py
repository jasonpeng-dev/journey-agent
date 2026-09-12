from __future__ import annotations

from collections.abc import Iterable

from app.agent.generic import GenericGoalResolver
from app.agent.provider import (
    DynamicGoalActionMatchRequest,
    DynamicGoalActionRouting,
    DynamicGoalActionRoutingRequest,
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalFamilyRouting,
    DynamicGoalFamilyRoutingRequest,
    DynamicGoalIntentDraft,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    DynamicGoalMentionSlot,
    DynamicGoalOperationGrounding,
    DynamicGoalOperationGroundingRequest,
    DynamicGoalScalarMentionSlot,
    GoalFamilyMatchRequest,
)
from tests.scenario_fixtures import LINJIANG_V2_TEST
from tests.unit.test_contract_driven_goal_resolver import _operation, _slot


class _VNextProvider:
    def __init__(
        self,
        *,
        grounding: Iterable[object],
        family: str,
        family_prompt: str | None = None,
        family_results: Iterable[object] = (),
        action_key: str | None = None,
        action_results: Iterable[object] = (),
        operation: Iterable[object] = (),
        interpretation: Iterable[object] = (),
    ) -> None:
        self.grounding_results = list(grounding)
        self.family = family
        self.family_prompt = family_prompt
        self.family_results = list(family_results)
        self.action_key = action_key
        self.action_results = list(action_results)
        self.operation_results = list(operation)
        self.interpretation_results = list(interpretation)
        self.grounding_requests: list[DynamicGoalEntityGroundingRequest] = []
        self.family_requests: list[DynamicGoalFamilyRoutingRequest] = []
        self.action_requests: list[DynamicGoalActionRoutingRequest] = []
        self.operation_requests: list[DynamicGoalOperationGroundingRequest] = []
        self.interpretation_requests: list[DynamicGoalInterpretationRequest] = []

    def ground_dynamic_goal_entities(self, request: DynamicGoalEntityGroundingRequest) -> object:
        self.grounding_requests.append(request)
        return self.grounding_results.pop(0)

    def decide_dynamic_goal_family(self, request: DynamicGoalFamilyRoutingRequest) -> object:
        self.family_requests.append(request)
        if self.family_results:
            return self.family_results.pop(0)
        return DynamicGoalFamilyRouting(
            family=self.family,
            clarification_prompt=self.family_prompt,
        )

    def route_dynamic_goal_action(self, request: DynamicGoalActionRoutingRequest) -> object:
        self.action_requests.append(request)
        if self.action_results:
            return self.action_results.pop(0)
        if self.action_key is None:
            return DynamicGoalActionRouting(
                action_match="NO_MATCH",
                no_match_reason="NO_SEMANTIC_ACTION",
            )
        return DynamicGoalActionRouting(action_match="MATCHED", action_key=self.action_key)

    def ground_dynamic_goal_operation(
        self, request: DynamicGoalOperationGroundingRequest
    ) -> object:
        self.operation_requests.append(request)
        return self.operation_results.pop(0)

    def interpret_dynamic_goal(self, request: DynamicGoalInterpretationRequest) -> object:
        self.interpretation_requests.append(request)
        return self.interpretation_results.pop(0)

    def route_dynamic_goal(self, _request: object) -> object:
        raise AssertionError("VNext production must not use mixed semantic routing")

    def match_dynamic_goal_family(self, _request: GoalFamilyMatchRequest) -> object:
        raise AssertionError("VNext production must not use legacy family matching")

    def match_dynamic_goal_action(self, _request: DynamicGoalActionMatchRequest) -> object:
        raise AssertionError("VNext production must not use legacy Action matching")


def _role_grounding(
    refs: Iterable[DynamicGoalCandidateReference],
    *,
    actor: DynamicGoalMentionSlot | None = None,
    source: DynamicGoalMentionSlot | None = None,
    target: DynamicGoalMentionSlot | None = None,
    resource: DynamicGoalMentionSlot | None = None,
    amount: DynamicGoalScalarMentionSlot | None = None,
) -> DynamicGoalEntityGrounding:
    unspecified = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    return DynamicGoalEntityGrounding(
        candidate_refs=tuple(refs),
        intent=DynamicGoalIntentDraft(
            # These compatibility guesses are deliberately wrong: VNext ignores them.
            intent_kind="STATE",
            action=DynamicGoalMentionSlot(
                status="GROUNDED", ref_type="ACTION", key="repair_electrical"
            ),
            actor=actor or unspecified,
            source=source or unspecified,
            target=target or unspecified,
            resource=resource or unspecified,
            amount=amount or DynamicGoalScalarMentionSlot(status="NOT_SPECIFIED"),
        ),
    )


def test_exact_derived_state_survives_semantic_grounding_and_uses_vnext_state_path() -> None:
    provider = _VNextProvider(
        grounding=[
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(
                        ref_type="DERIVED_STATE", key="north_basic_engineering_support"
                    ),
                )
            )
        ],
        family="STATE",
        interpretation=[
            DynamicGoalInterpretation(
                requirements=(
                    {
                        "kind": "DERIVED_STATE",
                        "derived_key": "east_emergency_water_supply",
                        "accepted_values": ["AVAILABLE"],
                    },
                )
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Restore east emergency water supply", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    exact = provider.grounding_requests[0].deterministic_candidate_refs
    assert {(item.ref_type, item.key) for item in exact} == {
        ("DERIVED_STATE", "east_emergency_water_supply")
    }
    assert provider.family_requests[0].deterministic_candidate_refs == exact
    assert resolution.dynamic_requirements[0].derived_key == "east_emergency_water_supply"
    assert len(provider.grounding_requests) == 1
    assert len(provider.interpretation_requests) == 1
    assert provider.action_requests == []


def test_semantic_action_and_family_guesses_have_no_authority_and_actor_stays_unspecified() -> None:
    target = DynamicGoalMentionSlot(status="GROUNDED", ref_type="NODE", key="central_hospital")
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),),
                target=target,
            )
        ],
        family="OPERATION",
        action_key="inspect",
        operation=[
            _operation(
                "inspect",
                target=_slot("target", "NODE", "GROUNDED", ref_type="NODE", key="central_hospital"),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve("检查中央医院", LINJIANG_V2_TEST)

    requirement = resolution.dynamic_requirements[0]
    assert resolution.status == "RESOLVED"
    assert requirement.action_key == "inspect"
    assert requirement.target_key == "central_hospital"
    assert requirement.actor_key is None
    evidence = provider.operation_requests[0].explicit_role_evidence
    assert evidence["actor"]["status"] == "NOT_SPECIFIED"
    assert [item["stage"] for item in resolution.provider_observation["stages"]] == [
        "DETERMINISTIC_GROUNDING",
        "SEMANTIC_GROUNDING",
        "EVIDENCE_FREEZE",
        "FAMILY_ROUTING",
        "ACTION_ROUTING",
        "OPERATION_GROUNDING",
        "CONTRACT_VALIDATION",
        "FORMAL_GOAL",
    ]


def test_repair_facility_goal_freezes_target_and_leaves_actor_unspecified() -> None:
    target = DynamicGoalMentionSlot(
        status="GROUNDED",
        ref_type="NODE",
        key="central_telecom_hub",
    )
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(
                        ref_type="NODE",
                        key="central_telecom_hub",
                    ),
                ),
                target=target,
            )
        ],
        family="OPERATION",
        action_key="repair_facility",
        operation=[
            _operation(
                "repair_facility",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_telecom_hub",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "修复中央通信枢纽",
        LINJIANG_V2_TEST,
    )

    requirement = resolution.dynamic_requirements[0]
    assert resolution.status == "RESOLVED"
    assert requirement.action_key == "repair_facility"
    assert requirement.target_key == "central_telecom_hub"
    assert requirement.actor_key is None
    assert provider.operation_requests[0].explicit_role_evidence["actor"]["status"] == (
        "NOT_SPECIFIED"
    )


def test_genuine_family_ambiguity_returns_focused_clarification_without_action_routing() -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="AMBIGUOUS",
        family_prompt="Do you require the state to hold, or the operation to occur?",
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Make the system restored", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "FAMILY_AMBIGUOUS"
    assert resolution.clarification_prompt == provider.family_prompt
    assert provider.action_requests == []


def test_exact_aliases_remain_authoritative_when_semantic_grounding_disagrees() -> None:
    aliases = {
        "南部": ("REGION", "south_waterfront_district"),
        "东南区": ("REGION", "southeast_heights_district"),
        "电力维修零件": ("RESOURCE", "electrical_repair_parts"),
        "水务维修材料": ("RESOURCE", "water_system_parts"),
    }
    for surface, expected in aliases.items():
        provider = _VNextProvider(
            grounding=[
                DynamicGoalEntityGrounding(
                    candidate_refs=(
                        DynamicGoalCandidateReference(ref_type="REGION", key="central_district"),
                    )
                )
            ],
            family="OPERATION",
        )

        GenericGoalResolver(provider=provider).resolve(surface, LINJIANG_V2_TEST)

        exact = {
            (item.ref_type, item.key)
            for item in provider.family_requests[0].deterministic_candidate_refs
        }
        assert expected in exact


def test_travel_preserves_explicit_target_and_leaves_actor_unspecified() -> None:
    target = DynamicGoalMentionSlot(
        status="GROUNDED", ref_type="REGION", key="east_residential_district"
    )
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="east_residential_district"
                    ),
                ),
                target=target,
            )
        ],
        family="OPERATION",
        action_key="travel",
        operation=[
            _operation(
                "travel",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="REGION",
                    key="east_residential_district",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve("前往东部居住区", LINJIANG_V2_TEST)

    requirement = resolution.dynamic_requirements[0]
    assert resolution.status == "RESOLVED"
    assert requirement.target_key == "east_residential_district"
    assert requirement.actor_key is None


def test_resource_state_uses_frozen_region_resource_and_amount() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="REGION", key="central_district"),
                    DynamicGoalCandidateReference(ref_type="RESOURCE", key="water_system_parts"),
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED", ref_type="REGION", key="central_district"
                ),
                resource=DynamicGoalMentionSlot(
                    status="GROUNDED", ref_type="RESOURCE", key="water_system_parts"
                ),
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=1),
            )
        ],
        family="STATE",
        interpretation=[
            DynamicGoalInterpretation(
                requirements=(
                    {
                        "kind": "RESOURCE_AT_LEAST",
                        "region_key": "central_district",
                        "resource_key": "water_system_parts",
                        "minimum": 1,
                    },
                )
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "保证中央城区至少有1个水务维修部件", LINJIANG_V2_TEST
    )

    requirement = resolution.dynamic_requirements[0]
    assert resolution.status == "RESOLVED"
    assert requirement.region_key == "central_district"
    assert requirement.resource_key == "water_system_parts"
    assert requirement.minimum == 1


def test_bare_resource_ambiguity_preserves_known_amount_and_target() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="south_waterfront_district"
                    ),
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED", ref_type="REGION", key="south_waterfront_district"
                ),
                resource=DynamicGoalMentionSlot(
                    status="UNRESOLVED", ref_type="RESOURCE", surface="部件"
                ),
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30),
            )
        ],
        family="OPERATION",
        action_key="transport_resource",
        operation=[
            DynamicGoalOperationGrounding(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="你指的是哪一种公开部件?",
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "运30个部件到南部", LINJIANG_V2_TEST
    )

    roles = provider.operation_requests[0].explicit_role_evidence
    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.clarification_prompt == "你指的是哪一种公开部件?"
    assert roles["amount"]["value"] == 30
    assert roles["target"]["key"] == "south_waterfront_district"
    assert roles["resource"]["status"] == "UNRESOLVED"


def test_unknown_resource_is_not_nearest_canonical_rescued() -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="OPERATION",
        action_key="transport_resource",
        operation=[
            DynamicGoalOperationGrounding(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="Which public resource?",
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个医疗物资运到中央城区", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    request = provider.operation_requests[0]
    assert all(item.key != "emergency_relief_supplies" for item in request.semantic_candidate_refs)
    assert {(item.ref_type, item.key) for item in request.deterministic_candidate_refs} == {
        ("REGION", "central_district")
    }


def test_explicit_actor_static_conflict_is_rejected_but_unspecified_actor_is_not() -> None:
    actor_key = "communications_repair_team_alpha"
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="ACTOR", key=actor_key),
                    DynamicGoalCandidateReference(ref_type="NODE", key="south_bridge"),
                ),
                actor=DynamicGoalMentionSlot(status="GROUNDED", ref_type="ACTOR", key=actor_key),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED", ref_type="NODE", key="south_bridge"
                ),
            )
        ],
        family="OPERATION",
        action_key="clear_transport",
        operation=[
            _operation(
                "clear_transport",
                target=_slot("target", "NODE", "GROUNDED", ref_type="NODE", key="south_bridge"),
            )
        ],
    )
    provider.operation_results[0]["intent"]["actor"] = _slot(
        "actor", "ACTOR", "GROUNDED", ref_type="ACTOR", key=actor_key
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "让通信抢修一队清理南港大桥", LINJIANG_V2_TEST
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "EXPLICIT_ACTOR_ACTION_CONFLICT"


def test_generic_relation_contract_rejects_explicit_source_target_conflict() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),
                    DynamicGoalCandidateReference(ref_type="NODE", key="east_community_hospital"),
                ),
                source=DynamicGoalMentionSlot(
                    status="GROUNDED", ref_type="NODE", key="central_hospital"
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED", ref_type="NODE", key="east_community_hospital"
                ),
            )
        ],
        family="OPERATION",
        action_key="supply_power",
        operation=[
            _operation(
                "supply_power",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="east_community_hospital",
                ),
                parameters=[
                    _slot(
                        "source_key",
                        "NODE",
                        "GROUNDED",
                        ref_type="NODE",
                        key="central_hospital",
                    )
                ],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从中央医院向东区社区医院供电", LINJIANG_V2_TEST
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "EXPLICIT_RELATION_CONFLICT"


def test_nonlocal_transport_is_frozen_without_route_legality_validation() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="north_industrial_district"
                    ),
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="east_residential_district"
                    ),
                    DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
                )
            )
        ],
        family="OPERATION",
        action_key="transport_resource",
        operation=[
            _operation(
                "transport_resource",
                target=_slot(
                    "target",
                    "REGION",
                    "GROUNDED",
                    ref_type="REGION",
                    key="east_residential_district",
                ),
                bindings=[
                    _slot(
                        "source_region",
                        "REGION",
                        "GROUNDED",
                        ref_type="REGION",
                        key="north_industrial_district",
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
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从北部运30个应急燃料到东部", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert requirement.target_key == "east_residential_district"
    assert requirement.binding_constraints[0].value == "north_industrial_district"


def test_semantic_and_state_recovery_retry_only_the_failing_stage_once() -> None:
    provider = _VNextProvider(
        grounding=[
            {
                "status": "RESOLVED",
                "candidate_refs": [{"ref_type": "NODE", "key": "not_public"}],
            },
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="NODE", key="central_telecom_hub"),
                )
            ),
        ],
        family="STATE",
        interpretation=[
            DynamicGoalInterpretation(
                requirements=(
                    {
                        "kind": "FACT",
                        "node_key": "central_telecom_hub",
                        "fact_key": "operational",
                        "accepted_values": ["not-a-boolean"],
                    },
                )
            ),
            DynamicGoalInterpretation(
                requirements=(
                    {
                        "kind": "FACT",
                        "node_key": "central_telecom_hub",
                        "fact_key": "operational",
                        "accepted_values": [True],
                    },
                )
            ),
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "让中央通信枢纽恢复运行", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert [item.recovery_attempt for item in provider.grounding_requests] == [0, 1]
    assert [item.recovery_attempt for item in provider.interpretation_requests] == [0, 1]
    assert len(provider.family_requests) == 1
    assert provider.action_requests == []


def test_family_recovery_does_not_repeat_grounding_or_downstream_stages() -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="OPERATION",
        family_results=[{"family": "INVALID"}, {"family": "OPERATION"}],
        action_key="inspect",
        operation=[_operation("inspect", target=_slot("target", "NODE", "NOT_SPECIFIED"))],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "inspect a public target", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert len(provider.family_requests) == 2
    assert len(provider.action_requests) == 1
    assert len(provider.operation_requests) == 1


def test_action_recovery_keeps_family_frozen_and_retries_only_action() -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="OPERATION",
        action_results=[
            {"action_match": "MATCHED", "action_key": None},
            {"action_match": "MATCHED", "action_key": "inspect"},
        ],
        operation=[_operation("inspect", target=_slot("target", "NODE", "NOT_SPECIFIED"))],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "inspect a public target", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert len(provider.family_requests) == 1
    assert len(provider.action_requests) == 2
    assert len(provider.operation_requests) == 1


def test_operation_recovery_keeps_action_frozen_and_retries_only_operation() -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="OPERATION",
        action_key="inspect",
        operation=[
            {"status": "RESOLVED", "intent": {"action_key": "repair_electrical"}},
            _operation("inspect", target=_slot("target", "NODE", "NOT_SPECIFIED")),
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "inspect a public target", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert len(provider.family_requests) == 1
    assert len(provider.action_requests) == 1
    assert len(provider.operation_requests) == 2
    assert {request.action_key for request in provider.operation_requests} == {"inspect"}
