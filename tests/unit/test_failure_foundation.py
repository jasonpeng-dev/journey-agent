from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError
from app.agent.provider import GenericProviderError, PlanViolation
from app.domain.failures import (
    ActorEvidence,
    FactEvidence,
    FailureDomain,
    FailureEvent,
    FailureEvidence,
    FailureKind,
    FailurePhase,
    GenericEvidence,
    LocalityEvidence,
    ResourceEvidence,
    TransportEvidence,
    normalize_legacy_failure,
    project_legacy_failure,
)
from app.engine.locality import LocalityEngineError
from app.engine.rules import RuleFailure
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.versions import ScenarioVersionRepository
from app.services.generic_actions import GenericActionError
from app.services.generic_game import GenericGameError
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import LINJIANG_CURRENT_TEST


@pytest.mark.parametrize(
    ("failure", "domain", "kind", "phase", "retryable"),
    [
        (
            RuleFailure("TRAVEL_BLOCKED", "The route is blocked", True),
            FailureDomain.ACTION_RUNTIME,
            FailureKind.TRAVEL_BLOCKED,
            FailurePhase.EXECUTION,
            True,
        ),
        (
            GenericGameError("ACTION_TARGET_INVALID", "The target is invalid", retryable=True),
            FailureDomain.ACTION_RUNTIME,
            FailureKind.TARGET_INVALID,
            FailurePhase.EXECUTION,
            True,
        ),
        (
            GenericActionError("ACTION_PARAMETERS_INVALID", "The parameters are invalid"),
            FailureDomain.ACTION_RUNTIME,
            FailureKind.PARAMETER_INVALID,
            FailurePhase.EXECUTION,
            False,
        ),
        (
            LocalityEngineError(
                "LOCALITY_ROUTE_NOT_FOUND",
                "No route exists",
                retryable=True,
                details={"source_region": "a", "target_region": "b"},
            ),
            FailureDomain.ACTION_RUNTIME,
            FailureKind.LOCALITY_INVALID,
            FailurePhase.EXECUTION,
            True,
        ),
        (
            PlanViolation(
                code="TARGET_INTERACTION_INVALID",
                action_key="repair",
                actor_key="team",
                target_key="facility",
                required_interaction_key="repairable",
                actual_interactions=("inspectable",),
            ),
            FailureDomain.PLAN_VALIDATION,
            FailureKind.VALIDATOR_REJECTED,
            FailurePhase.PREFLIGHT,
            None,
        ),
        (
            GenericProviderError("MODEL_PROVIDER_TIMEOUT", "Provider timed out"),
            FailureDomain.PROVIDER,
            FailureKind.PROVIDER_ERROR,
            FailurePhase.PROVIDER_CALL,
            None,
        ),
        (
            GenericAgentError("GOAL_UNSUPPORTED", "Goal is not supported"),
            FailureDomain.GOAL,
            FailureKind.UNKNOWN,
            FailurePhase.PLANNING,
            None,
        ),
    ],
)
def test_current_failure_producers_normalize_without_losing_legacy_fields(
    failure: object,
    domain: FailureDomain,
    kind: FailureKind,
    phase: FailurePhase,
    retryable: bool | None,
) -> None:
    event = normalize_legacy_failure(failure)

    assert event.domain == domain
    assert event.kind == kind
    assert event.phase == phase
    projected = project_legacy_failure(event)
    assert projected.code == getattr(failure, "code", None)
    assert projected.message == getattr(failure, "message", str(failure))
    assert projected.retryable is retryable
    assert event.to_legacy() == projected
    assert FailureEvent.model_validate(event.model_dump(mode="json")) == event


def test_unknown_failure_is_fail_closed_and_retains_raw_identity() -> None:
    event = normalize_legacy_failure(
        {
            "code": "VENDOR_FAILURE_NEVER_SEEN",
            "message": "Vendor supplied an opaque failure",
            "retryable": True,
            "phase": "VENDOR_PHASE_9",
            "producer": "vendor-gateway",
            "details": {"opaque_token": "preserve-me", "attempt": 3},
        }
    )

    assert event.kind is FailureKind.UNKNOWN
    assert event.domain is FailureDomain.INTERNAL
    assert event.phase is FailurePhase.UNKNOWN
    assert event.producer == "vendor-gateway"
    assert event.code == "VENDOR_FAILURE_NEVER_SEEN"
    assert event.message == "Vendor supplied an opaque failure"
    assert event.legacy_retryable is True
    assert event.metadata["legacy_phase"] == "VENDOR_PHASE_9"
    assert event.metadata["legacy_details"] == {
        "opaque_token": "preserve-me",
        "attempt": 3,
        "phase": "VENDOR_PHASE_9",
    }
    assert event.to_legacy().model_dump(mode="json") == {
        "code": "VENDOR_FAILURE_NEVER_SEEN",
        "message": "Vendor supplied an opaque failure",
        "retryable": True,
    }


