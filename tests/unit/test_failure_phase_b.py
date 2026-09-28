"""Phase B producer normalization and declarative evidence regressions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from app.domain.enums import CommandReachability
from app.domain.failures import (
    ActorEvidence,
    FactEvidence,
    FailureDomain,
    FailureKind,
    FailurePhase,
    ResourceEvidence,
)
from app.domain.scenario_v2 import RuleDefinitionV2, ScenarioDefinitionV2
from app.domain.world import AccessState, Visibility
from app.engine.rules import (
    ActionRuleContext,
    DeclarativeRuleEngine,
    DeclarativeRuleState,
    RuleFactState,
    RuleNodeState,
)
from app.infrastructure.db.models import GameInstanceResourceState
from app.services.generic_actions import GenericActionError, GenericActionService
from app.services.generic_game import GenericGameError, GenericGameService
from tests.integration.test_linjiang_v2_0_v3_knowledge import (
    V2_0,
    _set_actor,
)
from tests.integration.test_linjiang_v2_0_v3_knowledge import _runtime as _linjiang_runtime
from tests.unit.test_generic_agent import _agent
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document
from tests.unit.test_transport_resource import _definition as _transport_definition
from tests.unit.test_transport_resource import _runtime as _transport_runtime
from tests.unit.test_transport_resource import _transport


def _rule_definition(condition: dict[str, Any]) -> tuple[ScenarioDefinitionV2, RuleDefinitionV2]:
    document = deepcopy(_contract_scenario_document())
    document["rules"] = []
    document["world"]["nodes"][1]["facts"] = [
        {
            "key": "ready",
            "name": "Ready",
            "value_type": "BOOLEAN",
            "initial_value": False,
            "initial_visibility": "KNOWN",
        }
    ]
    definition = ScenarioDefinitionV2.model_validate(document)
    rule = RuleDefinitionV2.model_validate(
        {
            "key": "phase_b_failure",
            "phase": "PREFLIGHT",
            "action_key": "treat_patient",
            "priority": 1,
            "condition": condition,
            "effects": [
                {
                    "kind": "EMIT_FAILURE",
                    "failure_code": "PHASE_B_FAILURE",
                    "message": "The authored precondition was not met.",
                    "retryable": True,
                }
            ],
        }
    )
    return definition, rule


def _rule_state(*, medicine: int | None = 0) -> DeclarativeRuleState:
    resources = {} if medicine is None else {"medicine": medicine}
    return DeclarativeRuleState(
        nodes={
            "patient_one": RuleNodeState(Visibility.KNOWN, AccessState.AVAILABLE),
            "triage_room": RuleNodeState(Visibility.KNOWN, AccessState.AVAILABLE),
        },
        facts={
            ("patient_one", "stable"): RuleFactState(False, Visibility.KNOWN),
            ("triage_room", "ready"): RuleFactState(False, Visibility.KNOWN),
        },
        resources=resources,
        resource_reservations={},
    )


def _rule_context() -> ActionRuleContext:
    return ActionRuleContext(
        action_key="treat_patient",
        target_node_key="patient_one",
        parameters={"dosage": 1},
        actor_key="doctor_lee",
        actor_current_node_key="triage_room",
        source_node_key="triage_room",
    )


@pytest.mark.parametrize(
    ("condition", "expected_node", "expected_fact", "expected_values"),
    [
        (
            {
                "kind": "FACT_EQUALS",
                "node": {"kind": "CURRENT_TARGET"},
                "fact_key": "stable",
                "value": True,
            },
            "patient_one",
            "stable",
            (True,),
        ),
        (
            {
                "kind": "FACT_NOT_EQUALS",
                "node": {"kind": "ACTION_SOURCE"},
                "fact_key": "ready",
                "value": True,
            },
            "triage_room",
            "ready",
            (True,),
        ),
        (
            {
                "kind": "FACT_IN",
                "node": {"kind": "EXPLICIT", "node_key": "patient_one"},
                "fact_key": "stable",
                "values": [True, False],
            },
            "patient_one",
            "stable",
            (True, False),
        ),
    ],
)
def test_rule_failure_event_extracts_fact_selectors_and_legacy_fields(
    condition: dict[str, Any],
    expected_node: str,
    expected_fact: str,
    expected_values: tuple[bool, ...],
) -> None:
    definition, rule = _rule_definition(condition)
    outcome = DeclarativeRuleEngine(definition)._outcome(rule, _rule_state(), _rule_context())

    assert outcome.failure is not None
    assert outcome.failure_event is not None
    event = outcome.failure_event
    assert event.code == "PHASE_B_FAILURE"
    assert event.message == outcome.failure.message
    assert event.legacy_retryable is True
    assert event.phase is FailurePhase.PREFLIGHT
    assert event.domain is FailureDomain.ACTION_RUNTIME
    assert event.kind is FailureKind.PRECONDITION_UNMET
    assert isinstance(event.evidence, FactEvidence)
    assert event.evidence.node_key == expected_node
    assert event.evidence.fact_key == expected_fact
    assert event.evidence.accepted_values == expected_values
    assert event.evidence.actual is False
    assert event.evidence.knowledge_status == Visibility.KNOWN.value


def test_rule_failure_event_distinguishes_known_shortfall_and_unknown_resource() -> None:
    condition = {
        "kind": "RESOURCE_COMPARE",
        "resource_key": "medicine",
        "operator": "LT",
        "value": 3,
    }
    definition, rule = _rule_definition(condition)
    engine = DeclarativeRuleEngine(definition)

    known = engine._outcome(rule, _rule_state(medicine=0), _rule_context())
    assert known.failure_event is not None
    assert known.failure_event.kind is FailureKind.RESOURCE_INSUFFICIENT
    assert isinstance(known.failure_event.evidence, ResourceEvidence)
    assert known.failure_event.evidence.available == 0
    assert known.failure_event.evidence.deficit == 3
    assert known.failure_event.evidence.knowledge_status == "KNOWN"

    unknown = engine._outcome(rule, _rule_state(medicine=None), _rule_context())
    assert unknown.failure_event is not None
    assert unknown.failure_event.kind is FailureKind.KNOWLEDGE_UNKNOWN
    assert isinstance(unknown.failure_event.evidence, ResourceEvidence)
    assert unknown.failure_event.evidence.available is None
    assert unknown.failure_event.evidence.deficit is None
    assert unknown.failure_event.evidence.knowledge_status == "UNKNOWN"


def test_rule_failure_event_keeps_all_condition_evidence() -> None:
    definition, rule = _rule_definition(
        {
            "kind": "ALL",
            "conditions": [
                {
                    "kind": "FACT_NOT_EQUALS",
                    "node": {"kind": "CURRENT_TARGET"},
                    "fact_key": "stable",
                    "value": True,
                },
                {
                    "kind": "RESOURCE_COMPARE",
                    "resource_key": "medicine",
                    "operator": "LT",
                    "value": 3,
                },
            ],
        }
    )
    outcome = DeclarativeRuleEngine(definition)._outcome(rule, _rule_state(), _rule_context())

    assert outcome.failure_event is not None
    assert isinstance(outcome.failure_event.evidence, FactEvidence)
    assert [item.family for item in outcome.failure_event.additional_evidence] == ["RESOURCE"]
    resource = outcome.failure_event.additional_evidence[0]
    assert isinstance(resource, ResourceEvidence)
    assert resource.resource_key == "medicine"


def test_intrinsic_transport_failure_exposes_typed_event_without_wire_change(
    session: Session,
) -> None:
    document = _transport_definition().model_dump(mode="json")
    edge = next(node for node in document["world"]["nodes"] if node["key"] == "edge_ab")
    edge["facts"][0]["initial_value"] = False
    definition = _transport_definition().model_validate(document)
    _runtime, scope = _transport_runtime(session, definition, "phase-b-transport-blocked")

    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 1},
        key="phase-b-transport-blocked-1",
    )

    assert result.applied is not None
    assert result.applied.outcome.failure is not None
    assert result.applied.outcome.failure_event is not None
    event = result.applied.outcome.failure_event
    assert event.code == "TRANSPORT_BLOCKED"
    assert event.kind is FailureKind.TRAVEL_BLOCKED
    assert event.producer == "GenericGameService"
    assert event.legacy_retryable is True
    assert event.evidence is not None and event.evidence.family == "TRANSPORT"
    assert result.operation.outcome is not None
    assert result.operation.outcome["failure"]["code"] == "TRANSPORT_BLOCKED"
    assert "failure_event" not in result.operation.outcome


def test_resource_expansion_failure_exposes_known_vs_unknown_evidence(
    session: Session,
) -> None:
    definition = _transport_definition()
    runtime, scope = _transport_runtime(session, definition, "phase-b-resource-failure")

    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 11},
        key="phase-b-resource-failure-1",
    )
    assert result.applied is not None
    assert result.applied.outcome.failure_event is not None
    event = result.applied.outcome.failure_event
    assert event.code == "TRANSPORT_RESOURCE_INSUFFICIENT"
    assert event.kind is FailureKind.RESOURCE_INSUFFICIENT
    assert isinstance(event.evidence, ResourceEvidence)
    assert event.evidence.knowledge_status == "KNOWN"

    # The first transport makes the destination inflow known.  A second
    # transport from that Region therefore takes the knowledge-unknown path.
    first = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
        key="phase-b-resource-failure-2",
    )
    assert first.applied is not None and first.applied.outcome.failure is None
    second = _transport(
        session,
        scope,
        target_key="region_c",
        parameters={"resource_key": "cargo_alpha", "amount": 15},
        key="phase-b-resource-failure-3",
    )
    assert second.applied is not None
    assert second.applied.outcome.failure_event is not None
    unknown = second.applied.outcome.failure_event
    assert unknown.code == "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN"
    assert unknown.kind is FailureKind.KNOWLEDGE_UNKNOWN
    assert isinstance(unknown.evidence, ResourceEvidence)
    assert unknown.evidence.knowledge_status == "UNKNOWN"
    assert unknown.evidence.deficit is None
    assert runtime.instance.runtime_revision >= 0


def test_actor_reachability_failure_is_typed_and_action_recovery_eligible(
    session: Session,
) -> None:
    runtime, scope = cast(
        tuple[Any, Any], _linjiang_runtime(session, V2_0, "phase-b-actor-disconnected")
    )
    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "central_district")
    actor = GenericGameService(session, scope)._actor("logistics_team_alpha")
    actor.command_reachability = CommandReachability.DISCONNECTED.value
    session.flush()

    with pytest.raises(GenericGameError) as caught:
        GenericGameService(session, scope).execute(
            actor_key="logistics_team_alpha",
            action_key="travel",
            target_node_key="east_residential_district",
            parameters={},
        )

    event = caught.value.failure_event
    assert event.code == "ACTOR_COMMAND_DISCONNECTED"
    assert event.kind is FailureKind.ACTOR_UNAVAILABLE
    assert event.domain is FailureDomain.ACTION_RUNTIME
    assert isinstance(event.evidence, ActorEvidence)
    assert event.evidence.actor_key == "logistics_team_alpha"
    assert event.evidence.reachability == CommandReachability.DISCONNECTED.value


@pytest.mark.parametrize(
    "error",
    [
        GenericGameError("RUNTIME_ACTOR_BINDING_INVALID", "binding drift"),
        GenericGameError("RUNTIME_STATE_INCOMPLETE", "state drift"),
    ],
)
def test_runtime_integrity_errors_do_not_become_gameplay_recovery(
    error: GenericGameError,
) -> None:
    event = error.failure_event
    assert event.domain is FailureDomain.INTERNAL
    assert event.kind is FailureKind.INTERNAL_ERROR
    assert not event.gameplay_recovery_eligible


def test_action_lifecycle_error_stays_outside_gameplay_recovery() -> None:
    event = GenericActionError(
        "ACTION_IDEMPOTENCY_KEY_REQUIRED",
        "an idempotency key is required",
    ).failure_event

    assert event.domain is FailureDomain.INTERNAL
    assert event.kind is FailureKind.INTERNAL_ERROR
    assert not event.gameplay_recovery_eligible


def test_generic_action_preflight_preserves_rule_failure_event(
    session: Session,
) -> None:
    agent, runtime = cast(tuple[Any, Any], _agent(session, preflight=True))
    resource = session.get(GameInstanceResourceState, (runtime.instance.id, "medicine"))
    assert resource is not None
    resource.value = 0
    session.flush()

    with pytest.raises(GenericActionError) as caught:
        GenericActionService(session, agent.scope).execute_action(
            actor_key="doctor_lee",
            action_key="treat_patient",
            target_key="patient_one",
            parameters={"dosage": 2},
            idempotency_key="phase-b-preflight-event",
        )

    event = caught.value.failure_event
    assert event.code == "INSUFFICIENT_MEDICINE"
    assert event.phase is FailurePhase.PREFLIGHT
    assert event.producer == "DeclarativeRuleEngine"
    assert event.kind is FailureKind.RESOURCE_INSUFFICIENT
    assert isinstance(event.evidence, ResourceEvidence)
