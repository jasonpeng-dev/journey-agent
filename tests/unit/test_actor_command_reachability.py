from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError, GenericAgentService
from app.agent.provider import PlanProposal, PlanRequest, PlanStepProposal
from app.domain.enums import CommandReachability
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import GameInstanceActor, Player
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionError, GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import GENERIC_TEST


def _relay_definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    document["metadata"]["key"] = "actor_reachability_test"
    document["metadata"]["name"] = "Actor Reachability Test"
    document["world"]["key"] = "actor_reachability_test"
    document["world"]["name"] = "Actor Reachability Test"
    document["metadata"]["locality"].update(
        {
            "enabled": True,
            "region_node_type_key": "region",
            "facility_node_type_key": "room",
            "transport_node_type_key": "room",
            "located_in_relation_type_key": "located_in",
            "transport_endpoint_relation_type_key": "located_in",
        }
    )
    document["world"]["node_types"].append({"key": "region", "name": "Region", "description": ""})
    document["world"]["nodes"].extend(
        [
            {
                "key": "clinic_region",
                "name": "Clinic Region",
                "description": "",
                "node_type_key": "region",
                "initial_access": "AVAILABLE",
                "initial_visibility": "KNOWN",
                "interaction_keys": [],
                "facts": [],
            },
            {
                "key": "remote_room",
                "name": "Remote Room",
                "description": "",
                "node_type_key": "room",
                "initial_access": "AVAILABLE",
                "initial_visibility": "KNOWN",
                "interaction_keys": [],
                "facts": [],
            },
            {
                "key": "remote_region",
                "name": "Remote Region",
                "description": "",
                "node_type_key": "region",
                "initial_access": "AVAILABLE",
                "initial_visibility": "KNOWN",
                "interaction_keys": [],
                "facts": [],
            },
        ]
    )
    document["world"]["relations"].extend(
        [
            {
                "source_node_key": "triage_room",
                "relation_type_key": "located_in",
                "target_node_key": "clinic_region",
            },
            {
                "source_node_key": "remote_room",
                "relation_type_key": "located_in",
                "target_node_key": "remote_region",
            },
        ]
    )
    document["interactions"].append(
        {
            "key": "relayable",
            "name": "Relayable",
            "description": "Can receive a command relay.",
        }
    )
    next(action for action in document["actions"] if action["key"] == "diagnose_patient")[
        "required_actor_role_key"
    ] = "nurse"
    actor_profiles = document["actors"]["actor_profiles"]
    nurse = next(item for item in actor_profiles if item["key"] == "nurse_ana")
    second_nurse = deepcopy(nurse)
    second_nurse["key"] = "nurse_beth"
    second_nurse["name"] = "Nurse Beth"
    actor_profiles.append(second_nurse)
    for actor in actor_profiles:
        actor["initial_node_key"] = "clinic_region"
        actor["command_reachability"] = "ONLINE" if actor["key"] == "doctor_lee" else "DISCONNECTED"
        if actor["key"] == "doctor_lee":
            actor["allowed_action_keys"].extend(["relay_message", "restore_comms"])
        elif actor["key"] in {"nurse_ana", "nurse_beth"}:
            actor["allowed_action_keys"].append("diagnose_patient")
    document["actions"].extend(
        [
            {
                "key": "relay_message",
                "name": "Relay Message",
                "description": "Restore command reachability for an Actor in the same Region.",
                "required_interaction_key": "relayable",
                "execution_mode": "IMMEDIATE",
                "allowed_actor_capabilities": ["EXECUTE_ACTION"],
                "expected_outcomes": [
                    {"code": "RELAYED", "name": "Message relayed", "success": True}
                ],
                "planning": {"success_outcome_codes": ["RELAYED"]},
                "behavior": "RELAY_MESSAGE",
                "locality": "ACTOR_REGION",
                "target_kind": "ACTOR",
            },
            {
                "key": "restore_comms",
                "name": "Restore Communications",
                "description": "Restore command reachability for explicitly selected Actors.",
                "required_interaction_key": "treatable",
                "execution_mode": "IMMEDIATE",
                "allowed_actor_capabilities": ["EXECUTE_ACTION"],
                "expected_outcomes": [
                    {"code": "COMMS_RESTORED", "name": "Communications restored", "success": True}
                ],
                "planning": {"success_outcome_codes": ["COMMS_RESTORED"]},
                "behavior": "RULE",
                "locality": "NONE",
            },
        ]
    )
    document["rules"].append(
        {
            "key": "relay_message_resolution",
            "phase": "RESOLVE",
            "action_key": "relay_message",
            "priority": 0,
            "effects": [{"kind": "EMIT_OUTCOME", "outcome_code": "RELAYED"}],
        }
    )
    document["rules"].append(
        {
            "key": "restore_comms_resolution",
            "phase": "RESOLVE",
            "action_key": "restore_comms",
            "priority": 1,
            "effects": [
                {
                    "kind": "SET_ACTOR_COMMAND_REACHABILITY",
                    "actor_key": "nurse_ana",
                    "command_reachability": "ONLINE",
                },
                {
                    "kind": "SET_ACTOR_COMMAND_REACHABILITY",
                    "actor_key": "nurse_beth",
                    "command_reachability": "ONLINE",
                },
                {"kind": "EMIT_OUTCOME", "outcome_code": "COMMS_RESTORED"},
            ],
        }
    )
    return ScenarioDefinitionV2.model_validate(document)


