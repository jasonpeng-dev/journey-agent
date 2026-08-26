from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.agent.planning_context import PlanningContextBuilder
from app.domain.enums import RelationVisibility
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import (
    GameInstanceNodeState,
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
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _hidden_relation_document(*, reveal_on_treatment: bool = False) -> dict[str, Any]:
    document = deepcopy(_contract_scenario_document())
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
    source = _contract_scenario_document()
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
        _hidden_relation_document(reveal_on_treatment=True),
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
