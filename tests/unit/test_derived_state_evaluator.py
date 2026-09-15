from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import ResourcePoolVisibility
from app.domain.formal_goal import compile_ad_hoc_dynamic_goal
from app.domain.runtime_scope import GameInstanceId, RuntimeScope
from app.domain.scenario_v2 import ObjectiveRequirementKind, ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentTask,
    GameInstanceFactState,
    GameInstanceResourceState,
    Player,
    WorldOperation,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.versions import ScenarioVersionRepository
from app.services.derived_state import evaluate_derived_states
from app.services.formal_goal import FormalGoalCompletionEvaluator, load_formal_goal_for_task
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import InitializedRuntime, RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.dynamic_goal_helpers import dynamic_candidate as AdHocGoalRequirementCandidateV1
from tests.unit.test_derived_state_domain import _resource_derived_document


def _runtime(
    session: Session,
    document: dict[str, Any] | None = None,
) -> tuple[ScenarioDefinitionV2, RuntimeScope, InitializedRuntime]:
    document = document or _resource_derived_document()
    document["initialization"]["resource_initial_states"] = [
        {
            "resource_key": "medicine",
            "scope_node_key": "triage_room",
            "value": 10,
            "reserved_value": 0,
        }
    ]
    definition = ScenarioDefinitionV2.model_validate(document)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="derived-state-evaluator")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="derived-state-evaluator",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return definition, scope, runtime


def _gated_derived_document() -> dict[str, Any]:
    document = _resource_derived_document()
    patient = next(node for node in document["world"]["nodes"] if node["key"] == "patient_one")
    assert isinstance(patient, dict)
    patient["facts"] = [
        *patient["facts"],
        {
            "key": "diagnosis",
            "name": "Diagnosis",
            "value_type": "BOOLEAN",
            "initial_value": False,
            "initial_visibility": "KNOWN",
        },
    ]
    document["derived_states"].append(
        {
            "key": "gated_patient_ready",
            "name": "Gated patient ready",
            "description": "The gated patient state is ready.",
            "value_type": "BOOLEAN",
            "available_value": True,
            "unavailable_value": False,
            "dependencies": [
                {
                    "kind": "FACT",
                    "node_key": "patient_one",
                    "fact_key": "diagnosis",
                    "accepted_values": [True],
                    "knowledge_gate": {
                        "node_key": "patient_one",
                        "fact_key": "stable",
                        "accepted_values": [True],
                    },
                }
            ],
        }
    )
    return document


def _set_fact(
    session: Session,
    instance_id: UUID,
    node_key: str,
    fact_key: str,
    value: object,
    *,
    visibility: Visibility | None = None,
) -> None:
    row = session.get(GameInstanceFactState, (instance_id, node_key, fact_key))
    assert row is not None
    row.truth_value = value
    if visibility is not None:
        row.visibility = visibility


def test_derived_truth_knowledge_and_nested_state_follow_three_valued_matrix(
    session: Session,
) -> None:
    definition, scope, runtime = _runtime(session)
    instance = runtime.instance

    initial = evaluate_derived_states(session, scope, definition)
    assert initial.truth_value("patient_ready") is False
    assert initial.knowledge_value("patient_ready") is False
    assert initial.truth_value("clinic_ready") == "BLOCKED"
    assert initial.knowledge_value("clinic_ready") == "BLOCKED"

    _set_fact(
        session,
        instance.id,
        "patient_one",
        "stable",
        True,
        visibility=Visibility.HIDDEN,
    )
    session.flush()
    hidden_fact = evaluate_derived_states(session, scope, definition)
    assert hidden_fact.truth_value("patient_ready") is True
    assert hidden_fact.knowledge_value("patient_ready") is None
    assert hidden_fact.truth_value("clinic_ready") == "READY"
    assert hidden_fact.knowledge_value("clinic_ready") is None

    _set_fact(
        session,
        instance.id,
        "patient_one",
        "stable",
        True,
        visibility=Visibility.KNOWN,
    )
    session.flush()
    known = evaluate_derived_states(session, scope, definition)
    assert known.truth_value("clinic_ready") == "READY"
    assert known.knowledge_value("clinic_ready") == "READY"

    resource = session.get(
        GameInstanceResourceState,
        (instance.id, "medicine@triage_room"),
    )
    assert resource is not None
    resource.value = 0
    session.flush()
    low_resource = evaluate_derived_states(session, scope, definition)
    assert low_resource.truth_value("patient_ready") is False
    assert low_resource.truth_value("clinic_ready") == "BLOCKED"
    assert low_resource.knowledge_value("clinic_ready") == "BLOCKED"


def test_derived_knowledge_gate_hides_dependency_until_gate_is_known_and_accepted(
    session: Session,
) -> None:
    definition, scope, runtime = _runtime(session, _gated_derived_document())

    closed = evaluate_derived_states(session, scope, definition)
    assert closed.truth_value("gated_patient_ready") is False
    assert closed.knowledge_value("gated_patient_ready") is None

    _set_fact(
        session,
        runtime.instance.id,
        "patient_one",
        "diagnosis",
        True,
        visibility=Visibility.KNOWN,
    )
    session.flush()
    gate_closed = evaluate_derived_states(session, scope, definition)
    assert gate_closed.truth_value("gated_patient_ready") is True
    assert gate_closed.knowledge_value("gated_patient_ready") is None

    _set_fact(
        session,
        runtime.instance.id,
        "patient_one",
        "stable",
        True,
        visibility=Visibility.KNOWN,
    )
    session.flush()
    accepted = evaluate_derived_states(session, scope, definition)
    assert accepted.truth_value("gated_patient_ready") is True
    assert accepted.knowledge_value("gated_patient_ready") is True

    _set_fact(
        session,
        runtime.instance.id,
        "patient_one",
        "diagnosis",
        False,
        visibility=Visibility.KNOWN,
    )
    session.flush()
    dependency_false = evaluate_derived_states(session, scope, definition)
    assert dependency_false.truth_value("gated_patient_ready") is False
    assert dependency_false.knowledge_value("gated_patient_ready") is False

    _set_fact(
        session,
        runtime.instance.id,
        "patient_one",
        "diagnosis",
        True,
        visibility=Visibility.HIDDEN,
    )
    hidden_dependency = evaluate_derived_states(session, scope, definition)
    assert hidden_dependency.truth_value("gated_patient_ready") is True
    assert hidden_dependency.knowledge_value("gated_patient_ready") is None