def _runtime(session: Session):  # type: ignore[no-untyped-def]
    definition = _relay_definition()
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="actor-reachability-player")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="actor-reachability-runtime",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return runtime, scope, definition


def test_disconnected_actor_cannot_execute_ordinary_action(session: Session) -> None:
    runtime, scope, _definition = _runtime(session)

    with pytest.raises(GenericActionError) as error:
        GenericActionService(session, scope).execute_action(
            actor_key="nurse_ana",
            action_key="diagnose_patient",
            target_key="patient_one",
            parameters={},
            idempotency_key="disconnected-diagnose",
        )

    assert error.value.code == "ACTOR_COMMAND_DISCONNECTED"
    actor = session.get(GameInstanceActor, (runtime.instance.id, "nurse_ana"))
    assert actor is not None
    assert actor.command_reachability == CommandReachability.DISCONNECTED.value


def test_relay_is_generic_same_region_and_restores_target_actor(session: Session) -> None:
    runtime, scope, _definition = _runtime(session)
    actions = GenericActionService(session, scope)

    relayed = actions.execute_action(
        actor_key="doctor_lee",
        action_key="relay_message",
        target_key="nurse_ana",
        parameters={},
        idempotency_key="relay-nurse",
    )

    assert relayed.applied is not None
    assert relayed.applied.outcome.failure is None
    target = session.get(GameInstanceActor, (runtime.instance.id, "nurse_ana"))
    assert target is not None
    assert target.command_reachability == CommandReachability.ONLINE.value

    ordinary = actions.execute_action(
        actor_key="nurse_ana",
        action_key="diagnose_patient",
        target_key="patient_one",
        parameters={},
        idempotency_key="post-relay-diagnose",
    )
    assert ordinary.applied is not None
    assert ordinary.applied.outcome.failure is None


def test_relay_rejects_different_region_and_online_target(session: Session) -> None:
    runtime, scope, _definition = _runtime(session)
    doctor = session.get(GameInstanceActor, (runtime.instance.id, "doctor_lee"))
    target = session.get(GameInstanceActor, (runtime.instance.id, "nurse_ana"))
    assert doctor is not None and target is not None
    doctor.current_node_key = "remote_region"
    session.flush()
    with pytest.raises(GenericActionError) as locality_error:
        GenericActionService(session, scope).execute_action(
            actor_key="doctor_lee",
            action_key="relay_message",
            target_key="nurse_ana",
            parameters={},
            idempotency_key="relay-wrong-region",
        )
    assert locality_error.value.code == "LOCALITY_ACTOR_REGION_INVALID"

    doctor.current_node_key = "clinic_region"
    target.command_reachability = CommandReachability.ONLINE.value
    session.flush()
    with pytest.raises(GenericActionError) as online_error:
        GenericActionService(session, scope).execute_action(
            actor_key="doctor_lee",
            action_key="relay_message",
            target_key="nurse_ana",
            parameters={},
            idempotency_key="relay-online-target",
        )
    assert online_error.value.code == "RELAY_TARGET_NOT_DISCONNECTED"


