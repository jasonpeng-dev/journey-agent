from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.agent.generic import (
    GenericAgentService,
    GenericGoalResolver,
)
from app.agent.planning_context import PlanningContextBuilder
from app.agent.provider import (
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    GenericProviderError,
    GoalSelection,
    GoalSelectionRequest,
    PlanProposal,
    PlanRequest,
)
from app.domain.enums import AgentTaskStatus, ResourcePoolAvailability, ResourcePoolVisibility
from app.domain.formal_goal import (
    AdHocGoalRequirementCandidateV1,
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
from tests.scenario_fixtures import GENERIC_TEST


@dataclass
class _DynamicProvider:
    interpretation: object

    def __post_init__(self) -> None:
        self.requests: list[DynamicGoalInterpretationRequest] = []
        self.selection_requests: list[GoalSelectionRequest] = []

    @property
    def model_name(self) -> str:
        return "dynamic-test-provider"

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        self.selection_requests.append(request)
        return GoalSelection(objective_keys=("establish_citywide_sustained_emergency_support",))

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


def test_exact_public_entity_uses_focused_ontology_and_one_recovery() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="west_freight_corridor",
        fact_key="passable",
        accepted_values=(True,),
    )
    provider = _GroundingProvider(
        DynamicGoalEntityGrounding(status="UNSUPPORTED"),
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
    assert provider.grounding_requests == []
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
        "DETERMINISTIC_ENTITY_GROUNDING"
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
    assert provider.requests[0].ontology["grounding"]["source"] == ("DETERMINISTIC_PUBLIC_TOPOLOGY")


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
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u4e2d\u592e\u57ce\u533a\u548c\u4e1c\u90e8\u5c45\u4f4f\u533a\u4e2d\u95f4\u90a3\u6761\u8def",
        definition,
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert set(resolution.candidate_keys) == {
        "central_river_tunnel",
        "central_river_tunnel_backup",
    }
    assert provider.requests == []


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
    assert len(provider.grounding_requests) == 3
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
            DynamicGoalEntityGrounding(status="UNSUPPORTED"),
            DynamicGoalEntityGrounding(candidate_keys=("west_freight_corridor",)),
        ),
        (DynamicGoalInterpretation(requirements=(candidate,)),),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u897f\u8fb9\u90a3\u6761\u8d27\u8fd0\u8d70\u9053",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.grounding_requests) == 3
    assert len(provider.requests) == 1
    assert provider.requests[0].grounded_entity_keys == ("west_freight_corridor",)
    assert resolution.provider_observation is not None
    assert [item["result"] for item in resolution.provider_observation["attempts"]] == [
        "MODEL_PROVIDER_RESPONSE_INVALID",
        "MODEL_UNSUPPORTED",
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


def test_interpretation_retries_three_times_without_regrounding() -> None:
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
            {"status": "RESOLVED", "requirements": []},
            DynamicGoalInterpretation(requirements=(candidate,)),
        ),
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "\u4fee\u590d\u897f\u90e8\u8d27\u8fd0\u8d70\u5eca",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert provider.grounding_requests == []
    assert len(provider.requests) == 3
    assert [request.recovery_attempt for request in provider.requests] == [0, 1, 1]
    assert provider.requests[0].ontology == provider.requests[1].ontology
    assert provider.requests[1].ontology == provider.requests[2].ontology
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["attempt_count"] == 3
    assert [item["result"] for item in resolution.provider_observation["attempts"]] == [
        "MODEL_UNSUPPORTED",
        "MODEL_PROVIDER_RESPONSE_INVALID",
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
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))

    resolution = GenericGoalResolver(provider=provider).resolve(
        objective.name,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "RESOLVED"
    assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["stage"] == "DERIVED_GOAL_CATALOG"
    assert resolution.objective_keys == ()
    assert len(resolution.dynamic_requirements) == 1
    assert resolution.dynamic_requirements[0].kind == ObjectiveRequirementKind.DERIVED_STATE
    assert resolution.dynamic_requirements[0].derived_key == (
        "citywide_sustained_emergency_support"
    )
    assert provider.selection_requests == []
    assert provider.requests == []


def test_canonical_task6_goal_and_alias_keep_hidden_semantics(
    session,
) -> None:
    objective = next(
        item
        for item in LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.objectives
        if item.key == "establish_sustained_emergency_generation"
    )
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))
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
    assert provider.requests == []


def test_unsupported_dynamic_goal_does_not_fallback_to_predefined() -> None:
    provider = _DynamicProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))

    resolution = GenericGoalResolver(provider=provider).resolve(
        "Invent an unsupported ontology requirement",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()
    assert provider.selection_requests == []
    assert len(provider.requests) == 1


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
        "requirement_kinds": ["FACT", "RESOURCE_AT_LEAST"],
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
    provider = _DynamicProvider({"status": "RESOLVED", "requirements": []})

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
    assert len(provider.requests) == 1
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
    operational = next(
        item
        for item in ontology["world"]["facts"]
        if item["node_key"] == "central_telecom_hub" and item["fact_key"] == "operational"
    )
    assert operational["goal_aliases"] == [
        "恢复中央通信能力",
        "恢复中央通信核心",
        "Restore central communications",
    ]
    assert operational["goal_target_values"] == [True]


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
