from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.scenarios.documents import (
    SCENARIO_DOCUMENT_SCHEMA_VERSION,
    ScenarioDefinitionDocument,
)
from app.scenarios.persistence import (
    ScenarioDefinitionRepository,
    ScenarioPersistenceError,
)
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.services.seed import seed_scenario_definitions


def test_starfire_definition_document_round_trips_all_content() -> None:
    document = ScenarioDefinitionDocument.from_domain(STARFIRE_SCENARIO_DEFINITION)
    restored = ScenarioDefinitionDocument.model_validate(
        document.model_dump(mode="json")
    ).to_domain()

    assert restored == STARFIRE_SCENARIO_DEFINITION
    assert document.schema_version == SCENARIO_DOCUMENT_SCHEMA_VERSION
    assert len(document.world.nodes) == 6
    assert len(document.world.interactions) == 6
    assert len(document.world.relations) == 7
    assert len(document.world.resources) == 4
    assert len(document.objective_catalog.definitions) == 5
    assert document.behavior_bundle.key == "starfire"
    assert document.behavior_bundle.version == "1"

    full = next(
        objective
        for objective in document.objective_catalog.definitions
        if objective.key == "FULL_NORTHERN_RECOVERY"
    )
    assert {item.fact_key for item in full.completion_requirements} == {
        "valley_security",
        "outpost_status",
        "trade_route_status",
    }
    assert full.prerequisites[0].requirements[0].fact_key == "village_support"
    assert set(full.subsumes) == {
        "SECURE_NORTHERN_VALLEY",
        "RESTORE_STARFIRE_OUTPOST",
        "OPEN_NORTHERN_TRADE_ROUTE",
    }


def test_repository_persists_and_reloads_complete_starfire_draft(session: Session) -> None:
    repository = ScenarioDefinitionRepository(session)

    scenario = repository.persist_initial_draft(STARFIRE_SCENARIO_DEFINITION)
    session.flush()
    session.expire_all()

    loaded = repository.load_draft(scenario.id)
    persisted = session.get(Scenario, scenario.id)
    draft = session.get(ScenarioDraft, scenario.id)

    assert loaded == STARFIRE_SCENARIO_DEFINITION
    assert persisted is not None
    assert persisted.key == "starfire_command"
    assert persisted.status == "DRAFT"
    assert draft is not None
    assert draft.revision == 1
    assert draft.validation_status == "UNVALIDATED"
    assert draft.definition_document["behavior_bundle"] == {
        "key": "starfire",
        "version": "1",
    }
    assert session.scalar(select(func.count()).select_from(ScenarioVersion)) == 0


def test_builtin_scenario_seed_is_idempotent(session: Session) -> None:
    seed_scenario_definitions(session)
    seed_scenario_definitions(session)

    assert session.scalar(select(func.count()).select_from(Scenario)) == 1
    assert session.scalar(select(func.count()).select_from(ScenarioDraft)) == 1


def test_repository_fails_closed_for_corrupt_persisted_document(session: Session) -> None:
    repository = ScenarioDefinitionRepository(session)
    scenario = repository.persist_initial_draft(STARFIRE_SCENARIO_DEFINITION)
    draft = session.get(ScenarioDraft, scenario.id)
    assert draft is not None
    corrupt = deepcopy(draft.definition_document)
    corrupt["world"]["nodes"][0]["node_type"] = "NOT_A_NODE_TYPE"
    draft.definition_document = corrupt
    session.flush()

    with pytest.raises(ScenarioPersistenceError) as caught:
        repository.load_draft(scenario.id)

    assert caught.value.code == "SCENARIO_DEFINITION_INVALID"


def test_initial_seed_rejects_same_key_with_different_definition(session: Session) -> None:
    repository = ScenarioDefinitionRepository(session)
    repository.persist_initial_draft(STARFIRE_SCENARIO_DEFINITION)
    changed = deepcopy(
        ScenarioDefinitionDocument.from_domain(STARFIRE_SCENARIO_DEFINITION).model_dump(mode="json")
    )
    changed["world"]["name"] = "Changed Starfire"
    conflicting = ScenarioDefinitionDocument.model_validate(changed).to_domain()

    with pytest.raises(ScenarioPersistenceError) as caught:
        repository.persist_initial_draft(conflicting)

    assert caught.value.code == "SCENARIO_DEFINITION_CONFLICT"