def test_explicit_actor_reachability_effect_updates_multiple_non_target_actors(
    session: Session,
) -> None:
    runtime, scope, _definition = _runtime(session)

    applied = GenericActionService(session, scope).execute_action(
        actor_key="doctor_lee",
        action_key="restore_comms",
        target_key="patient_one",
        parameters={},
        idempotency_key="restore-explicit-actors",
    )

    assert applied.applied is not None
    assert applied.applied.outcome.failure is None
    for actor_key in ("nurse_ana", "nurse_beth"):
        actor = session.get(GameInstanceActor, (runtime.instance.id, actor_key))
        assert actor is not None
        assert actor.command_reachability == CommandReachability.ONLINE.value


class _RelayPlanProvider:
    model_name = "relay-plan-test-provider"

    def __init__(self, *, relay_first: bool) -> None:
        self.relay_first = relay_first
        self.requests: list[PlanRequest] = []

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        relay = PlanStepProposal(
            action_key="relay_message",
            actor_key="doctor_lee",
            target_key="nurse_ana",
        )
        diagnose = PlanStepProposal(
            action_key="diagnose_patient",
            actor_key="nurse_ana",
            target_key="patient_one",
        )
        return PlanProposal(
            plan_summary="relay before ordinary action",
            steps=(relay, diagnose) if self.relay_first else (diagnose, relay),
        )


def test_plan_projection_allows_relay_then_disconnected_actor_action(
    session: Session,
) -> None:
    runtime, scope, _definition = _runtime(session)
    provider = _RelayPlanProvider(relay_first=True)
    task = GenericAgentService(
        session,
        scope,
        provider=provider,
    ).create_task(runtime.session, "diagnose the patient")

    assert task.current_plan_version == 1
    request = provider.requests[0]
    assert request.planning_context is not None
    disconnected = next(
        item
        for item in request.planning_context.relevant_actors
        if item["actor_key"] == "nurse_ana"
    )
    assert disconnected["current_known_state"]["command_reachability"] == "DISCONNECTED"
    assert disconnected["execution_state"]["status"] == "KNOWN_BLOCKED"
    assert disconnected["execution_state"]["known_blockers"][0]["type"] == ("COMMAND_REACHABILITY")
    assert "diagnose_patient" in disconnected["allowed_action_keys"]
    assert "nurse_ana" in {item["target_key"] for item in request.planning_context.relevant_targets}
    relay = next(
        item
        for item in request.planning_context.relevant_actions
        if item["action_key"] == "relay_message"
    )
    assert relay["target_kind"] == "ACTOR"


def test_plan_projection_rejects_ordinary_action_before_relay(session: Session) -> None:
    runtime, scope, _definition = _runtime(session)
    provider = _RelayPlanProvider(relay_first=False)
    with pytest.raises(GenericAgentError, match="could not produce") as error:
        GenericAgentService(
            session,
            scope,
            provider=provider,
        ).create_task(runtime.session, "diagnose the patient")

    assert error.value.code == "MODEL_PLAN_REJECTED"
    diagnostic = provider.requests[1].repair_diagnostics[0]
    assert diagnostic.code == "ACTOR_COMMAND_DISCONNECTED"
    assert diagnostic.step_id
    assert diagnostic.dimension == "COMMAND_REACHABILITY"
    assert diagnostic.required == "ONLINE"
    assert diagnostic.actual == "DISCONNECTED"
    assert diagnostic.action_key == "diagnose_patient"
    assert diagnostic.actor_key == "nurse_ana"
    assert diagnostic.target_key == "patient_one"
    assert "known_recovery_effects" not in diagnostic.model_dump()
