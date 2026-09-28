from sqlalchemy.orm import Session

from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import ScenarioPresentationProfile, ScenarioVersion
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0, require_builtin_v2_version
from app.services.game_lifecycle import GameLifecycleService
from app.services.player_projection import PlayerProjectionService
from app.services.runtime_initialization import RuntimeInitializationService


def test_player_projection_exposes_resolved_profile_without_expanding_knowledge(
    session: Session,
) -> None:
    version = require_builtin_v2_version(session, LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
    runtime = RuntimeInitializationService(session).create(
        player_id=GameLifecycleService(session).platform_player().id,
        scenario_version_id=version.id,
        creation_key="presentation-projection-contract",
    )
    version = session.get(ScenarioVersion, runtime.instance.scenario_version_id)
    assert version is not None
    session.add(
        ScenarioPresentationProfile(
            scenario_id=version.scenario_id,
            revision=7,
            profile_document={
                "schema_version": 1,
                "template": "compact",
                "world_entities": {"knowledge_level": "A"},
                "family_overrides": [{"node_family": "FACILITY", "default_open": "FULL"}],
            },
        )
    )
    session.commit()

    state = PlayerProjectionService(session).game_state(GameInstanceId(runtime.instance.id))
    assert state.presentation is not None
    assert state.presentation.revision == 7
    assert state.presentation.template == "compact"
    assert state.presentation.knowledge_level == "A"
    facility = next(node for node in state.visible_nodes if node.node_family == "FACILITY")
    assert facility.presentation is not None
    assert facility.presentation.default_open == "FULL"
    assert facility.presentation.knowledge_level == "A"
    assert all(
        "truth" not in field and "hidden" not in field for field in state.presentation.model_dump()
    )
