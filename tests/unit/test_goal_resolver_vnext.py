from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.agent.generic import GenericGoalResolver
from app.agent.provider import (
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
    GenericProviderError,
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


def _travel_grounding(
    *,
    action: str = "travel",
    actor: DynamicGoalMentionSlot | None = None,
) -> DynamicGoalEntityGrounding:
    unspecified = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    return DynamicGoalEntityGrounding(
        candidate_refs=(
            DynamicGoalCandidateReference(ref_type="ACTION", key=action),
            DynamicGoalCandidateReference(ref_type="REGION", key="east_residential_district"),
        ),
        intent=DynamicGoalIntentDraft(
            intent_kind="OPERATION",
            action=DynamicGoalMentionSlot(
                status="GROUNDED",
                ref_type="ACTION",
                key=action,
                surface="前往",
                match_semantics="EXACT_OR_AUTHORED",
            ),
            actor=actor or unspecified,
            target=DynamicGoalMentionSlot(
                status="GROUNDED",
                ref_type="REGION",
                key="east_residential_district",
                surface="东部居住区",
                match_semantics="EXACT_OR_AUTHORED",
            ),
        ),
    )


def _transport_role_grounding(
    *,
    target_key: str = "central_district",
    target_surface: str = "中央城区",
    resource_status: str = "GROUNDED",
    resource_key: str = "emergency_relief_supplies",
    resource_match_semantics: str | None = "SEMANTIC_EQUIVALENT",
    resource_surface: str = "救灾用品",
    source_status: str = "NOT_SPECIFIED",
    source_key: str | None = None,
    source_surface: str | None = None,
) -> DynamicGoalEntityGrounding:
    resource = (
        DynamicGoalMentionSlot(status="NOT_SPECIFIED")
        if resource_status == "NOT_SPECIFIED"
        else DynamicGoalMentionSlot(
            status=resource_status,  # type: ignore[arg-type]
            ref_type="RESOURCE",
            key=resource_key if resource_status == "GROUNDED" else None,
            surface=resource_surface,
            match_semantics=resource_match_semantics,  # type: ignore[arg-type]
        )
    )
    source = (
        DynamicGoalMentionSlot(status="NOT_SPECIFIED")
        if source_status == "NOT_SPECIFIED"
        else DynamicGoalMentionSlot(
            status=source_status,  # type: ignore[arg-type]
            ref_type="REGION",
            key=source_key if source_status == "GROUNDED" else None,
            surface=source_surface,
            match_semantics=("SEMANTIC_EQUIVALENT" if source_status == "GROUNDED" else None),
        )
    )
    refs = [
        DynamicGoalCandidateReference(ref_type="REGION", key=target_key),
    ]
    if resource_status == "GROUNDED":
        refs.append(DynamicGoalCandidateReference(ref_type="RESOURCE", key=resource_key))
    if source_status == "GROUNDED" and source_key is not None:
        refs.append(DynamicGoalCandidateReference(ref_type="REGION", key=source_key))
    return _role_grounding(
        refs,
        target=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="REGION",
            key=target_key,
            surface=target_surface,
            match_semantics="EXACT_OR_AUTHORED",
        ),
        source=source,
        resource=resource,
        amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30, surface="30"),
    )


