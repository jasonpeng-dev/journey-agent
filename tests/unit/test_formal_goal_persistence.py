from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.agent.objective_scope import ObjectiveScope
from app.domain.formal_goal import (
    AdHocGoalRequirementCandidateV1,
    FormalGoalSourceKind,
    compile_ad_hoc_dynamic_goal,
    compile_predefined_formal_goal,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveRequirementKind
from app.infrastructure.db.models import AgentTask, FormalGoalImmutableError
from app.scenarios.builtin import require_builtin_v2_version
from app.scenarios.versions import ScenarioVersionRepository
from app.services.formal_goal import load_formal_goal_for_task
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from tests.scenario_fixtures import GENERIC_TEST


def _task(session: Session, *, formal: bool) -> tuple[AgentTask, object, object]:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    from app.infrastructure.db.models import Player

    player = Player(name="formal-goal-persistence")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=(
            "formal-goal-persistence-formal" if formal else "formal-goal-persistence-legacy"
        ),
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    snapshot = ScenarioVersionRepository(session).load(version.id)
    objective = snapshot.definition.objectives[0]
    objective_scope = ObjectiveScope.create((objective.key,), f"scenario-version:{version.id}")
    contract = compile_predefined_formal_goal(snapshot, (objective,))
    task = AgentTask(
        player_id=player.id,
        game_instance_id=runtime.instance.id,
        owner_actor_key=runtime.session.actor_key,
        origin_session_id=runtime.session.id,
        last_session_id=runtime.session.id,
        goal_description="stabilize the patient",
        scenario_key=GENERIC_TEST.metadata.key,
        objective_resolution_status="CONFIRMED",
        objective_scope_keys=list(objective_scope.objective_keys),
        objective_catalog_version=objective_scope.catalog_version,
        objective_scope_hash=objective_scope.content_hash,
        objective_frozen_at=datetime.now(UTC),
        objective_freeze_source="TEST",
        formal_goal_contract_schema_version=contract.schema_version if formal else None,
        formal_goal_source_kind=contract.source_kind.value if formal else None,
        formal_goal_contract_json=contract.model_dump(mode="json") if formal else None,
        formal_goal_contract_hash=contract.content_hash if formal else None,
        formal_goal_scenario_version_id=snapshot.id if formal else None,
        formal_goal_scenario_content_hash=snapshot.content_hash if formal else None,
        formal_goal_compiler_version=contract.compiler_version if formal else None,
    )
    session.add(task)
    session.flush()
    return task, runtime, scope


def test_legacy_predefined_task_compiles_transiently_without_write_back(session: Session) -> None:
    task, _runtime_value, scope = _task(session, formal=False)
    assert task.formal_goal_contract_json is None

    contract = load_formal_goal_for_task(session, scope, task)

    assert contract.source_kind == FormalGoalSourceKind.PREDEFINED
    assert contract.predefined_objectives[0].objective_key == task.objective_scope_keys[0]
    assert task.formal_goal_contract_json is None
    assert task.formal_goal_contract_hash is None


def test_persisted_formal_goal_validates_hash_and_exact_scenario(session: Session) -> None:
    task, _runtime_value, scope = _task(session, formal=True)

    contract = load_formal_goal_for_task(session, scope, task)

    assert contract.content_hash == task.formal_goal_contract_hash
    assert contract.scenario.scenario_version_id == scope.scenario_version_id


def test_dynamic_task_does_not_need_a_fake_objective_scope(session: Session) -> None:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    from app.infrastructure.db.models import Player

    player = Player(name="formal-goal-dynamic-persistence")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="formal-goal-dynamic-persistence",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    snapshot = ScenarioVersionRepository(session).load(version.id)
    authored_requirement = snapshot.definition.objectives[0].completion_requirements[0]
    assert authored_requirement.kind == ObjectiveRequirementKind.FACT
    assert authored_requirement.node_key is not None
    assert authored_requirement.fact_key is not None
    contract = compile_ad_hoc_dynamic_goal(
        snapshot,
        (
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.FACT,
                node_key=authored_requirement.node_key,
                fact_key=authored_requirement.fact_key,
                accepted_values=authored_requirement.accepted_values,
            ),
        ),
    )
    task = AgentTask(
        player_id=player.id,
        game_instance_id=runtime.instance.id,
        owner_actor_key=runtime.session.actor_key,
        origin_session_id=runtime.session.id,
        last_session_id=runtime.session.id,
        goal_description="dynamic fact goal",
        scenario_key=GENERIC_TEST.metadata.key,
        objective_resolution_status="CONFIRMED",
        objective_scope_keys=None,
        objective_catalog_version=None,
        # The old non-null column remains a compatibility fingerprint; it is
        # not an authored ObjectiveScope and is never used by the loader.
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

    assert task.objective_scope_keys is None
    assert task.objective_catalog_version is None
    assert loaded.source_kind == FormalGoalSourceKind.AD_HOC_DYNAMIC


def test_frozen_formal_goal_orm_fields_are_immutable(session: Session) -> None:
    task, _runtime_value, _scope = _task(session, formal=True)
    task.formal_goal_contract_hash = "0" * 64

    with pytest.raises(FormalGoalImmutableError):
        session.flush()
