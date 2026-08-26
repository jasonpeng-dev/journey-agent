from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import (
    GameInstanceFactState,
    GameInstanceMemoryEvent,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_game import GenericGameError, GenericGameService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = _contract_scenario_document()
    effects = document["rules"][0]["effects"]
    effects.insert(
        1,
        {
            "kind": "ADJUST_RESOURCE",
            "resource_key": "medicine",
            "amount": {
                "source": "PARAMETER",
                "parameter_key": "dosage",
                "multiplier": -1,
            },
        },
    )
    effects.insert(
        2,
        {
            "kind": "WRITE_MEMORY_EVENT",
            "memory_key": "patient_stabilized",
            "memory_content": "Patient One is stable.",
        },
    )
    return ScenarioDefinitionV2.model_validate(document)


def _runtime(session: Session, creation_key: str) -> tuple[GenericGameService, object]:
    definition = _definition()
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name=creation_key)
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=creation_key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return GenericGameService(session, scope), runtime


def test_generic_service_atomically_applies_exact_version_outcome(session: Session) -> None:
    game, runtime = _runtime(session, "generic-apply")

    result = game.execute(
        actor_key="doctor_lee",
        action_key="treat_patient",
        target_node_key="patient_one",
        parameters={"dosage": 3},
    )

    assert result.outcome.outcome_code == "COMPLETED"
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "patient_one", "stable"),
    )
    resource = session.get(
        GameInstanceResourceState,
        (runtime.instance.id, "medicine"),
    )
    assert fact is not None and fact.truth_value is True
    assert resource is not None and resource.value == 7
    memory = session.scalar(
        select(GameInstanceMemoryEvent).where(
            GameInstanceMemoryEvent.game_instance_id == runtime.instance.id
        )
    )
    assert memory is not None
    assert memory.actor_key == "doctor_lee"
    assert memory.source_rule_key == "treatment_succeeds"
    assert result.runtime_revision == 2


def test_generic_service_rejects_complete_invalid_outcome_before_mutation(
    session: Session,
) -> None:
    game, runtime = _runtime(session, "generic-invalid")
    resource = session.get(
        GameInstanceResourceState,
        (runtime.instance.id, "medicine"),
    )
    assert resource is not None
    resource.value = 2
    session.flush()

    with pytest.raises(GenericGameError) as caught:
        game.execute(
            actor_key="doctor_lee",
            action_key="treat_patient",
            target_node_key="patient_one",
            parameters={"dosage": 3},
        )

    assert caught.value.code == "KNOWN_RESOURCE_INSUFFICIENT"
    assert caught.value.retryable is True
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "patient_one", "stable"),
    )
    assert fact is not None and fact.truth_value is False
    assert resource.value == 2


def test_generic_service_never_writes_another_instance(session: Session) -> None:
    game_a, runtime_a = _runtime(session, "generic-a")
    definition = _definition()
    scenario = ScenarioDefinitionRepository(session).find_scenario(definition.metadata.key)
    assert scenario is not None and scenario.current_published_version_id is not None
    player_b = Player(name="generic-b")
    session.add(player_b)
    session.flush()
    runtime_b = RuntimeInitializationService(session).create(
        player_id=player_b.id,
        scenario_version_id=scenario.current_published_version_id,
        creation_key="generic-b",
    )

    game_a.execute(
        actor_key="doctor_lee",
        action_key="treat_patient",
        target_node_key="patient_one",
        parameters={"dosage": 2},
    )

    fact_a = session.get(
        GameInstanceFactState,
        (runtime_a.instance.id, "patient_one", "stable"),
    )
    fact_b = session.get(
        GameInstanceFactState,
        (runtime_b.instance.id, "patient_one", "stable"),
    )
    assert fact_a is not None and fact_a.truth_value is True
    assert fact_b is not None and fact_b.truth_value is False