def test_exact_derived_state_survives_semantic_grounding_and_uses_vnext_state_path() -> None:
    provider = _VNextProvider(
        grounding=[
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(
                        ref_type="DERIVED_STATE", key="east_emergency_water_supply"
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
    assert provider.action_requests[0].semantic_action_evidence is None
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


def test_repair_facility_semantics_discard_contaminating_region_hint() -> None:
    target = DynamicGoalMentionSlot(
        status="GROUNDED",
        ref_type="NODE",
        key="central_telecom_hub",
        surface="中央通信塔",
    )
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (DynamicGoalCandidateReference(ref_type="NODE", key="central_telecom_hub"),),
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

    resolution = GenericGoalResolver(provider=provider).resolve("修复中央通信塔", LINJIANG_V2_TEST)

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].target_key == "central_telecom_hub"
    assert {
        (item.ref_type, item.key)
        for item in provider.grounding_requests[0].deterministic_candidate_refs
    } == {("REGION", "central_district")}
    assert {
        (item.ref_type, item.key) for item in provider.operation_requests[0].semantic_candidate_refs
    } == {("NODE", "central_telecom_hub")}
    frozen = resolution.provider_observation["stages"][2]["merged_refs"]
    assert {(item["ref_type"], item["key"]) for item in frozen} == {("NODE", "central_telecom_hub")}


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


def test_exact_aliases_are_forwarded_as_advisory_grounding_hints() -> None:
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


def test_travel_requires_an_explicit_actor() -> None:
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

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["diagnostics"]["missing_slot_keys"] == ["actor"]


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
                    status="GROUNDED",
                    ref_type="REGION",
                    key="south_waterfront_district",
                    surface="南部",
                ),
                resource=DynamicGoalMentionSlot(
                    status="UNRESOLVED", ref_type="RESOURCE", surface="部件"
                ),
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30),
            ),
            _role_grounding(
                (
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="south_waterfront_district"
                    ),
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="REGION",
                    key="south_waterfront_district",
                    surface="南部",
                ),
                resource=DynamicGoalMentionSlot(
                    status="UNRESOLVED", ref_type="RESOURCE", surface="部件"
                ),
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30),
            ),
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
                actor=DynamicGoalMentionSlot(
                    status="GROUNDED", ref_type="ACTOR", key=actor_key, surface="通信抢修一队"
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key="south_bridge",
                    surface="南港大桥",
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
    assert resolution.source == "SOURCE_TARGET_RELATION_CONFLICT"
    assert resolution.dynamic_requirements == ()


def test_supply_power_omitted_source_is_a_legal_unconstrained_operation() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                    surface="hospital",
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
                    key="central_hospital",
                ),
                parameters=[_slot("source_key", "NODE", "NOT_SPECIFIED")],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "supply power to the hospital", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert requirement.action_key == "supply_power"
    assert requirement.target_key == "central_hospital"
    assert requirement.parameter_constraints is None
    assert provider.operation_requests[0].explicit_role_evidence["source"]["status"] == (
        "NOT_SPECIFIED"
    )


def test_supply_power_explicit_valid_source_is_frozen_and_relation_passes() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="NODE", key="east_distribution_station"),
                    DynamicGoalCandidateReference(ref_type="NODE", key="east_community_hospital"),
                ),
                source=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key="east_distribution_station",
                    surface="distribution station",
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key="east_community_hospital",
                    surface="community hospital",
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
                        key="east_distribution_station",
                    )
                ],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "supply power from the distribution station to the community hospital",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].parameter_constraints == {
        "source_key": "east_distribution_station"
    }


def test_supply_power_explicit_wrong_source_is_a_relation_conflict() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),
                    DynamicGoalCandidateReference(ref_type="NODE", key="east_community_hospital"),
                ),
                source=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                    surface="central hospital",
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key="east_community_hospital",
                    surface="community hospital",
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
        "supply power from central hospital to the community hospital",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "SOURCE_TARGET_RELATION_CONFLICT"
    assert provider.operation_requests[0].action_key == "supply_power"


@pytest.mark.parametrize(
    ("action_key", "operation", "expected_missing"),
    [
        (
            "generate_power",
            _operation("generate_power", target=_slot("target", "NODE", "NOT_SPECIFIED")),
            ("target",),
        ),
        (
            "travel",
            _operation(
                "travel",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            ),
            ("actor",),
        ),
        (
            "transport_resource",
            _operation(
                "transport_resource",
                target=_slot(
                    "target",
                    "REGION",
                    "GROUNDED",
                    ref_type="REGION",
                    key="central_district",
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
            ),
            ("source_region",),
        ),
        (
            "transport_resource",
            _operation(
                "transport_resource",
                target=_slot(
                    "target",
                    "REGION",
                    "GROUNDED",
                    ref_type="REGION",
                    key="central_district",
                ),
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
                    _slot("amount", "INTEGER", "NOT_SPECIFIED"),
                    _slot(
                        "resource_key",
                        "RESOURCE",
                        "GROUNDED",
                        ref_type="RESOURCE",
                        key="emergency_fuel",
                    ),
                ],
            ),
            ("amount",),
        ),
        (
            "transport_resource",
            _operation(
                "transport_resource",
                target=_slot(
                    "target",
                    "REGION",
                    "GROUNDED",
                    ref_type="REGION",
                    key="central_district",
                ),
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
                    _slot("amount", "INTEGER", "GROUNDED", value=30),
                    _slot("resource_key", "RESOURCE", "NOT_SPECIFIED"),
                ],
            ),
            ("resource_key",),
        ),
        (
            "transport_resource",
            _operation(
                "transport_resource",
                target=_slot("target", "REGION", "NOT_SPECIFIED"),
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
                    _slot("amount", "INTEGER", "GROUNDED", value=30),
                    _slot(
                        "resource_key",
                        "RESOURCE",
                        "GROUNDED",
                        ref_type="RESOURCE",
                        key="emergency_fuel",
                    ),
                ],
            ),
            ("target",),
        ),
    ],
)
def test_goal_required_missing_slot_returns_clarification_without_formal_goal(
    action_key: str,
    operation: dict[str, object],
    expected_missing: tuple[str, ...],
) -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="OPERATION",
        action_key=action_key,
        operation=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        f"exercise {action_key}", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()
    assert resolution.provider_observation["diagnostics"]["missing_slot_keys"] == list(
        expected_missing
    )


