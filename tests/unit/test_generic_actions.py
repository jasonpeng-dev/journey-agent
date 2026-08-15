from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.engine.rules import RuleEngineError
from app.infrastructure.db.models import GameInstanceFactState, Player
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from app.tools.generic import ExecuteActionArgs, GenericActionToolContext, execute_action
from tests.unit.test_scenario_definition_v2 import _medical_scenario_document


def _game(
    session: Session,
    *,
    async_action: bool,
) -> tuple[GenericActionService, GenericActionToolContext, object]:
    document = deepcopy(_medical_scenario_document())
    document["actions"][0]["execution_mode"] = "ASYNC" if async_action else "IMMEDIATE"
    definition = ScenarioDefinitionV2.model_validate(document)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name=f"action-{'async' if async_action else 'immediate'}")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=f"action-{'async' if async_action else 'immediate'}",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return (
        GenericActionService(session, scope),
        GenericActionToolContext(scope, "doctor_lee"),
        runtime,
    )


def test_single_generic_tool_executes_immediate_action_idempotently(session: Session) -> None:
    _service, context, _runtime = _game(session, async_action=False)
    args = ExecuteActionArgs(
        action_key="treat_patient",
        target_key="patient_one",
        parameters={"dosage": 2},
        idempotency_key="immediate-action",
    )

    first = execute_action(session, context, args)
    replay = execute_action(session, context, args)

    assert first["status"] == "RESOLVED"
    assert first["outcome"]["outcome_code"] == "COMPLETED"
    assert not first["replayed"]
    assert replay["operation_id"] == first["operation_id"]
    assert replay["replayed"]


def test_async_action_waits_then_resolves_through_exact_same_generic_path(
    session: Session,
) -> None:
    service, context, runtime = _game(session, async_action=True)
    started = execute_action(
        session,
        context,
        ExecuteActionArgs(
            action_key="treat_patient",
            target_key="patient_one",
            parameters={"dosage": 2},
            idempotency_key="async-action",
        ),
    )
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "patient_one", "stable"),
    )
    assert started["status"] == "PENDING"
    assert fact is not None and fact.truth_value is False

    resolved = service.resolve_operation(
        UUID(started["operation_id"]), resolution_key="world-event-1"
    )
    replay = service.resolve_operation(resolved.operation.id, resolution_key="world-event-1")

    assert resolved.operation.status.value == "RESOLVED"
    assert resolved.applied is not None
    assert resolved.applied.outcome.outcome_code == "COMPLETED"
    assert fact.truth_value is True
    assert replay.replayed


def test_rule_engine_error_becomes_safe_operation_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, context, _runtime = _game(session, async_action=True)
    started = execute_action(
        session,
        context,
        ExecuteActionArgs(
            action_key="treat_patient",
            target_key="patient_one",
            parameters={"dosage": 2},
            idempotency_key="contract-error-action",
        ),
    )

    def raise_contract_error(**_kwargs: object) -> None:
        raise RuleEngineError("RULE_PARAMETER_MISSING", "Required rule state is missing")

    monkeypatch.setattr(service.game, "execute", raise_contract_error)
    resolved = service.resolve_operation(
        UUID(started["operation_id"]), resolution_key="contract-error-resolution"
    )

    assert resolved.operation.status.value == "RESOLVED"
    assert resolved.applied is not None
    assert resolved.applied.outcome.failure is not None
    assert resolved.applied.outcome.failure.code == "RULE_PARAMETER_MISSING"
    assert not resolved.applied.outcome.failure.retryable