def test_hidden_resource_value_is_truth_only_and_knowledge_stays_unknown(
    session: Session,
) -> None:
    definition, scope, runtime = _runtime(session)
    instance = runtime.instance
    _set_fact(
        session,
        instance.id,
        "patient_one",
        "stable",
        True,
        visibility=Visibility.KNOWN,
    )
    resource = session.get(
        GameInstanceResourceState,
        (instance.id, "medicine@triage_room"),
    )
    assert resource is not None
    resource.visibility = ResourcePoolVisibility.HIDDEN
    resource.value = 10
    session.flush()

    evaluation = evaluate_derived_states(session, scope, definition)

    assert evaluation.truth_value("clinic_ready") == "READY"
    assert evaluation.knowledge_value("clinic_ready") is None
    assert evaluation.values["clinic_ready"].knowledge_status == "UNKNOWN"


def test_derived_evaluation_is_read_only_and_recomputes_after_base_mutation(
    session: Session,
) -> None:
    definition, scope, runtime = _runtime(session)
    instance = runtime.instance
    before_revision = instance.runtime_revision
    before_operations = session.scalar(select(func.count()).select_from(WorldOperation))

    evaluate_derived_states(session, scope, definition)

    session.expire(instance)
    assert instance.runtime_revision == before_revision
    assert session.scalar(select(func.count()).select_from(WorldOperation)) == before_operations

    _set_fact(
        session,
        instance.id,
        "patient_one",
        "stable",
        True,
        visibility=Visibility.KNOWN,
    )
    session.flush()
    recomputed = evaluate_derived_states(session, scope, definition)
    assert recomputed.truth_value("patient_ready") is True
    assert recomputed.knowledge_value("patient_ready") is True


def test_derived_formal_completion_separates_truth_from_player_visibility(
    session: Session,
) -> None:
    definition, scope, runtime = _runtime(session)
    snapshot = ScenarioVersionRepository(session).load(scope.scenario_version_id)
    contract = compile_ad_hoc_dynamic_goal(
        snapshot,
        (
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.DERIVED_STATE,
                derived_key="clinic_ready",
                accepted_values=("READY",),
            ),
        ),
    )
    _set_fact(
        session,
        runtime.instance.id,
        "patient_one",
        "stable",
        True,
        visibility=Visibility.HIDDEN,
    )
    session.flush()

    hidden = FormalGoalCompletionEvaluator(session, scope).evaluate(
        contract,
        definition=definition,
    )
    assert hidden.completed is True
    assert hidden.player_visible_completed is False
    assert hidden.requirements[0].value == "READY"
    assert hidden.requirements[0].satisfied is True
    assert hidden.requirements[0].player_visible_satisfied is False

    _set_fact(
        session,
        runtime.instance.id,
        "patient_one",
        "stable",
        True,
        visibility=Visibility.KNOWN,
    )
    session.flush()
    visible = FormalGoalCompletionEvaluator(session, scope).evaluate(
        contract,
        definition=definition,
    )
    assert visible.completed is True
    assert visible.player_visible_completed is True


def test_derived_formal_contract_round_trips_through_task_persistence(
    session: Session,
) -> None:
    definition, scope, runtime = _runtime(session)
    snapshot = ScenarioVersionRepository(session).load(scope.scenario_version_id)
    contract = compile_ad_hoc_dynamic_goal(
        snapshot,
        (
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.DERIVED_STATE,
                derived_key="patient_ready",
                accepted_values=(True,),
            ),
        ),
    )
    task = AgentTask(
        player_id=runtime.instance.player_id,
        game_instance_id=runtime.instance.id,
        owner_actor_key=runtime.session.actor_key,
        origin_session_id=runtime.session.id,
        last_session_id=runtime.session.id,
        goal_description="dynamic derived goal",
        scenario_key=definition.metadata.key,
        objective_resolution_status="CONFIRMED",
        objective_scope_hash=contract.content_hash,
        objective_frozen_at=datetime.now(UTC),
        objective_freeze_source="TEST",
        formal_goal_contract_schema_version=contract.schema_version,
        formal_goal_source_kind=contract.source_kind.value,
        formal_goal_contract_json=contract.model_dump(mode="json"),
        formal_goal_contract_hash=contract.content_hash,
        formal_goal_scenario_version_id=snapshot.id,
        formal_goal_scenario_content_hash=snapshot.content_hash,
        formal_goal_compiler_version=contract.compiler_version,
    )
    session.add(task)
    session.flush()

    loaded = load_formal_goal_for_task(session, scope, task)

    assert loaded == contract
    assert loaded.completion_requirements[0].requirement.kind == (
        ObjectiveRequirementKind.DERIVED_STATE
    )