def test_action_surface_fragment_does_not_ground_required_target() -> None:
    """Action wording must not become a target constraint by substring reuse."""

    provider = _VNextProvider(
        grounding=[
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="ACTION", key="generate_power"),
                ),
                intent=DynamicGoalIntentDraft(
                    intent_kind="OPERATION",
                    action=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="ACTION",
                        key="generate_power",
                        surface="启动燃料应急发电",
                        match_semantics="EXACT_OR_AUTHORED",
                    ),
                    target=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="NODE",
                        key="southeast_fuel_emergency_power_plant",
                        surface="燃料应急发电",
                        match_semantics="SEMANTIC_EQUIVALENT",
                    ),
                    resource=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="RESOURCE",
                        key="emergency_fuel",
                        surface="燃料",
                        match_semantics="SEMANTIC_EQUIVALENT",
                    ),
                ),
            )
        ],
        family="OPERATION",
        action_key="generate_power",
        operation=[
            _operation(
                "generate_power",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="southeast_fuel_emergency_power_plant",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "启动燃料应急发电", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()
    assert resolution.provider_observation["diagnostics"]["missing_slot_keys"] == ["target"]


def test_bare_unresolved_required_role_normalizes_to_missing_slot() -> None:
    provider = _VNextProvider(
        grounding=[
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="ACTION", key="generate_power"),
                ),
                intent=DynamicGoalIntentDraft(
                    intent_kind="OPERATION",
                    action=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="ACTION",
                        key="generate_power",
                    ),
                    target=DynamicGoalMentionSlot(status="UNRESOLVED"),
                ),
            )
        ],
        family="OPERATION",
        action_key="generate_power",
        operation=[
            _operation(
                "generate_power",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="southeast_fuel_emergency_power_plant",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "启动燃料应急发电", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()


def test_omitted_target_is_not_promoted_from_explicit_source_identity() -> None:
    """An explicit source cannot silently fill an omitted required target."""

    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="REGION", key="west_logistics_district"),
                    DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
                ),
                source=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="REGION",
                    key="west_logistics_district",
                    surface="west depot",
                    match_semantics="SEMANTIC_EQUIVALENT",
                ),
                target=DynamicGoalMentionSlot(status="NOT_SPECIFIED"),
                resource=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="RESOURCE",
                    key="emergency_fuel",
                    surface="emergency fuel",
                    match_semantics="SEMANTIC_EQUIVALENT",
                ),
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30, surface="30"),
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
                    key="west_logistics_district",
                ),
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
        "transport 30 emergency fuel from west depot", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()
    assert resolution.provider_observation["diagnostics"]["missing_slot_keys"] == ["target"]


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
                ),
                source=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="REGION",
                    key="north_industrial_district",
                ),
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
        operation=[
            _operation(
                "inspect",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            )
        ],
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
        operation=[
            _operation(
                "inspect",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            )
        ],
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
            _operation(
                "inspect",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            ),
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


