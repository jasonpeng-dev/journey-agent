from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest
from sqlalchemy import select

from app.agent.generic import (
    GenericAgentService,
    GenericGoalResolver,
    _validate_dynamic_goal_operation_lock,
)
from app.agent.planning_context import PlanningContextBuilder
from app.agent.provider import (
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalIntentDraft,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    DynamicGoalMentionSlot,
    GenericProviderError,
    GoalSelection,
    GoalSelectionRequest,
    PlanProposal,
    PlanRequest,
)
from app.domain.action_invocation import ActionInvocationBinding
from app.domain.enums import AgentTaskStatus, ResourcePoolAvailability, ResourcePoolVisibility
from app.domain.formal_goal import (
    AdHocActionCompletedRequirementCandidateV1,
    AdHocFactRequirementCandidateV1,
    AdHocGoalCandidateSetV2,
    FormalGoalError,
    FormalGoalSourceKind,
    compile_predefined_formal_goal,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveRequirementKind, RelationDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import GameInstanceFactState, GameInstanceResourceState, Player
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0, require_builtin_v2_version
from app.scenarios.versions import ScenarioVersionRepository
from app.services.formal_goal import load_formal_goal_for_task
from app.services.game_instances import GameInstanceService
from app.services.play import PlayOrchestrator
from app.services.player_projection import PlayerProjectionService
from app.services.runtime_initialization import RuntimeInitializationService
from tests.dynamic_goal_helpers import dynamic_candidate as AdHocGoalRequirementCandidateV1
from tests.scenario_fixtures import GENERIC_TEST
from tests.unit.test_transport_resource import _definition as transport_definition


def _stage1_operation_grounding(
    *,
    action_key: str,
    target_key: str | None = None,
    target_ref_type: Literal["NODE", "REGION", "ACTOR"] = "REGION",
    source_key: str | None = None,
    resource_key: str | None = None,
    resource_status: Literal["GROUNDED", "UNRESOLVED", "NOT_SPECIFIED"] = "GROUNDED",
    amount: int | None = None,
    actor_key: str | None = None,
) -> DynamicGoalEntityGrounding:
    """Return a provider-shaped Stage 1 result for an explicit operation.

    The test fixture deliberately supplies the typed semantic result that a
    Stage 1 model is expected to return.  It does not derive roles from the
    natural-language surface, so synthetic Actions exercise the same generic
    resolver boundary as authored Actions.
    """

    refs = [DynamicGoalCandidateReference(ref_type="ACTION", key=action_key)]
    action = DynamicGoalMentionSlot(status="GROUNDED", ref_type="ACTION", key=action_key)
    if source_key is None:
        source = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    else:
        source = DynamicGoalMentionSlot(status="GROUNDED", ref_type="REGION", key=source_key)
        refs.append(DynamicGoalCandidateReference(ref_type="REGION", key=source_key))
    if target_key is None:
        target = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    else:
        target = DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type=target_ref_type,
            key=target_key,
        )
        refs.append(DynamicGoalCandidateReference(ref_type=target_ref_type, key=target_key))
    if resource_status == "NOT_SPECIFIED":
        resource = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    elif resource_status == "UNRESOLVED":
        resource = DynamicGoalMentionSlot(status="UNRESOLVED", ref_type="RESOURCE")
        if resource_key is not None:
            refs.append(DynamicGoalCandidateReference(ref_type="RESOURCE", key=resource_key))
    else:
        assert resource_key is not None
        resource = DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="RESOURCE",
            key=resource_key,
        )
        refs.append(DynamicGoalCandidateReference(ref_type="RESOURCE", key=resource_key))
    if amount is None:
        amount_slot = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    else:
        amount_slot = DynamicGoalMentionSlot(status="GROUNDED", value=amount)
    if actor_key is None:
        actor = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    else:
        actor = DynamicGoalMentionSlot(status="GROUNDED", ref_type="ACTOR", key=actor_key)
        refs.append(DynamicGoalCandidateReference(ref_type="ACTOR", key=actor_key))
    return DynamicGoalEntityGrounding(
        candidate_refs=tuple(refs),
        intent=DynamicGoalIntentDraft(
            intent_kind="OPERATION",
            action=action,
            actor=actor,
            source=source,
            target=target,
            resource=resource,
            amount=amount_slot,
        ),
    )


@dataclass
class _DynamicProvider:
    interpretation: object
    default_grounding: DynamicGoalEntityGrounding | None = None

    def __post_init__(self) -> None:
        self.requests: list[DynamicGoalInterpretationRequest] = []
        self.selection_requests: list[GoalSelectionRequest] = []
        self.grounding_requests: list[DynamicGoalEntityGroundingRequest] = []

    @property
    def model_name(self) -> str:
        return "dynamic-test-provider"

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        self.selection_requests.append(request)
        return GoalSelection(objective_keys=("establish_citywide_sustained_emergency_support",))

    def ground_dynamic_goal_entities(
        self,
        request: DynamicGoalEntityGroundingRequest,
    ) -> DynamicGoalEntityGrounding:
        self.grounding_requests.append(request)
        if self.default_grounding is not None:
            return self.default_grounding
        if isinstance(self.interpretation, DynamicGoalInterpretation):
            refs: list[DynamicGoalCandidateReference] = []
            for candidate in self.interpretation.requirements:
                if candidate.kind == ObjectiveRequirementKind.FACT:
                    assert candidate.node_key is not None
                    refs.append(
                        DynamicGoalCandidateReference(ref_type="NODE", key=candidate.node_key)
                    )
                elif candidate.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
                    assert candidate.region_key is not None and candidate.resource_key is not None
                    refs.extend(
                        (
                            DynamicGoalCandidateReference(
                                ref_type="REGION", key=candidate.region_key
                            ),
                            DynamicGoalCandidateReference(
                                ref_type="RESOURCE", key=candidate.resource_key
                            ),
                        )
                    )
                elif candidate.kind == ObjectiveRequirementKind.DERIVED_STATE:
                    assert candidate.derived_key is not None
                    refs.append(
                        DynamicGoalCandidateReference(
                            ref_type="DERIVED_STATE", key=candidate.derived_key
                        )
                    )
            if refs:
                unique_refs = {(item.ref_type, item.key): item for item in refs}
                return DynamicGoalEntityGrounding(candidate_refs=tuple(unique_refs.values()))
        goal = request.goal.casefold()
        references = request.public_catalog.get("references", ())
        if "patient" in goal:
            key = "patient_one"
            return DynamicGoalEntityGrounding(
                candidate_refs=(DynamicGoalCandidateReference(ref_type="NODE", key=key),)
            )
        if "capability" in goal:
            key = "east_emergency_power_network"
            return DynamicGoalEntityGrounding(
                candidate_refs=(DynamicGoalCandidateReference(ref_type="DERIVED_STATE", key=key),)
            )
        if "south terminal" in goal:
            return DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="NODE", key="south_fuel_terminal"),
                )
            )
        if "corridor" in goal or "走廊" in request.goal or "通道" in request.goal:
            key = "west_freight_corridor"
            if any(isinstance(item, dict) and item.get("key") == key for item in references):
                return DynamicGoalEntityGrounding(
                    candidate_refs=(DynamicGoalCandidateReference(ref_type="NODE", key=key),)
                )
        return DynamicGoalEntityGrounding(status="UNSUPPORTED")

    def interpret_dynamic_goal(
        self,
        request: DynamicGoalInterpretationRequest,
    ) -> object:
        self.requests.append(request)
        return self.interpretation

    def propose_plan(self, _request: PlanRequest) -> PlanProposal:
        raise AssertionError("Dynamic Goal creation test must not start planning")


class _GroundingProvider(_DynamicProvider):
    def __init__(
        self,
        grounding: DynamicGoalEntityGrounding,
        interpretations: tuple[DynamicGoalInterpretation, ...],
    ) -> None:
        super().__init__(DynamicGoalInterpretation(status="UNSUPPORTED"))
        self.grounding = grounding
        self.interpretations = list(interpretations)
        self.grounding_requests: list[DynamicGoalEntityGroundingRequest] = []

    def ground_dynamic_goal_entities(
        self,
        request: DynamicGoalEntityGroundingRequest,
    ) -> DynamicGoalEntityGrounding:
        self.grounding_requests.append(request)
        return self.grounding

    def interpret_dynamic_goal(
        self,
        request: DynamicGoalInterpretationRequest,
    ) -> object:
        self.requests.append(request)
        return self.interpretations.pop(0)


