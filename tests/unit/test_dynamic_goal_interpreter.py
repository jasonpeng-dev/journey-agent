from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agent.generic import GenericAgentService, GenericGoalResolver
from app.agent.provider import (
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    GoalSelection,
    GoalSelectionRequest,
    PlanProposal,
    PlanRequest,
)
from app.domain.formal_goal import (
    AdHocGoalRequirementCandidateV1,
    FormalGoalSourceKind,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveRequirementKind
from app.infrastructure.db.models import Player
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0, require_builtin_v2_version
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

    @property
    def model_name(self) -> str:
        return "dynamic-test-provider"

    def select_objectives(self, _request: GoalSelectionRequest) -> GoalSelection:
        return GoalSelection(status="UNSUPPORTED")

    def interpret_dynamic_goal(
        self,
        request: DynamicGoalInterpretationRequest,
    ) -> object:
        self.requests.append(request)
        return self.interpretation

    def propose_plan(self, _request: PlanRequest) -> PlanProposal:
        raise AssertionError("Dynamic Goal creation test must not start planning")


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


def test_dynamic_interpreter_is_strict_and_knowledge_safe() -> None:
    provider = _DynamicProvider(
        DynamicGoalInterpretation(requirements=(_stable_candidate(),))
    )
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


def test_dynamic_interpreter_malformed_response_fails_closed() -> None:
    provider = _DynamicProvider({"status": "RESOLVED", "requirements": []})

    with pytest.raises(ValueError, match="invalid Dynamic Goal interpretation"):
        GenericGoalResolver(provider=provider).resolve("unstated goal", GENERIC_TEST)


def test_dynamic_goal_freezes_without_legacy_objective_scope(session) -> None:
    runtime = _runtime(session)
    provider = _DynamicProvider(
        DynamicGoalInterpretation(requirements=(_stable_candidate(),))
    )
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
    provider = _DynamicProvider(
        DynamicGoalInterpretation(requirements=(_stable_candidate(),))
    )
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
    assert projected.roadmap.stages[0].objective_key is None


def test_play_dynamic_goal_idempotency_reuses_frozen_contract(session) -> None:
    runtime = _runtime(session)
    provider = _DynamicProvider(
        DynamicGoalInterpretation(requirements=(_stable_candidate(),))
    )
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
    assert GenericAgentService(session, scope, provider=provider).evaluate(task).completed is False


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
    assert all(
        fact["fact_key"] != "operational"
        or fact["node_key"] != "south_fuel_terminal"
        for fact in ontology["world"]["facts"]
    )
    assert "initial_value" not in str(ontology)


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