def test_family_state_drift_recovers_to_operation_without_terminal_equivalent() -> None:
    provider = _VNextProvider(
        grounding=[
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),
                ),
                intent=DynamicGoalIntentDraft(
                    intent_kind="OPERATION",
                    action=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="ACTION",
                        key="travel",
                    ),
                    actor=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="ACTOR",
                        key="electrical_repair_team_alpha",
                        surface="actor",
                        match_semantics="SEMANTIC_EQUIVALENT",
                    ),
                    target=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="NODE",
                        key="central_hospital",
                        surface="central hospital",
                    ),
                ),
            )
        ],
        family="OPERATION",
        family_results=[{"family": "STATE"}, {"family": "OPERATION"}],
        action_key="travel",
        operation=[
            _operation(
                "travel",
                actor=_slot(
                    "actor",
                    "ACTOR",
                    "GROUNDED",
                    ref_type="ACTOR",
                    key="electrical_repair_team_alpha",
                ),
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "actor travel to central hospital", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.family_requests) == 2
    assert provider.family_requests[0].semantic_family_evidence.operation_expressed is True
    assert provider.family_requests[0].semantic_family_evidence.state_equivalent_available is False
    assert provider.family_requests[1].recovery_feedback[0]["code"] == (
        "FAMILY_OPERATION_STATE_EQUIVALENCE_CONFLICT"
    )


def test_family_state_drift_without_recovery_is_typed_provider_failure() -> None:
    provider = _VNextProvider(
        grounding=[
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),
                ),
                intent=DynamicGoalIntentDraft(
                    intent_kind="OPERATION",
                    action=DynamicGoalMentionSlot(
                        status="GROUNDED", ref_type="ACTION", key="travel"
                    ),
                    target=DynamicGoalMentionSlot(
                        status="GROUNDED",
                        ref_type="NODE",
                        key="central_hospital",
                        surface="central hospital",
                    ),
                ),
            )
        ],
        family="STATE",
        family_results=[{"family": "STATE"}, {"family": "STATE"}],
    )

    with pytest.raises(Exception) as caught:
        GenericGoalResolver(provider=provider).resolve(
            "travel to central hospital", LINJIANG_V2_TEST
        )
    assert getattr(caught.value, "code", None) == "PROVIDER_SCHEMA_INVALID"