class _SequenceDynamicProvider(_DynamicProvider):
    def __init__(self, grounding: tuple[object, ...], interpretations: tuple[object, ...]) -> None:
        super().__init__(DynamicGoalInterpretation(status="UNSUPPORTED"))
        self.grounding_results = list(grounding)
        self.interpretation_results = list(interpretations)
        self.grounding_requests: list[DynamicGoalEntityGroundingRequest] = []

    def ground_dynamic_goal_entities(
        self,
        request: DynamicGoalEntityGroundingRequest,
    ) -> object:
        self.grounding_requests.append(request)
        if not self.grounding_results:
            if request.deterministic_candidate_refs:
                return DynamicGoalEntityGrounding(
                    candidate_refs=request.deterministic_candidate_refs,
                )
            return DynamicGoalEntityGrounding(status="UNSUPPORTED")
        result = self.grounding_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def interpret_dynamic_goal(
        self,
        request: DynamicGoalInterpretationRequest,
    ) -> object:
        self.requests.append(request)
        return self.interpretation_results.pop(0)


def _stable_candidate() -> AdHocGoalRequirementCandidateV1:
    return AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=(True,),
    )


def _runtime(session, definition=GENERIC_TEST, key="dynamic-goal"):
    version = require_builtin_v2_version(session, definition)
    assert version is not None
    player = Player(name=key)
    session.add(player)
    session.flush()
    return RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=key,
    )


def test_unmatched_linjiang_goal_routes_to_dynamic_fact_not_task5() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u901a\u897f\u90e8\u8d27\u8fd0\u901a\u9053",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert resolution.objective_keys == ()
    assert resolution.dynamic_requirements == (candidate,)
    assert provider.selection_requests == []
    assert len(provider.requests) == 1