def test_typed_evidence_families_round_trip_and_preserve_raw_details() -> None:
    evidence = TransportEvidence(
        transport_key="route_ab",
        source_region="region_a",
        target_region="region_b",
        endpoints=("region_a", "region_b"),
        passable=False,
        knowledge_status="KNOWN",
        raw={"passability_fact_key": "passable"},
    )
    event = normalize_legacy_failure(
        RuleFailure("TRAVEL_BLOCKED", "The route is blocked", True),
        phase=FailurePhase.RESOLVE,
        evidence=evidence,
        action_key="travel",
        actor_key="team",
        target_key="region_b",
        knowledge_changes=({"kind": "FACT_REVEALED", "key": "route_ab.passable", "value": False},),
    )

    assert event.evidence == evidence
    assert event.evidence.family == "TRANSPORT"
    assert event.knowledge_changes[0]["value"] is False
    assert event.gameplay_recovery_eligible
    restored = FailureEvent.model_validate(event.model_dump(mode="json"))
    assert restored.evidence == evidence


@pytest.mark.parametrize(
    "evidence",
    [
        ResourceEvidence(
            resource_key="medicine",
            required=3,
            available=1,
            deficit=2,
            knowledge_status="KNOWN",
            scope_key="clinic",
        ),
        FactEvidence(
            node_key="facility",
            fact_key="operational",
            required=True,
            accepted_values=(True,),
            actual=False,
            knowledge_status="KNOWN",
        ),
        LocalityEvidence(
            actor_region="region_a",
            target_region="region_b",
            required_locality="SAME_REGION",
        ),
        ActorEvidence(
            actor_key="team",
            required="ONLINE",
            actual="DISCONNECTED",
            reachability="DISCONNECTED",
        ),
        GenericEvidence(details={"custom": "opaque"}),
    ],
)
def test_evidence_union_is_tagged_and_typed(evidence: FailureEvidence) -> None:
    event = FailureEvent(
        domain=FailureDomain.ACTION_RUNTIME,
        kind=FailureKind.PRECONDITION_UNMET,
        code="SYNTHETIC_FAILURE",
        phase=FailurePhase.RESOLVE,
        evidence=evidence,
    )
    restored = FailureEvent.model_validate(event.model_dump(mode="json"))
    assert restored.evidence is not None
    assert restored.evidence.family == evidence.family


def test_provider_retryability_is_separate_from_gameplay_retryability() -> None:
    event = normalize_legacy_failure(
        GenericProviderError("MODEL_PROVIDER_TRANSPORT_ERROR", "Transport failed"),
        provider_retryable=True,
    )

    assert event.domain is FailureDomain.PROVIDER
    assert event.kind is FailureKind.PROVIDER_ERROR
    assert event.provider_retryable is True
    assert event.legacy_retryable is None
    assert not event.gameplay_recovery_eligible
    assert event.to_legacy().retryable is None


def test_old_schema_v2_rule_fields_and_recovery_hints_still_parse() -> None:
    definition = LINJIANG_CURRENT_TEST
    assert definition.schema_version == 2
    assert definition.planning.recovery_hints
    authored_effect = next(
        effect
        for rule in definition.rules
        for effect in rule.effects
        if effect.kind.value == "EMIT_FAILURE"
    )
    assert authored_effect.failure_code is not None
    assert authored_effect.message is not None
    assert authored_effect.retryable is not None
    legacy = RuleFailure(
        authored_effect.failure_code,
        authored_effect.message,
        authored_effect.retryable,
    )
    event = normalize_legacy_failure(legacy, phase=FailurePhase.PREFLIGHT)
    assert event.phase is FailurePhase.PREFLIGHT
    assert event.to_legacy().model_dump(mode="json") == {
        "code": authored_effect.failure_code,
        "message": authored_effect.message,
        "retryable": authored_effect.retryable,
    }


def test_old_schema_v2_published_v7_shape_loads_without_binding_drift(session: Session) -> None:
    """Exercise a disposable v7 snapshot; production DB bytes remain untouched."""

    document = LINJIANG_CURRENT_TEST.model_dump(mode="json")
    document["metadata"]["key"] = "phase_a_legacy_v7"
    document["metadata"]["name"] = "Phase A Legacy v7"
    document["world"]["key"] = "phase_a_legacy_v7"
    document["world"]["name"] = "Phase A Legacy v7"
    definition = type(LINJIANG_CURRENT_TEST).model_validate(document)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    service = ScenarioService(session)
    version = service.publish_draft(scenario.id, expected_revision=1).version
    for revision in range(2, 8):
        next_document = definition.model_dump(mode="json")
        next_document["metadata"]["description"] = f"Legacy v2 revision {revision}"
        service.replace_draft(
            scenario.id,
            expected_revision=revision - 1,
            definition_document=next_document,
        )
        version = service.publish_draft(scenario.id, expected_revision=revision).version

    assert version.version_number == 7
    loaded = ScenarioVersionRepository(session).load(version.id)
    assert loaded.version_number == 7
    assert loaded.schema_version == 2
    loaded_actors = {
        actor.key: (actor.initial_node_key, frozenset(actor.allowed_action_keys))
        for actor in loaded.definition.actors.actor_profiles
    }
    original_actors = {
        actor.key: (actor.initial_node_key, frozenset(actor.allowed_action_keys))
        for actor in definition.actors.actor_profiles
    }
    assert loaded_actors == original_actors
    assert {item.key for item in loaded.definition.actions} == {
        item.key for item in definition.actions
    }
    assert loaded.definition.planning.recovery_hints == definition.planning.recovery_hints
    assert any(
        effect.kind.value == "EMIT_FAILURE"
        and effect.failure_code
        and effect.message
        and effect.retryable is not None
        for rule in loaded.definition.rules
        for effect in rule.effects
    )
