from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError, GenericAgentService
from app.agent.planning_context import PlanningContextBuilder
from app.domain.enums import RelationVisibility
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import (
    GameInstanceNodeState,
    GameInstanceRelationKnowledge,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import scenario_content_hash
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleService
from app.services.generic_game import GenericGameService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.player_projection import PlayerProjectionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.unit.test_generic_gameplay_capabilities import _runtime as capability_runtime
from tests.unit.test_scenario_definition_v2 import _medical_scenario_document


def _hidden_medical_document(*, reveal_on_treatment: bool = False) -> dict[str, Any]:
    document = deepcopy(_medical_scenario_document())
    relation = document["world"]["relations"][0]
    relation["key"] = "triage_contains_patient"
    relation["initial_visibility"] = "HIDDEN"
    if reveal_on_treatment:
        document["rules"][0]["effects"].insert(
            0,
            {
                "kind": "SET_RELATION_VISIBILITY",
                "relation_key": "triage_contains_patient",
                "visibility": "VISIBLE",
            },
        )
    return document


def _publish_runtime(
    session: Session,
    document: dict[str, Any],
    creation_key: str,
) -> tuple[ScenarioDefinitionV2, object, object]:
    definition = ScenarioDefinitionV2.model_validate(document)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = GameLifecycleService(session).platform_player()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=creation_key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return definition, runtime, scope


def test_legacy_relation_defaults_visible_without_hash_or_serialization_change() -> None:
    source = _medical_scenario_document()
    definition = ScenarioDefinitionV2.model_validate(source)

    assert definition.world.relations[0].initial_visibility == RelationVisibility.VISIBLE
    assert definition.world.relations[0].model_dump(mode="json") == {
        "source_node_key": "triage_room",
        "relation_type_key": "contains",
        "target_node_key": "patient_one",
    }
    assert scenario_content_hash(source) == scenario_content_hash(
        definition.model_dump(mode="json")
    )


def test_hidden_relation_is_absent_from_player_and_planning_context_until_revealed(
    session: Session,
) -> None:
    definition, runtime, scope = _publish_runtime(
        session,
        _hidden_medical_document(reveal_on_treatment=True),
        "hidden-relation-projection",
    )
    projection = SharedKnowledgeProjection(session, scope, definition)
    assert projection.known_relations() == ()

    agent_task = GenericAgentService(session, scope).create_task(
        runtime.session,
        "stabilize the patient",
        initialize_plan=False,
    )
    context = PlanningContextBuilder(session, scope).build(
        definition,
        (definition.objectives[0],),
        task=agent_task,
        replan_reason=None,
    )
    assert context.current_knowledge["relations"] == []
    player_state = PlayerProjectionService(session).game_state(scope.game_instance_id)
    assert player_state.known_relations == []

    result = GenericGameService(session, scope).execute(
        actor_key="doctor_lee",
        action_key="treat_patient",
        target_node_key="patient_one",
        parameters={"dosage": 1},
    )
    assert result.outcome.failure is None
    assert any(item.kind == "RELATION_REVEALED" for item in result.knowledge_changes)
    assert len(projection.known_relations()) == 1
    player_state = PlayerProjectionService(session).game_state(scope.game_instance_id)
    assert len(player_state.known_relations) == 1

    context = PlanningContextBuilder(session, scope).build(
        definition,
        (definition.objectives[0],),
        task=agent_task,
        replan_reason=None,
    )
    assert len(context.current_knowledge["relations"]) == 1

    patient_state = session.get(GameInstanceNodeState, (runtime.instance.id, "patient_one"))
    assert patient_state is not None
    patient_state.visibility = "HIDDEN"
    session.flush()
    assert projection.known_relations() == ()


def test_hidden_power_relation_is_not_known_to_validator_or_runtime(
    session: Session,
) -> None:
    agent, runtime, definition = capability_runtime(session, "hidden-power-relation")
    relation = next(
        item
        for item in definition.world.relations
        if item.source_node_key == "central_hospital"
        and item.target_node_key == "central_telecom_hub"
        and item.relation_type_key == "supplies_power_to"
    )
    row = session.get(
        GameInstanceRelationKnowledge,
        (
            runtime.instance.id,
            f"{relation.source_node_key}__{relation.relation_type_key}__{relation.target_node_key}",
        ),
    )
    assert row is not None
    row.visibility = RelationVisibility.HIDDEN
    session.flush()

    projection = SharedKnowledgeProjection(session, agent.scope, definition)
    assert not any(
        item["source_node_key"] == "central_hospital"
        and item["target_node_key"] == "central_telecom_hub"
        for item in projection.known_relations()
    )
    action = next(item for item in definition.actions if item.key == "supply_power")
    with pytest.raises(GenericAgentError) as error:
        agent._validate_projected_supply_power(
            definition,
            action,
            "central_telecom_hub",
            {"source_key": "central_hospital"},
            agent._known_fact_projection(),
            agent._known_node_keys(),
            agent._known_relation_keys(definition),
        )
    assert error.value.code == "SUPPLY_POWER_RELATION_UNKNOWN"

    result = GenericGameService(session, agent.scope).execute(
        actor_key="electrical_team_beta",
        action_key="supply_power",
        target_node_key="central_telecom_hub",
        parameters={"source_key": "central_hospital"},
    )
    assert result.outcome.failure is not None
    assert result.outcome.failure.code == "SUPPLY_POWER_RELATION_UNKNOWN"

    row.visibility = RelationVisibility.VISIBLE
    session.flush()
    agent._validate_projected_supply_power(
        definition,
        action,
        "central_telecom_hub",
        {"source_key": "central_hospital"},
        agent._known_fact_projection(),
        agent._known_node_keys(),
        agent._known_relation_keys(definition),
    )
    result = GenericGameService(session, agent.scope).execute(
        actor_key="electrical_team_beta",
        action_key="supply_power",
        target_node_key="central_telecom_hub",
        parameters={"source_key": "central_hospital"},
    )
    assert result.outcome.failure is None