def test_invalid_unresolved_role_uses_one_bounded_recovery_then_omitted_role() -> None:
    target = DynamicGoalMentionSlot(
        status="GROUNDED",
        ref_type="NODE",
        key="central_hospital",
        surface="central hospital",
    )
    invalid = _role_grounding(
        (DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),),
        target=target,
        source=DynamicGoalMentionSlot(
            status="UNRESOLVED",
            provenance="SEMANTIC_ROLE_EVIDENCE",
        ),
    )
    recovered = _role_grounding(
        (DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),),
        target=target,
        source=DynamicGoalMentionSlot(status="NOT_SPECIFIED"),
    )
    provider = _VNextProvider(
        grounding=[invalid, recovered],
        family="OPERATION",
        action_key="inspect",
        operation=[
            _operation(
                "inspect",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "inspect central hospital", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 2
    assert provider.grounding_requests[1].recovery_attempt == 1
    assert provider.operation_requests[0].explicit_role_evidence["source"]["status"] == (
        "NOT_SPECIFIED"
    )


def test_action_semantic_evidence_conflict_retries_then_accepts_router_choice() -> None:
    provider = _VNextProvider(
        grounding=[
            _travel_grounding(
                actor=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="ACTOR",
                    key="electrical_repair_team_alpha",
                    surface="actor",
                    match_semantics="SEMANTIC_EQUIVALENT",
                )
            )
        ],
        family="OPERATION",
        action_results=[
            {"action_match": "MATCHED", "action_key": "transport_resource"},
            {"action_match": "MATCHED", "action_key": "travel"},
        ],
        operation=[
            _operation(
                "travel",
                actor=_slot(
                    "actor",
                    "ACTOR",
                    "GROUNDED",
                    ref_type="ACTOR",
                    key="electrical_repair_team_alpha",
                ),
                target=_slot(
                    "target",
                    "REGION",
                    "GROUNDED",
                    ref_type="REGION",
                    key="east_residential_district",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "actor 前往东部居住区", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].action_key == "travel"
    assert len(provider.action_requests) == 2
    evidence = provider.action_requests[0].semantic_action_evidence
    assert evidence is not None
    assert evidence.action_key == "travel"
    assert evidence.surface == "前往"
    assert provider.action_requests[1].recovery_feedback[0]["code"] == (
        "ACTION_SEMANTIC_EVIDENCE_CONFLICT"
    )
    assert provider.operation_requests[0].action_key == "travel"


def test_action_semantic_evidence_conflict_fails_closed_after_bounded_retry() -> None:
    provider = _VNextProvider(
        grounding=[_travel_grounding()],
        family="OPERATION",
        action_results=[
            {"action_match": "MATCHED", "action_key": "transport_resource"},
            {"action_match": "MATCHED", "action_key": "transport_resource"},
        ],
    )

    with pytest.raises(GenericProviderError) as caught:
        GenericGoalResolver(provider=provider).resolve("前往东部居住区", LINJIANG_V2_TEST)

    assert caught.value.code == "PROVIDER_SCHEMA_INVALID"
    assert caught.value.validation_diagnostics[0]["code"] == ("ACTION_SEMANTIC_EVIDENCE_CONFLICT")
    assert provider.operation_requests == []


def test_grounded_invalid_exact_claim_triggers_one_semantic_recheck() -> None:
    invalid = _transport_role_grounding(
        source_status="GROUNDED",
        source_key="west_logistics_district",
        source_surface="30",
        target_key="east_residential_district",
        target_surface="东边",
        resource_match_semantics="EXACT_OR_AUTHORED",
    )
    recovered = _transport_role_grounding(
        source_status="GROUNDED",
        source_key="west_logistics_district",
        source_surface="30",
        target_key="east_residential_district",
        target_surface="东边",
        resource_match_semantics="SEMANTIC_EQUIVALENT",
    )
    operation = _operation(
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
                key="west_logistics_district",
            )
        ],
        parameters=[
            _slot("amount", "INTEGER", "GROUNDED", value=30),
            _slot(
                "resource_key",
                "RESOURCE",
                "GROUNDED",
                ref_type="RESOURCE",
                key="emergency_relief_supplies",
            ),
        ],
    )
    provider = _VNextProvider(
        grounding=[invalid, recovered],
        family="OPERATION",
        action_key="transport_resource",
        operation=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个救灾用品运到东边", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 2
    assert provider.grounding_requests[1].recovery_feedback[0]["code"] == (
        "ROLE_MATCH_SEMANTICS_RECHECK"
    )
    assert resolution.dynamic_requirements[0].parameter_constraints == {
        "resources": [{"resource_key": "emergency_relief_supplies", "amount": 30}]
    }


def test_invalid_exact_claim_remaining_unresolved_clarifies_after_recheck() -> None:
    invalid = _transport_role_grounding(
        target_key="east_residential_district",
        target_surface="东边",
        resource_match_semantics="EXACT_OR_AUTHORED",
    )
    unresolved = _transport_role_grounding(
        target_key="east_residential_district",
        target_surface="东边",
        resource_status="UNRESOLVED",
        resource_match_semantics=None,
    )
    provider = _VNextProvider(
        grounding=[invalid, unresolved],
        family="OPERATION",
        action_key="transport_resource",
        operation=[
            DynamicGoalOperationGrounding(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="请明确资源。",
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个救灾用品运到东边", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert len(provider.grounding_requests) == 2
    assert provider.grounding_requests[1].recovery_feedback[0]["code"] == (
        "ROLE_MATCH_SEMANTICS_RECHECK"
    )


def test_explicit_unresolved_source_triggers_one_semantic_grounding_recheck() -> None:
    first = _transport_role_grounding(
        target_key="central_district",
        target_surface="中央城区",
        resource_key="emergency_fuel",
        resource_surface="应急燃料",
        resource_match_semantics="EXACT_OR_AUTHORED",
        source_status="UNRESOLVED",
        source_surface="仓储物流一带",
    )
    second = _transport_role_grounding(
        target_key="central_district",
        target_surface="中央城区",
        resource_key="emergency_fuel",
        resource_surface="应急燃料",
        resource_match_semantics="EXACT_OR_AUTHORED",
        source_status="GROUNDED",
        source_key="west_logistics_district",
        source_surface="仓储物流一带",
    )
    operation = _operation(
        "transport_resource",
        target=_slot(
            "target",
            "REGION",
            "GROUNDED",
            ref_type="REGION",
            key="central_district",
        ),
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
    provider = _VNextProvider(
        grounding=[first, second],
        family="OPERATION",
        action_key="transport_resource",
        operation=[operation],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从仓储物流一带运30个应急燃料到中央城区", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 2
    assert provider.grounding_requests[1].recovery_feedback[0]["code"] == (
        "ROLE_SEMANTIC_GROUNDING_RECHECK"
    )
    assert resolution.dynamic_requirements[0].binding_constraints[0].value == (
        "west_logistics_district"
    )


def test_first_valid_semantic_equivalent_grounding_needs_no_extra_confirmation() -> None:
    grounding = _transport_role_grounding(
        source_status="GROUNDED",
        source_key="west_logistics_district",
        source_surface="30",
        target_key="east_residential_district",
        target_surface="东边",
        resource_match_semantics="SEMANTIC_EQUIVALENT",
    )
    provider = _VNextProvider(
        grounding=[grounding],
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
                        key="west_logistics_district",
                    )
                ],
                parameters=[
                    _slot("amount", "INTEGER", "GROUNDED", value=30),
                    _slot(
                        "resource_key",
                        "RESOURCE",
                        "GROUNDED",
                        ref_type="RESOURCE",
                        key="emergency_relief_supplies",
                    ),
                ],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个救灾用品运到东边", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