def test_explicit_transport_goal_preserves_action_defined_source_binding() -> None:
    candidate = AdHocActionCompletedRequirementCandidateV1(
        kind="ACTION_COMPLETED",
        action_key="transport_resource",
        target_key="south_waterfront_district",
        binding_constraints=(
            ActionInvocationBinding(
                role="source_region",
                value="southeast_heights_district",
            ),
        ),
        parameter_constraints={
            "resource_key": "emergency_fuel",
            "amount": 30,
        },
    )
    provider = _DynamicProvider(
        DynamicGoalInterpretation(requirements=(candidate,)),
        default_grounding=_stage1_operation_grounding(
            action_key="transport_resource",
            source_key="southeast_heights_district",
            target_key="south_waterfront_district",
            resource_key="emergency_fuel",
            amount=30,
        ),
    )
    goal = (
        "\u4ece\u4e1c\u5357\u9ad8\u5730\u533a\u8fd0 30 "
        "\u4e2a\u5e94\u6025\u71c3\u6599\u5230\u5357\u90e8\u6ee8\u6c34\u533a"
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        goal,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(resolution.dynamic_requirements) == 1
    requirement = resolution.dynamic_requirements[0]
    assert requirement.action_key == candidate.action_key
    assert requirement.target_key == candidate.target_key
    assert requirement.binding_constraints == candidate.binding_constraints
    assert requirement.parameter_constraints == {
        "resources": [{"resource_key": "emergency_fuel", "amount": 30}]
    }
    assert len(provider.grounding_requests) == 1
    assert provider.requests == []
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["stage_2_skipped"] is True
    intent = resolution.provider_observation["intent"]
    assert isinstance(intent, dict)
    assert intent["intent_kind"] == "OPERATION"
    assert intent["action"]["status"] == "GROUNDED"
    assert intent["action"]["ref_type"] == "ACTION"
    assert intent["source"]["status"] == "GROUNDED"
    assert intent["source"]["ref_type"] == "REGION"
    assert intent["target"]["status"] == "GROUNDED"
    assert intent["target"]["ref_type"] == "REGION"
    assert intent["resource"]["status"] == "GROUNDED"
    assert intent["resource"]["ref_type"] == "RESOURCE"
    assert intent["amount"]["status"] == "GROUNDED"
    assert intent["amount"]["value"] == 30
    assert intent["actor"]["status"] == "NOT_SPECIFIED"
    assert resolution.provider_observation["grounded_operation"] == {
        "action_key": "transport_resource",
        "actor_key": None,
        "target_key": "south_waterfront_district",
        "binding_constraints": [{"role": "source_region", "value": "southeast_heights_district"}],
        "parameter_constraints": {"resource_key": "emergency_fuel", "amount": 30},
    }


def test_explicit_operation_without_source_keeps_source_and_actor_not_specified() -> None:
    candidate = AdHocActionCompletedRequirementCandidateV1(
        kind="ACTION_COMPLETED",
        action_key="transport_resource",
        target_key="south_waterfront_district",
        parameter_constraints={
            "resource_key": "emergency_fuel",
            "amount": 30,
        },
    )
    provider = _DynamicProvider(
        DynamicGoalInterpretation(requirements=(candidate,)),
        default_grounding=_stage1_operation_grounding(
            action_key="transport_resource",
            target_key="south_waterfront_district",
            resource_key="emergency_fuel",
            amount=30,
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个应急燃料运到南部滨水区",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert provider.requests == []
    assert resolution.provider_observation is not None
    intent = resolution.provider_observation["intent"]
    assert isinstance(intent, dict)
    assert intent["intent_kind"] == "OPERATION"
    assert intent["source"]["status"] == "NOT_SPECIFIED"
    assert intent["actor"]["status"] == "NOT_SPECIFIED"
    assert intent["target"]["key"] == "south_waterfront_district"
    assert intent["resource"]["key"] == "emergency_fuel"
    assert intent["amount"]["value"] == 30


@pytest.mark.parametrize(
    ("surface", "resource_key"),
    [
        ("电力部件", "electrical_repair_parts"),
        ("通用部件", "general_engineering_parts"),
    ],
)
def test_explicit_resource_shorthand_remains_unresolved_until_typed_grounding(
    surface: str,
    resource_key: str,
) -> None:
    provider = _SequenceDynamicProvider(
        (
            _stage1_operation_grounding(
                action_key="transport_resource",
                source_key="south_waterfront_district",
                target_key="southeast_heights_district",
                resource_key=resource_key,
                resource_status="UNRESOLVED",
                amount=30,
            ),
            _stage1_operation_grounding(
                action_key="transport_resource",
                source_key="south_waterfront_district",
                target_key="southeast_heights_district",
                resource_key=resource_key,
                amount=30,
            ),
        ),
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        f"从南部滨水区运30个{surface}到东南高地区",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 2
    assert provider.grounding_requests[0].intent is None
    grounding_intent = provider.grounding_requests[1].intent
    assert grounding_intent is not None
    assert grounding_intent.intent_kind == "OPERATION"
    assert grounding_intent.source.status == "GROUNDED"
    assert grounding_intent.target.status == "GROUNDED"
    assert grounding_intent.resource.status == "UNRESOLVED"
    assert grounding_intent.resource.ref_type == "RESOURCE"
    assert grounding_intent.amount.status == "GROUNDED"
    assert provider.requests == []
    assert resolution.provider_observation is not None
    interpretation_intent = resolution.provider_observation["intent"]
    assert isinstance(interpretation_intent, dict)
    assert interpretation_intent["resource"]["status"] == "GROUNDED"
    assert interpretation_intent["resource"]["key"] == resource_key


def test_ambiguous_explicit_resource_does_not_become_a_state_goal() -> None:
    grounding = _stage1_operation_grounding(
        action_key="transport_resource",
        source_key="south_waterfront_district",
        target_key="southeast_heights_district",
        resource_key="emergency_fuel",
        resource_status="UNRESOLVED",
        amount=30,
    ).model_copy(
        update={
            "candidate_refs": (
                DynamicGoalCandidateReference(ref_type="ACTION", key="transport_resource"),
                DynamicGoalCandidateReference(ref_type="REGION", key="south_waterfront_district"),
                DynamicGoalCandidateReference(ref_type="REGION", key="southeast_heights_district"),
                DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
                DynamicGoalCandidateReference(ref_type="RESOURCE", key="general_engineering_parts"),
            )
        }
    )
    provider = _GroundingProvider(
        grounding,
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从南部滨水区运30个资源到东南高地区",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.dynamic_requirements == ()
    assert provider.requests == []
    assert len(provider.grounding_requests) == 2


def test_synthetic_unique_region_grounding_stays_region_typed() -> None:
    definition = transport_definition()
    definition = definition.model_copy(
        update={
            "goal_resolution": definition.goal_resolution.model_copy(
                update={"allow_llm_fallback": True}
            )
        }
    )
    provider = _SequenceDynamicProvider(
        (
            _stage1_operation_grounding(
                action_key="transport_resource",
                target_key="region_a",
                resource_key="cargo_alpha",
                amount=30,
            ),
        ),
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "transport 30 cargo to eastern region",
        definition,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert provider.requests == []
    assert resolution.provider_observation is not None
    intent = resolution.provider_observation["intent"]
    assert isinstance(intent, dict)
    assert intent["target"]["status"] == "GROUNDED"
    assert intent["target"]["ref_type"] == "REGION"
    assert intent["target"]["key"] == "region_a"
    assert intent["resource"]["ref_type"] == "RESOURCE"


def test_explicit_operation_lock_skips_provider_reinterpretation() -> None:
    provider = _SequenceDynamicProvider(
        (
            _stage1_operation_grounding(
                action_key="transport_resource",
                source_key="southeast_heights_district",
                target_key="south_waterfront_district",
                resource_key="emergency_fuel",
                amount=30,
            ),
        ),
        (
            DynamicGoalInterpretation(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="Please specify an actor.",
            ),
            DynamicGoalInterpretation(
                requirements=(
                    AdHocFactRequirementCandidateV1(
                        kind="FACT",
                        node_key="southeast_access_corridor",
                        fact_key="passable",
                        accepted_values=(True,),
                    ),
                )
            ),
        ),
    )
    goal = (
        "\u4ece\u4e1c\u5357\u9ad8\u5730\u533a\u8fd030\u4e2a\u5e94\u6025\u71c3\u6599"
        "\u5230\u5357\u90e8\u6ee8\u6c34\u533a"
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        goal,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert isinstance(requirement, AdHocActionCompletedRequirementCandidateV1)
    assert requirement.action_key == "transport_resource"
    assert requirement.actor_key is None
    assert requirement.target_key == "south_waterfront_district"
    assert requirement.binding_constraints == (
        ActionInvocationBinding(
            role="source_region",
            value="southeast_heights_district",
        ),
    )
    assert requirement.parameter_constraints == {
        "resources": [{"resource_key": "emergency_fuel", "amount": 30}]
    }
    assert provider.requests == []
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["result"] == "DETERMINISTIC_GROUNDED_OPERATION"
    assert resolution.provider_observation["stage_2_skipped"] is True


def test_operation_without_directional_source_and_target_stays_clarifiable() -> None:
    grounding = _stage1_operation_grounding(
        action_key="transport_resource",
        resource_key="emergency_fuel",
        amount=30,
    )
    assert grounding.intent is not None
    grounding = grounding.model_copy(
        update={
            "candidate_refs": (
                DynamicGoalCandidateReference(ref_type="ACTION", key="transport_resource"),
                DynamicGoalCandidateReference(ref_type="REGION", key="southeast_heights_district"),
                DynamicGoalCandidateReference(ref_type="REGION", key="south_waterfront_district"),
                DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
            ),
            "intent": grounding.intent.model_copy(
                update={
                    "source": DynamicGoalMentionSlot(
                        status="UNRESOLVED", ref_type="REGION", surface="source region"
                    ),
                    "target": DynamicGoalMentionSlot(
                        status="UNRESOLVED", ref_type="REGION", surface="target region"
                    ),
                }
            ),
        }
    )
    provider = _SequenceDynamicProvider(
        (grounding,),
        (),
    )
    goal = (
        "\u8fd030\u4e2a\u5e94\u6025\u71c3\u6599\uff0c\u5728\u4e1c\u5357\u9ad8\u5730\u533a"
        "\u548c\u5357\u90e8\u6ee8\u6c34\u533a\u4e4b\u95f4\u8fd0\u8f93"
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        goal,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert provider.requests == []
    assert len(provider.grounding_requests) == 2


def test_explicit_operation_lock_is_generic_for_synthetic_transport() -> None:
    definition = transport_definition()
    definition = definition.model_copy(
        update={
            "goal_resolution": definition.goal_resolution.model_copy(
                update={"allow_llm_fallback": True}
            )
        }
    )
    provider = _SequenceDynamicProvider(
        (
            _stage1_operation_grounding(
                action_key="transport_resource",
                source_key="region_a",
                target_key="region_b",
                resource_key="cargo_alpha",
                amount=30,
            ),
        ),
        (
            DynamicGoalInterpretation(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="Please specify the source.",
            ),
            DynamicGoalInterpretation(status="UNSUPPORTED"),
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "transport 30 cargo alpha from region a to region b",
        definition,
    )

    assert resolution.status == "RESOLVED"
    requirement = resolution.dynamic_requirements[0]
    assert isinstance(requirement, AdHocActionCompletedRequirementCandidateV1)
    assert requirement.action_key == "transport_resource"
    assert requirement.target_key == "region_b"
    assert requirement.binding_constraints == (
        ActionInvocationBinding(role="source_region", value="region_a"),
    )
    assert requirement.parameter_constraints == {
        "resources": [{"resource_key": "cargo_alpha", "amount": 30}]
    }


def test_stage1_can_ground_a_new_action_without_resolver_lexical_rules() -> None:
    definition = transport_definition()
    synthetic_action = definition.actions[0].model_copy(
        update={
            "key": "relocate_clinical_kits",
            "name": "Reposition Clinical Kits",
            "description": "Move a payload between public regions along one corridor.",
        }
    )
    definition = definition.model_copy(
        update={
            "actions": (synthetic_action,),
            "goal_resolution": definition.goal_resolution.model_copy(
                update={"allow_llm_fallback": True}
            ),
        }
    )
    provider = _SequenceDynamicProvider(
        (
            _stage1_operation_grounding(
                action_key="relocate_clinical_kits",
                source_key="region_a",
                target_key="region_b",
                resource_key="cargo_alpha",
                amount=30,
            ),
        ),
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "relocate 30 cargo alpha from region a into region b",
        definition,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert provider.requests == []
    grounding_request = provider.grounding_requests[0]
    action_reference = next(
        item
        for item in grounding_request.public_catalog["references"]
        if isinstance(item, dict) and item.get("key") == "relocate_clinical_kits"
    )
    assert action_reference["name"] == "Reposition Clinical Kits"
    assert action_reference["operation_binding_contract"]
    requirement = resolution.dynamic_requirements[0]
    assert isinstance(requirement, AdHocActionCompletedRequirementCandidateV1)
    assert requirement.action_key == "relocate_clinical_kits"
    assert requirement.target_key == "region_b"
    assert requirement.binding_constraints == (
        ActionInvocationBinding(role="source_region", value="region_a"),
    )
    assert requirement.parameter_constraints == {
        "resources": [{"resource_key": "cargo_alpha", "amount": 30}]
    }


def test_partial_typed_operation_grounding_adds_semantic_resource_candidate() -> None:
    definition = transport_definition()
    definition = definition.model_copy(
        update={
            "goal_resolution": definition.goal_resolution.model_copy(
                update={"allow_llm_fallback": True}
            )
        }
    )
    provider = _SequenceDynamicProvider(
        (
            _stage1_operation_grounding(
                action_key="transport_resource",
                source_key="region_a",
                target_key="region_b",
                resource_key="cargo_alpha",
                amount=30,
            ),
        ),
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "transport 30 cargo from region a to region b",
        definition,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    grounding_request = provider.grounding_requests[0]
    assert grounding_request.deterministic_candidate_refs
    assert set(grounding_request.deterministic_candidate_refs) >= {
        DynamicGoalCandidateReference(ref_type="REGION", key="region_a"),
        DynamicGoalCandidateReference(ref_type="REGION", key="region_b"),
    }
    action_reference = next(
        item
        for item in grounding_request.public_catalog["references"]
        if item["ref_type"] == "ACTION" and item["key"] == "transport_resource"
    )
    assert action_reference["parameters"]
    assert action_reference["operation_binding_contract"]
    assert provider.requests == []
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["stage_2_skipped"] is True


def test_directional_partial_grounding_does_not_change_non_operation_resource_goal() -> None:
    definition = transport_definition()
    definition = definition.model_copy(
        update={
            "goal_resolution": definition.goal_resolution.model_copy(
                update={"allow_llm_fallback": True}
            )
        }
    )
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
        region_key="region_a",
        resource_key="cargo_alpha",
        minimum=30,
    )
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(ref_type="REGION", key="region_a"),
                DynamicGoalCandidateReference(ref_type="RESOURCE", key="cargo_alpha"),
            )
        ),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "keep 30 cargo alpha in region a",
        definition,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert len(provider.requests) == 1


def test_explicit_operation_lock_recovers_from_legacy_binding_shape() -> None:
    provider = _SequenceDynamicProvider(
        (
            _stage1_operation_grounding(
                action_key="transport_resource",
                source_key="southeast_heights_district",
                target_key="south_waterfront_district",
                resource_key="emergency_fuel",
                amount=30,
            ),
        ),
        (
            {
                "status": "RESOLVED",
                "requirements": [
                    {
                        "kind": "ACTION_COMPLETED",
                        "action_key": "transport_resource",
                        "target_key": "south_waterfront_district",
                        "binding_constraints": [
                            {
                                "role": "source_region",
                                "source": "EXECUTION_START_ACTOR_REGION",
                                "value_type": "REGION",
                            }
                        ],
                        "parameter_constraints": {
                            "resource_key": "emergency_fuel",
                            "amount": 30,
                        },
                    }
                ],
            },
            DynamicGoalInterpretation(status="UNSUPPORTED"),
        ),
    )
    goal = (
        "\u4ece\u4e1c\u5357\u9ad8\u5730\u533a\u8fd030\u4e2a\u5e94\u6025\u71c3\u6599"
        "\u5230\u5357\u90e8\u6ee8\u6c34\u533a"
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        goal,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert provider.requests == []
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["provider_fallback"] == "GROUNDED_OPERATION_LOCK"
    assert resolution.provider_observation["stage_2_skipped"] is True


def test_operation_lock_rejects_changed_or_expanded_stage_two_response() -> None:
    locked_requirement = AdHocActionCompletedRequirementCandidateV1(
        kind="ACTION_COMPLETED",
        action_key="transport_resource",
        target_key="region_b",
        binding_constraints=(ActionInvocationBinding(role="source_region", value="region_a"),),
        parameter_constraints={"resource_key": "cargo_alpha", "amount": 30},
    )
    locked = AdHocGoalCandidateSetV2(requirements=(locked_requirement,))
    changed = AdHocGoalCandidateSetV2(
        requirements=(locked_requirement.model_copy(update={"target_key": "region_c"}),)
    )
    expanded = AdHocGoalCandidateSetV2(
        requirements=(
            locked_requirement,
            AdHocFactRequirementCandidateV1(
                kind="FACT",
                node_key="region_b",
                fact_key="delivered",
                accepted_values=(True,),
            ),
        )
    )

    for actual in (changed, expanded):
        with pytest.raises(FormalGoalError) as error:
            _validate_dynamic_goal_operation_lock(actual, locked)
        assert error.value.code == "OPERATION_LOCK_MISMATCH"
        assert error.value.details["mismatch"] == "EXACT_OPERATION_LOCK"


def test_exact_public_entity_uses_focused_ontology_and_one_recovery() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
        (
            DynamicGoalInterpretation(status="UNSUPPORTED"),
            DynamicGoalInterpretation(requirements=(candidate,)),
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u897f\u90e8\u8d27\u8fd0\u8d70\u5eca",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert len(provider.grounding_requests) == 1
    assert len(provider.requests) == 2
    focused_keys = {
        "west_freight_corridor",
        "central_district",
        "west_logistics_district",
    }
    assert {item["key"] for item in provider.requests[0].ontology["world"]["nodes"]} == (
        focused_keys
    )
    assert provider.requests[0].grounded_entity_keys == ("west_freight_corridor",)
    assert provider.requests[0].recovery_attempt == 0
    assert provider.requests[1].recovery_attempt == 1
    assert provider.requests[0].ontology == provider.requests[1].ontology
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["attempt_count"] == 2
    assert resolution.provider_observation["grounding"]["source"] == (
        "MODEL_ENTITY_GROUNDING_WITH_DETERMINISTIC_REFS"
    )


def test_colloquial_public_entity_uses_one_bounded_grounding_call() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u897f\u8fb9\u90a3\u6761\u8d27\u8fd0\u8d70\u9053",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    catalog = provider.grounding_requests[0].public_catalog
    assert "objectives" not in str(catalog)
    assert "facts" not in str(catalog)
    assert "initial_value" not in str(catalog)
    ontology = provider.requests[0].ontology
    assert {item["key"] for item in ontology["world"]["nodes"]} == {
        "west_freight_corridor",
        "central_district",
        "west_logistics_district",
    }
    assert provider.requests[0].grounded_entity_keys == ("west_freight_corridor",)


def test_public_topology_uniquely_grounds_relation_without_model_search() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="central_river_tunnel",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u4e2d\u592e\u57ce\u533a\u548c\u4e1c\u90e8\u5c45\u4f4f\u533a\u4e2d\u95f4\u90a3\u6761\u8def",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert provider.selection_requests == []
    assert len(provider.requests) == 1
    assert provider.requests[0].grounded_entity_keys == ("central_river_tunnel",)
    assert provider.requests[0].ontology["grounding"]["source"] == (
        "MODEL_ENTITY_GROUNDING_WITH_DETERMINISTIC_REFS"
    )


def test_ambiguous_public_topology_clarifies_without_arbitrary_pick() -> None:
    original = LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
    transport = next(item for item in original.world.nodes if item.key == "central_river_tunnel")
    second_transport = transport.model_copy(
        update={"key": "central_river_tunnel_backup", "name": "备用中央河底隧道"}
    )
    extra_relations = (
        RelationDefinitionV2(
            key="central_river_tunnel_backup__endpoint__central_district",
            source_node_key="central_river_tunnel_backup",
            relation_type_key="endpoint",
            target_node_key="central_district",
        ),
        RelationDefinitionV2(
            key="central_river_tunnel_backup__endpoint__east_residential_district",
            source_node_key="central_river_tunnel_backup",
            relation_type_key="endpoint",
            target_node_key="east_residential_district",
        ),
    )
    world = original.world.model_copy(
        update={
            "nodes": (*original.world.nodes, second_transport),
            "relations": (*original.world.relations, *extra_relations),
        }
    )
    definition = original.model_copy(update={"world": world})
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(ref_type="NODE", key="central_river_tunnel"),
                DynamicGoalCandidateReference(ref_type="NODE", key="central_river_tunnel_backup"),
            )
        ),
        (
            DynamicGoalInterpretation(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="Which corridor should this Goal target?",
            ),
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u4e2d\u592e\u57ce\u533a\u548c\u4e1c\u90e8\u5c45\u4f4f\u533a\u4e2d\u95f4\u90a3\u6761\u8def",
        definition,
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.objective_keys == ()
    assert len(provider.requests) == 1
    assert provider.requests[0].grounded_entity_keys == (
        "central_river_tunnel",
        "central_river_tunnel_backup",
    )


def test_entity_grounding_unsupported_does_not_start_interpretation() -> None:
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(status="UNSUPPORTED"),
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Invent a galaxy-scale ontology requirement",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert len(provider.grounding_requests) == 2
    assert provider.requests == []
    assert resolution.objective_keys == ()


def test_entity_grounding_retries_retryable_results_and_reuses_successful_grounding() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _SequenceDynamicProvider(
        (
            GenericProviderError("MODEL_PROVIDER_RESPONSE_INVALID", "invalid"),
            DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
        ),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u897f\u8fb9\u90a3\u6761\u8d27\u8fd0\u8d70\u9053",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 2
    assert len(provider.requests) == 1
    assert provider.requests[0].grounded_entity_keys == ("west_freight_corridor",)
    assert resolution.provider_observation is not None
    assert [item["result"] for item in resolution.provider_observation["attempts"]] == [
        "MODEL_PROVIDER_RESPONSE_INVALID",
        "BACKEND_ACCEPTED",
        "MODEL_ACCEPTED",
    ]


def test_entity_grounding_clarification_stops_without_retry() -> None:
    provider = _SequenceDynamicProvider(
        (
            DynamicGoalEntityGrounding(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="Which corridor?",
            ),
            DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
        ),
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Repair a western corridor",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert len(provider.grounding_requests) == 1
    assert provider.requests == []


def test_two_by_two_retries_interpretation_without_regrounding_after_first_rejection() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _SequenceDynamicProvider(
        (DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),),
        (
            DynamicGoalInterpretation(status="UNSUPPORTED"),
            DynamicGoalInterpretation(requirements=(candidate,)),
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "A western corridor terminal goal",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert len(provider.requests) == 2
    assert [request.recovery_attempt for request in provider.requests] == [0, 1]
    assert resolution.provider_observation is not None
    calls = resolution.provider_observation["provider_calls"]
    assert [call["purpose"] for call in calls] == [
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
        "DYNAMIC_GOAL_INTERPRETATION",
    ]
    assert [call["grounding_round"] for call in calls] == [1, 1, 1]
    assert [call["interpretation_attempt"] for call in calls[1:]] == [1, 2]


def test_interpretation_schema_recovery_reuses_grounding_projection_and_feedback() -> None:
    derived_key = "north_basic_engineering_support"
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.DERIVED_STATE,
        derived_key=derived_key,
        accepted_values=("AVAILABLE",),
    )
    provider = _SequenceDynamicProvider(
        (
            DynamicGoalEntityGrounding(
                candidate_refs=(
                    DynamicGoalCandidateReference(ref_type="DERIVED_STATE", key=derived_key),
                )
            ),
        ),
        (
            {
                "status": "RESOLVED",
                "requirements": [{"kind": "DERIVED_STATE", "derived_key": derived_key}],
            },
            DynamicGoalInterpretation(requirements=(candidate,)),
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Restore northern basic engineering support",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert len(provider.requests) == 2
    first_request, recovery_request = provider.requests
    assert recovery_request.grounded_candidate_refs == first_request.grounded_candidate_refs
    assert recovery_request.ontology == first_request.ontology
    assert recovery_request.recovery_attempt == 1
    assert [item.model_dump(mode="json") for item in recovery_request.recovery_feedback] == [
        {
            "requirement_index": 0,
            "kind": "DERIVED_STATE",
            "issue": "MISSING_REQUIRED_FIELD",
            "field": "accepted_values",
            "expected_shape": {
                "kind": "DERIVED_STATE",
                "derived_key": derived_key,
                "accepted_values": ["AVAILABLE"],
            },
            "focused_target_value": "AVAILABLE",
        }
    ]


def test_debug_observation_records_each_logical_call_input_and_output() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _SequenceDynamicProvider(
        (DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),),
        (
            DynamicGoalInterpretation(status="UNSUPPORTED"),
            DynamicGoalInterpretation(requirements=(candidate,)),
        ),
    )

    resolution = GenericGoalResolver(
        provider=provider,
        observability_level="DEBUG",
    ).resolve(
        "A debug public goal",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.provider_observation is not None
    calls = resolution.provider_observation["provider_calls"]
    assert [call["purpose"] for call in calls] == [
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
        "DYNAMIC_GOAL_INTERPRETATION",
    ]
    assert all("debug_snapshot" in call for call in calls)
    assert calls[0]["debug_snapshot"]["input"]["public_catalog"]
    assert calls[0]["debug_snapshot"]["output"]["status"] == "RESOLVED"
    assert calls[1]["debug_snapshot"]["input"]["recovery_attempt"] == 0
    assert calls[1]["debug_snapshot"]["output"]["status"] == "UNSUPPORTED"
    assert calls[2]["debug_snapshot"]["input"]["recovery_attempt"] == 1
    assert calls[2]["debug_snapshot"]["output"]["requirements"]
    assert calls[2]["projection"]["allowed_fact_keys"]


def test_normal_observation_does_not_create_debug_snapshot() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _SequenceDynamicProvider(
        (DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "A normal public goal",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.provider_observation is not None
    assert all(
        "debug_snapshot" not in call for call in resolution.provider_observation["provider_calls"]
    )


def test_two_by_two_regrounds_after_two_interpretation_rejections() -> None:
    first_candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="central_telecom_hub",
        fact_key="operational",
        accepted_values=(True,),
    )
    provider = _SequenceDynamicProvider(
        (
            DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
            DynamicGoalEntityGrounding(candidate_keys=("central_telecom_hub",)),
        ),
        (
            DynamicGoalInterpretation(requirements=(first_candidate,)),
            DynamicGoalInterpretation(requirements=(first_candidate,)),
            DynamicGoalInterpretation(requirements=(first_candidate,)),
        ),
    )

    resolution = GenericGoalResolver(
        provider=provider,
        observability_level="DEBUG",
    ).resolve("A bounded public goal", LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 2
    assert len(provider.requests) == 3
    assert provider.requests[0].grounded_entity_keys == ("west_freight_corridor",)
    assert provider.requests[1].grounded_entity_keys == ("west_freight_corridor",)
    assert provider.requests[2].grounded_entity_keys == ("central_telecom_hub",)
    assert resolution.provider_observation is not None
    calls = resolution.provider_observation["provider_calls"]
    assert [call["call_type"] for call in calls] == [
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
        "DYNAMIC_GOAL_INTERPRETATION",
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
    ]
    assert [call["call_order"] for call in calls] == [1, 2, 3, 4, 5]
    assert [(call["grounding_round"], call.get("interpretation_attempt")) for call in calls] == [
        (1, None),
        (1, 1),
        (1, 2),
        (2, None),
        (2, 1),
    ]
    assert all("debug_snapshot" in call for call in calls)
    assert calls[0]["debug_snapshot"]["input"]["public_catalog"]
    assert calls[1]["debug_snapshot"]["input"]["recovery_attempt"] == 0
    assert calls[2]["debug_snapshot"]["input"]["recovery_attempt"] == 1
    assert calls[3]["debug_snapshot"]["input"]["public_catalog"]
    assert calls[4]["debug_snapshot"]["input"]["recovery_attempt"] == 0


def test_invalid_grounding_reference_skips_interpretation_and_uses_second_round() -> None:
    candidate = _stable_candidate()
    provider = _SequenceDynamicProvider(
        (
            DynamicGoalEntityGrounding(candidate_keys=("not_public",)),
            DynamicGoalEntityGrounding(candidate_keys=("patient_one",)),
        ),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "A patient terminal goal",
        GENERIC_TEST,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 2
    assert len(provider.requests) == 1
    assert resolution.provider_observation is not None
    assert [call["purpose"] for call in resolution.provider_observation["provider_calls"]] == [
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
    ]


def test_grounding_unsupported_is_bounded_to_two_rounds() -> None:
    provider = _SequenceDynamicProvider(
        (
            DynamicGoalEntityGrounding(status="UNSUPPORTED"),
            DynamicGoalEntityGrounding(status="UNSUPPORTED"),
        ),
        (),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "An unsupported public goal",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert len(provider.grounding_requests) == 2
    assert provider.requests == []
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["grounding_round_count"] == 2


def test_two_by_two_failure_does_not_issue_a_third_grounding_or_interpretation_call() -> None:
    provider = _SequenceDynamicProvider(
        (
            DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
            DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
        ),
        (DynamicGoalInterpretation(status="UNSUPPORTED"),) * 4,
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "A public goal that cannot be interpreted",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert len(provider.grounding_requests) == 2
    assert len(provider.requests) == 4
    assert resolution.provider_observation is not None
    assert len(resolution.provider_observation["provider_calls"]) == 6
    assert [call["purpose"] for call in resolution.provider_observation["provider_calls"]] == [
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
        "DYNAMIC_GOAL_INTERPRETATION",
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
        "DYNAMIC_GOAL_INTERPRETATION",
    ]


def test_interpretation_retries_twice_without_regrounding() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _SequenceDynamicProvider(
        (),
        (
            DynamicGoalInterpretation(status="UNSUPPORTED"),
            DynamicGoalInterpretation(requirements=(candidate,)),
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u897f\u90e8\u8d27\u8fd0\u8d70\u5eca",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 1
    assert len(provider.requests) == 2
    assert [request.recovery_attempt for request in provider.requests] == [0, 1]
    assert provider.requests[0].ontology == provider.requests[1].ontology
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["attempt_count"] == 2
    assert [item["result"] for item in resolution.provider_observation["attempts"]] == [
        "BACKEND_ACCEPTED",
        "MODEL_UNSUPPORTED",
        "MODEL_ACCEPTED",
    ]


def test_entity_grounding_cannot_invent_a_public_entity_key() -> None:
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(candidate_keys=("invented_entity",)),
        (DynamicGoalInterpretation(status="UNSUPPORTED"),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Repair the western corridor",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert provider.requests == []
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["result"] == "BACKEND_VALIDATION_REJECTED"
    assert resolution.provider_observation["rejection_code"] == (
        "FORMAL_GOAL_DYNAMIC_ENTITY_NOT_PUBLIC"
    )


def test_canonical_task5_goal_routes_to_public_derived_capability() -> None:
    objective = next(
        item
        for item in LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.objectives
        if item.key == "establish_citywide_sustained_emergency_support"
    )
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.DERIVED_STATE,
        derived_key="citywide_sustained_emergency_support",
        accepted_values=("AVAILABLE",),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))

    resolution = GenericGoalResolver(provider=provider).resolve(
        objective.name,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["stage"] == "DYNAMIC_GOAL_INTERPRETATION"
    assert resolution.objective_keys == ()
    assert len(resolution.dynamic_requirements) == 1
    assert resolution.dynamic_requirements[0].kind == ObjectiveRequirementKind.DERIVED_STATE
    assert resolution.dynamic_requirements[0].derived_key == (
        "citywide_sustained_emergency_support"
    )
    assert provider.selection_requests == []
    assert len(provider.requests) == 1


def test_canonical_task6_goal_and_alias_keep_hidden_semantics(
    session,
) -> None:
    objective = next(
        item
        for item in LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.objectives
        if item.key == "establish_sustained_emergency_generation"
    )
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.DERIVED_STATE,
        derived_key="southeast_sustained_emergency_generation",
        accepted_values=("AVAILABLE",),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    resolver = GenericGoalResolver(provider=provider)

    for goal in (objective.name, *objective.goal_aliases):
        resolution = resolver.resolve(goal, LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
        assert resolution.status == "RESOLVED"
        assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
        assert resolution.objective_keys == ()
        assert len(resolution.dynamic_requirements) == 1
        assert resolution.dynamic_requirements[0].kind == ObjectiveRequirementKind.DERIVED_STATE
        assert resolution.dynamic_requirements[0].derived_key == (
            "southeast_sustained_emergency_generation"
        )

    version = require_builtin_v2_version(session, LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
    assert version is not None
    contract = compile_predefined_formal_goal(
        ScenarioVersionRepository(session).load(version.id),
        (objective,),
    )
    assert contract.source_kind == FormalGoalSourceKind.PREDEFINED
    requirement = contract.completion_requirements[0].requirement
    assert requirement.kind == ObjectiveRequirementKind.DERIVED_STATE
    assert requirement.derived_key == "southeast_sustained_emergency_generation"
    derived = ScenarioVersionRepository(session).load(version.id).definition
    assert any(
        item.knowledge_gate is not None
        for item in derived.derived_state_definitions[
            "southeast_sustained_emergency_generation"
        ].dependencies
    )
    assert provider.selection_requests == []
    assert len(provider.requests) == 2


def test_unsupported_dynamic_goal_does_not_fallback_to_predefined() -> None:
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Invent an unsupported ontology requirement",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()
    assert provider.selection_requests == []
    assert len(provider.grounding_requests) == 2
    assert provider.requests == []


def test_ambiguous_dynamic_goal_does_not_fallback_to_predefined() -> None:
    provider = _DynamicProvider(
        DynamicGoalInterpretation(
            status="NEEDS_CLARIFICATION",
            clarification_prompt="Which corridor outcome do you want?",
        )
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Make the western corridor useful",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.objective_keys == ()
    assert resolution.clarification_prompt == "Which corridor outcome do you want?"
    assert provider.selection_requests == []
    assert len(provider.requests) == 1


def test_dynamic_interpreter_is_strict_and_knowledge_safe() -> None:
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(_stable_candidate(),)))
    resolution = GenericGoalResolver(provider=provider).resolve(
        "Keep the patient stable",
        GENERIC_TEST,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.objective_keys == ()
    assert resolution.dynamic_requirements == (_stable_candidate(),)
    assert provider.requests
    ontology = provider.requests[0].ontology
    assert "objectives" not in ontology
    assert "actions" not in ontology
    assert "initial_value" not in str(ontology)
    assert ontology["goal_language"] == {
        "requirement_kinds": ["FACT"],
        "combination": "IMPLICIT_AND",
        "comparison": "AT_LEAST_FOR_RESOURCE",
    }


def test_dynamic_interpreter_returns_fact_values_after_backend_canonicalization() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=("true",),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Keep the patient stable",
        GENERIC_TEST,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].accepted_values == (True,)


def test_dynamic_interpreter_records_safe_type_diagnostic_on_rejection() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=("yes",),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Keep the patient stable",
        GENERIC_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["rejection_code"] == ("FORMAL_GOAL_VALUE_TYPE_INVALID")
    assert resolution.provider_observation["value_type_diagnostics"] == [
        {
            "expected_value_type": "BOOLEAN",
            "actual_candidate_json_type": "string",
        }
    ]


def test_dynamic_interpreter_malformed_response_fails_closed() -> None:
    provider = _DynamicProvider(
        {"status": "RESOLVED", "requirements": []},
        default_grounding=DynamicGoalEntityGrounding(
            candidate_refs=(DynamicGoalCandidateReference(ref_type="NODE", key="patient_one"),)
        ),
    )

    with pytest.raises(ValueError, match="invalid Dynamic Goal interpretation"):
        GenericGoalResolver(provider=provider).resolve("unstated goal", GENERIC_TEST)


def test_dynamic_goal_freezes_without_legacy_objective_scope(session) -> None:
    runtime = _runtime(session)
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(_stable_candidate(),)))
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    agent = GenericAgentService(session, scope, provider=provider)

    task = agent.create_task(
        runtime.session,
        "Keep the patient stable",
        initialize_plan=False,
    )
    contract = load_formal_goal_for_task(session, scope, task)

    assert task.objective_scope_keys is None
    assert task.objective_catalog_version is None
    assert task.formal_goal_source_kind == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert task.objective_scope_hash == contract.content_hash
    assert contract.predefined_objectives == ()
    assert tuple(item.identity for item in contract.completion_requirements)
    assert agent.evaluate(task).completed is False


def test_dynamic_goal_player_projection_keeps_typed_identity(session) -> None:
    runtime = _runtime(session)
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(_stable_candidate(),)))
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    task = GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session,
        "Keep the patient stable",
        initialize_plan=False,
    )

    projected = PlayerProjectionService(session).task(
        task,
        GENERIC_TEST,
        known_facts={("patient_one", "stable"): False},
    )

    assert projected.goal_source_kind == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert len(projected.goal_requirements) == 1
    requirement = projected.goal_requirements[0]
    assert requirement.identity == requirement.key
    assert requirement.kind == "FACT"
    assert requirement.node_key == "patient_one"
    assert requirement.description.startswith(f"{GENERIC_TEST.world.node('patient_one').name}: ")
    assert GENERIC_TEST.world.node("patient_one").fact("stable").name in requirement.description
    assert "patient_one" not in requirement.description
    assert "stable" not in requirement.description
    assert "has the requested value" not in requirement.description
    assert projected.roadmap.stages[0].objective_key is None


def test_play_dynamic_goal_idempotency_reuses_frozen_contract(session) -> None:
    runtime = _runtime(session)
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(_stable_candidate(),)))
    orchestrator = PlayOrchestrator(
        session,
        GameInstanceId(runtime.instance.id),
        provider=provider,
    )

    first = orchestrator.submit_goal("Keep the patient stable", idempotency_key="dynamic-1")
    second = orchestrator.submit_goal("Keep the patient stable", idempotency_key="dynamic-1")

    assert first.task is not None
    assert first.resolution.dynamic_requirements == (_stable_candidate(),)
    assert second.replayed is True
    assert second.task is first.task
    assert second.resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert len(provider.requests) == 1


def test_dynamic_resource_requirement_reuses_the_generic_typed_path(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-resource-goal",
    )
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
        region_key="southeast_heights_district",
        resource_key="electrical_repair_parts",
        minimum=100,
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    task = GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session,
        "Maintain an electrical parts reserve in the southeast",
        initialize_plan=False,
    )

    contract = load_formal_goal_for_task(session, scope, task)
    assert len(contract.completion_requirements) == 1
    assert contract.completion_requirements[0].requirement.kind == (
        ObjectiveRequirementKind.RESOURCE_AT_LEAST
    )
    ontology_resources = provider.requests[0].ontology["world"]["resources"]
    assert any(item["key"] == "electrical_repair_parts" for item in ontology_resources)
    assert all(set(item) <= {"key", "name", "description"} for item in ontology_resources)
    assert GenericAgentService(session, scope, provider=provider).evaluate(task).completed is False


def test_dynamic_catalog_exposes_public_derived_metadata_without_dependencies() -> None:
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Describe a public capability",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert len(provider.requests) == 4
    ontology = provider.requests[0].ontology
    assert "DERIVED_STATE" in ontology["goal_language"]["requirement_kinds"]
    derived = ontology["world"]["derived_states"]
    assert derived
    capability = next(item for item in derived if item["key"] == "east_emergency_power_network")
    assert set(capability) == {
        "key",
        "name",
        "description",
        "value_type",
        "target_value",
        "non_target_value",
        "goal_aliases",
        "allowed_values",
    }
    assert "dependencies" not in capability
    assert "sustained_requirements_discovered" not in str(ontology)
    assert "initial_value" not in str(ontology)
    assert "truth_value" not in str(ontology)
    assert "current_value" not in str(ontology)
    assert ontology["world"]["facts"] == []


def test_dynamic_catalog_excludes_non_goal_addressable_derived_state() -> None:
    public_state = next(
        item
        for item in LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.derived_states
        if item.key == "east_emergency_power_network"
    )
    internal_state = public_state.model_copy(
        update={
            "key": "internal_discovery_state",
            "name": "Internal discovery state",
            "goal_addressable": False,
        }
    )
    definition = LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.model_copy(
        update={
            "derived_states": (
                *LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.derived_states,
                internal_state,
            )
        }
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Describe a public capability",
        definition,
    )

    assert resolution.status == "UNSUPPORTED"
    ontology = provider.requests[0].ontology
    assert all(
        item["key"] != "internal_discovery_state" for item in ontology["world"]["derived_states"]
    )
    assert "internal_discovery_state" not in str(ontology)


def test_dynamic_interpreter_can_resolve_public_derived_capability() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.DERIVED_STATE,
        derived_key="east_emergency_power_network",
        accepted_values=("AVAILABLE",),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Restore the public east power capability",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert resolution.dynamic_requirements == (candidate,)
    assert len(provider.requests) == 1
    assert any(
        item["key"] == "east_emergency_power_network"
        for item in provider.requests[0].ontology["world"]["derived_states"]
    )


def test_dynamic_multiple_requirements_are_implicit_and(session) -> None:
    runtime = _runtime(session, key="dynamic-and-goal")
    candidates = (
        _stable_candidate(),
        AdHocGoalRequirementCandidateV1(
            kind=ObjectiveRequirementKind.FACT,
            node_key="patient_one",
            fact_key="diagnosis",
            accepted_values=("INFECTION",),
        ),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=candidates))
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    task = GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session,
        "Diagnose and stabilize the patient",
        initialize_plan=False,
    )

    contract = load_formal_goal_for_task(session, scope, task)
    assert len(contract.completion_requirements) == 2
    assert not GenericAgentService(session, scope, provider=provider).evaluate(task).completed


def test_dynamic_interpreter_never_receives_hidden_fact_truth_or_metadata(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-knowledge-boundary",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))
    resolution = GenericGoalResolver(
        provider=provider,
        db=session,
        scope=scope,
    ).resolve(
        "What is happening at the south terminal?",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    ontology = provider.requests[0].ontology
    assert "objectives" not in ontology
    assert "actions" not in ontology
    assert any(
        fact["fact_key"] == "operational" and fact["node_key"] == "south_fuel_terminal"
        for fact in ontology["world"]["facts"]
    )
    assert all(
        fact["fact_key"] != "sustained_requirements_discovered"
        for fact in ontology["world"]["facts"]
    )
    assert "initial_value" not in str(ontology)


def test_dynamic_goal_exposes_hidden_facility_fact_schema_without_truth(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-hidden-facility-fact-schema",
    )
    operational = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "water_treatment_plant", "operational"),
    )
    assert operational is not None
    operational.truth_value = True
    operational.visibility = Visibility.HIDDEN
    session.flush()

    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="water_treatment_plant",
        fact_key="operational",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    resolution = GenericGoalResolver(
        provider=provider,
        db=session,
        scope=scope,
    ).resolve(
        "Restore the water treatment plant to operation",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    fact = next(
        item
        for item in provider.requests[0].ontology["world"]["facts"]
        if item["node_key"] == "water_treatment_plant" and item["fact_key"] == "operational"
    )
    assert fact["value_type"] == "BOOLEAN"
    assert "initial_value" not in fact
    assert "truth_value" not in fact
    assert "current_value" not in fact
    assert all(
        item["fact_key"] != "repair_profile"
        for item in provider.requests[0].ontology["world"]["facts"]
    )


def test_hidden_facility_fact_goal_waits_for_public_knowledge(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-hidden-facility-completion",
    )
    operational = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "water_treatment_plant", "operational"),
    )
    assert operational is not None
    operational.truth_value = True
    operational.visibility = Visibility.HIDDEN
    session.flush()

    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="water_treatment_plant",
        fact_key="operational",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    agent = GenericAgentService(session, scope, provider=provider)
    resolution = agent.goal_resolver.resolve(
        "Restore the water treatment plant to operation",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )
    task = agent.create_task(
        runtime.session,
        "Restore the water treatment plant to operation",
        resolved_goal=resolution,
        initialize_plan=False,
    )

    assert task.status == AgentTaskStatus.ACTIVE
    assert agent.evaluate(task).completed is False
    assert agent.evaluate(task).authoritative_completed is True

    operational.visibility = Visibility.KNOWN
    session.flush()

    assert agent.evaluate(task).completed is True
    assert agent.execute_next(task) is None
    assert task.status == AgentTaskStatus.SUCCEEDED


def test_dynamic_hidden_internal_fact_is_rejected_without_validation_leakage(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-hidden-validation-boundary",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="southeast_fuel_emergency_power_plant",
        fact_key="sustained_requirements_discovered",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    resolution = GenericGoalResolver(
        provider=provider,
        db=session,
        scope=scope,
    ).resolve(
        "Make the sustained generation requirements discovered",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["rejection_code"] == (
        "FORMAL_GOAL_DYNAMIC_FACT_NOT_PUBLIC"
    )
    assert "sustained_requirements_discovered" not in str(provider.requests[0].ontology)
    assert "sustained_requirements_discovered" not in str(resolution.provider_observation)


def test_known_transport_exposes_passability_schema_without_hidden_truth(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-passability-ontology",
    )
    passability = session.scalar(
        select(GameInstanceFactState).where(
            GameInstanceFactState.game_instance_id == runtime.instance.id,
            GameInstanceFactState.node_key == "west_freight_corridor",
            GameInstanceFactState.fact_key == "passable",
        )
    )
    assert passability is not None
    passability.truth_value = False
    passability.visibility = Visibility.HIDDEN
    session.flush()

    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    resolution = GenericGoalResolver(
        provider=provider,
        db=session,
        scope=scope,
    ).resolve(
        "\u4fee\u901a\u897f\u90e8\u8d27\u8fd0\u901a\u9053",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    fact = next(
        item
        for item in provider.requests[0].ontology["world"]["facts"]
        if item["node_key"] == "west_freight_corridor" and item["fact_key"] == "passable"
    )
    assert fact["value_type"] == "BOOLEAN"
    assert "initial_value" not in fact
    assert "truth_value" not in fact
    assert "current_value" not in fact
    assert all(
        item["fact_key"] != "sustained_requirements_discovered"
        for item in provider.requests[0].ontology["world"]["facts"]
    )


def test_dynamic_passability_goal_keeps_planner_fact_unknown(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-passability-planner-unknown",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    agent = GenericAgentService(session, scope, provider=provider)
    resolution = agent.goal_resolver.resolve(
        "\u4fee\u901a\u897f\u90e8\u8d27\u8fd0\u901a\u9053",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )
    task = agent.create_task(
        runtime.session,
        "\u4fee\u901a\u897f\u90e8\u8d27\u8fd0\u901a\u9053",
        resolved_goal=resolution,
        initialize_plan=False,
    )
    contract = load_formal_goal_for_task(session, scope, task)
    planner_input = PlanningContextBuilder(session, scope).build_v2(
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        task=task,
        replan_reason=None,
        formal_goal=contract,
    )

    assert planner_input.known_world.facts.get("west_freight_corridor.passable") is None
    assert any(
        item.get("dimension") == "OBJECTIVE_FACT_KNOWLEDGE"
        and item.get("subject_key") == "west_freight_corridor"
        and item.get("fact_key") == "passable"
        and item.get("status") == "UNKNOWN"
        for item in planner_input.known_world.unknown_dependencies
    )


@pytest.mark.parametrize("truth_value", [True, False])
def test_dynamic_fact_submit_does_not_publish_hidden_truth_completion(
    session,
    truth_value: bool,
) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        f"dynamic-hidden-fact-completion-{truth_value}",
    )
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    assert fact is not None
    fact.truth_value = truth_value
    fact.visibility = Visibility.HIDDEN
    session.flush()

    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    orchestrator = PlayOrchestrator(
        session,
        GameInstanceId(runtime.instance.id),
        provider=provider,
    )

    submission = orchestrator.submit_goal(
        "修复中央城区和西部物流区中间那条路",
        idempotency_key=f"dynamic-hidden-fact-{truth_value}",
    )

    assert submission.task is not None
    assert submission.task.status == AgentTaskStatus.ACTIVE
    evaluation = GenericAgentService(
        session,
        GameInstanceService(session).load(GameInstanceId(runtime.instance.id)),
        provider=provider,
    ).evaluate(submission.task)
    assert evaluation.completed is False
    assert evaluation.authoritative_completed is truth_value


def test_dynamic_fact_completion_follows_public_knowledge_reveal(session) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "dynamic-public-fact-completion",
    )
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    assert fact is not None
    fact.truth_value = True
    fact.visibility = Visibility.HIDDEN
    session.flush()

    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "修复中央城区和西部物流区中间那条路",
        initialize_plan=False,
    )
    assert task.status == AgentTaskStatus.ACTIVE
    assert agent.evaluate(task).completed is False

    fact.visibility = Visibility.KNOWN
    session.flush()

    assert agent.evaluate(task).completed is True
    assert agent.execute_next(task) is None
    assert task.status == AgentTaskStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("quantity", "authoritative_completed"),
    [(120, True), (80, False)],
)
def test_dynamic_resource_unknown_quantity_does_not_publish_truth_completion(
    session,
    quantity: int,
    authoritative_completed: bool,
) -> None:
    runtime = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        f"dynamic-hidden-resource-completion-{quantity}",
    )
    pool = session.scalar(
        select(GameInstanceResourceState).where(
            GameInstanceResourceState.game_instance_id == runtime.instance.id,
            GameInstanceResourceState.pool_key == "south_emergency_fuel",
        )
    )
    assert pool is not None
    pool.value = quantity
    pool.availability = ResourcePoolAvailability.AVAILABLE
    pool.visibility = ResourcePoolVisibility.HIDDEN
    session.flush()

    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
        region_key="south_waterfront_district",
        resource_key="emergency_fuel",
        minimum=100,
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "Maintain an emergency fuel reserve in \u5357\u90e8\u6ee8\u6c34\u533a",
        initialize_plan=False,
    )

    assert task.status == AgentTaskStatus.ACTIVE
    evaluation = agent.evaluate(task)
    assert evaluation.completed is False
    assert evaluation.authoritative_completed is authoritative_completed


def test_provider_dynamic_contract_rejects_backend_owned_fields() -> None:
    with pytest.raises(ValueError):
        DynamicGoalInterpretation.model_validate(
            {
                "status": "RESOLVED",
                "requirements": [
                    {
                        "kind": "FACT",
                        "node_key": "patient_one",
                        "fact_key": "stable",
                        "accepted_values": [True],
                        "knowledge_gate": {"node_key": "patient_one", "fact_key": "stable"},
                    }
                ],
            }
        )


def test_stage_one_derived_reference_drives_only_that_focused_ontology() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.DERIVED_STATE,
        derived_key="north_basic_engineering_support",
        accepted_values=("AVAILABLE",),
    )
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(
                    ref_type="DERIVED_STATE",
                    key="north_basic_engineering_support",
                ),
            )
        ),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Make the northern engineering capability available",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    request = provider.requests[0]
    assert request.grounded_candidate_refs == (
        DynamicGoalCandidateReference(
            ref_type="DERIVED_STATE",
            key="north_basic_engineering_support",
        ),
    )
    assert [item["key"] for item in request.ontology["world"]["derived_states"]] == [
        "north_basic_engineering_support"
    ]
    assert request.ontology["world"]["resources"] == []
    assert request.ontology["world"]["facts"] == []


def test_stage_one_resource_and_region_references_drive_resource_ontology() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
        region_key="east_residential_district",
        resource_key="emergency_relief_supplies",
        minimum=20,
    )
    refs = (
        DynamicGoalCandidateReference(ref_type="REGION", key="east_residential_district"),
        DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_relief_supplies"),
    )
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(candidate_refs=refs),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Prepare at least twenty relief supplies in the east",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    ontology = provider.requests[0].ontology
    assert [item["key"] for item in ontology["world"]["resources"]] == ["emergency_relief_supplies"]
    assert ontology["world"]["derived_states"] == []
    assert ontology["world"]["regions"] == ["east_residential_district"]
    assert ontology["goal_language"]["requirement_kinds"] == ["RESOURCE_AT_LEAST"]


@pytest.mark.parametrize(
    ("grounding_refs", "candidate", "expected_code"),
    [
        (
            (DynamicGoalCandidateReference(ref_type="NODE", key="west_freight_corridor"),),
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.FACT,
                node_key="central_telecom_hub",
                fact_key="operational",
                accepted_values=(True,),
            ),
            "FORMAL_GOAL_DYNAMIC_GROUNDING_MISMATCH",
        ),
        (
            (
                DynamicGoalCandidateReference(ref_type="REGION", key="east_residential_district"),
                DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_relief_supplies"),
            ),
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
                region_key="east_residential_district",
                resource_key="general_engineering_parts",
                minimum=1,
            ),
            "FORMAL_GOAL_DYNAMIC_GROUNDING_MISMATCH",
        ),
        (
            (
                DynamicGoalCandidateReference(
                    ref_type="DERIVED_STATE", key="north_basic_engineering_support"
                ),
            ),
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.DERIVED_STATE,
                derived_key="east_emergency_power_network",
                accepted_values=("AVAILABLE",),
            ),
            "FORMAL_GOAL_DYNAMIC_GROUNDING_MISMATCH",
        ),
    ],
)
def test_dynamic_interpretation_cannot_escape_stage_one_projection(
    grounding_refs: tuple[DynamicGoalCandidateReference, ...],
    candidate: AdHocGoalRequirementCandidateV1,
    expected_code: str,
) -> None:
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(candidate_refs=grounding_refs),
        (DynamicGoalInterpretation(requirements=(candidate,)),) * 4,
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "A bounded public goal",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["rejection_code"] == expected_code


def test_dynamic_grounding_contract_has_no_requirement_or_value_fields() -> None:
    with pytest.raises(ValueError):
        DynamicGoalEntityGrounding.model_validate(
            {
                "status": "RESOLVED",
                "candidate_refs": [{"ref_type": "NODE", "key": "west_freight_corridor"}],
                "requirements": [{"kind": "FACT"}],
            }
        )
