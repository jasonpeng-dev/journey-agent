from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import GameInstanceActor, Player
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.runtime_recovery import RuntimeRecoveryService
from app.services.scenarios import ScenarioService
from tests.unit.test_scenario_definition_v2 import _medical_scenario_document


def test_v2_runtime_materializes_exact_version_actors_without_npc_seed(
    session: Session,
) -> None:
    definition = ScenarioDefinitionV2.model_validate(_medical_scenario_document())
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="v2-runtime-player")
    session.add(player)
    session.flush()

    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="medical-runtime",
    )

    assert runtime.instance.status == GameInstanceStatus.ACTIVE
    assert runtime.instance.current_node_key == "triage_room"
    assert runtime.session.actor_key == "doctor_lee"
    actors = session.scalars(
        select(GameInstanceActor).where(GameInstanceActor.game_instance_id == runtime.instance.id)
    ).all()
    assert len(actors) == 1
    actor = actors[0]
    assert actor.actor_key == "doctor_lee"
    assert actor.role_key == "clinician"
    assert actor.persona == "A careful emergency physician."
    assert actor.current_node_key == "triage_room"
    assert actor.allowed_action_keys == ["treat_patient"]
    assert set(actor.capabilities) == {"PLAN", "EXECUTE_ACTION", "INSPECT_STATE"}
    assert actor.is_primary


def test_v2_actor_runtime_is_idempotent_and_recoverable(session: Session) -> None:
    definition = ScenarioDefinitionV2.model_validate(_medical_scenario_document())
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="recover-v2-actor")
    session.add(player)
    session.flush()
    service = RuntimeInitializationService(session)
    created = service.create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="recover-actors",
    )

    replay = service.create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="recover-actors",
    )
    recovered = RuntimeRecoveryService(session).recover(GameInstanceId(created.instance.id))

    assert not replay.created
    assert replay.instance.id == created.instance.id
    assert [actor.actor_key for actor in recovered.actors] == ["doctor_lee"]
    assert recovered.sessions[0].actor_key == "doctor_lee"
