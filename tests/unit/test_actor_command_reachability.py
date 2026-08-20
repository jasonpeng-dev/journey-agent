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
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionError, GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService


def _relay_definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(LINJIANG_INFRASTRUCTURE_RECOVERY_V1.model_dump(mode="json"))
    document["metadata"]["key"] = "actor_reachability_test"
    document["metadata"]["name"] = "Actor Reachability Test"
    document["world"]["key"] = "actor_reachability_test"
    document["world"]["name"] = "Actor Reachability Test"
    for actor in document["actors"]["actor_profiles"]:
        if actor["key"] == "electrical_team_beta":
            actor["command_reachability"] = "DISCONNECTED"
        if actor["key"] == "municipal_repair_team_alpha":
            actor["command_reachability"] = "DISCONNECTED"
        if actor["key"] == "logistics_team_alpha":
            actor["allowed_action_keys"].append("relay_message")
            actor["allowed_action_keys"].append("restore_comms")
    document["actions"].append(
        {
            "key": "relay_message",
            "name": "Relay Message",
            "description": "Restore command reachability for an Actor in the same Region.",
            "required_interaction_key": "inspectable",
            "execution_mode": "IMMEDIATE",
            "allowed_actor_capabilities": ["EXECUTE_ACTION"],
            "expected_outcomes": [{"code": "RELAYED", "name": "Message relayed", "success": True}],
            "planning": {"success_outcome_codes": ["RELAYED"]},
            "behavior": "RELAY_MESSAGE",
            "locality": "ACTOR_REGION",
            "target_kind": "ACTOR",
        }
    )
    document["actions"].append(
        {
            "key": "restore_comms",
            "name": "Restore Communications",
            "description": "Restore command reachability for explicitly selected Actors.",
            "required_interaction_key": "inspectable",
            "execution_mode": "IMMEDIATE",
            "allowed_actor_capabilities": ["EXECUTE_ACTION"],
            "expected_outcomes": [
                {"code": "COMMS_RESTORED", "name": "Communications restored", "success": True}
            ],
            "planning": {"success_outcome_codes": ["COMMS_RESTORED"]},
            "behavior": "RULE",
            "locality": "FACILITY_REGION",
        }
    )
    document["rules"].append(
        {
            "key": "relay_message_resolution",
            "phase": "RESOLVE",
            "action_key": "relay_message",
            "priority": 1,
            "effects": [
                {
                    "kind": "SET_ACTOR_COMMAND_REACHABILITY",
                    "command_reachability": "ONLINE",
                },
                {"kind": "EMIT_OUTCOME", "outcome_code": "RELAYED"},
            ],
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
                    "actor_key": "electrical_team_beta",
                    "command_reachability": "ONLINE",
                },
                {
                    "kind": "SET_ACTOR_COMMAND_REACHABILITY",
                    "actor_key": "municipal_repair_team_alpha",
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
            actor_key="electrical_team_beta",
            action_key="inspect",
            target_key="central_hospital",
            parameters={},
            idempotency_key="disconnected-inspect",
        )

    assert error.value.code == "ACTOR_COMMAND_DISCONNECTED"
    actor = session.get(
        GameInstanceActor,
        (runtime.instance.id, "electrical_team_beta"),
    )
    assert actor is not None
    assert actor.command_reachability == CommandReachability.DISCONNECTED.value


def test_relay_is_generic_same_region_and_restores_target_actor(session: Session) -> None:
    runtime, scope, _definition = _runtime(session)
    actions = GenericActionService(session, scope)

    relayed = actions.execute_action(
        actor_key="logistics_team_alpha",
        action_key="relay_message",
        target_key="electrical_team_beta",
        parameters={},
        idempotency_key="relay-electrical",
    )

    assert relayed.applied is not None
    assert relayed.applied.outcome.failure is None
    target = session.get(
        GameInstanceActor,
        (runtime.instance.id, "electrical_team_beta"),
    )
    assert target is not None
    assert target.command_reachability == CommandReachability.ONLINE.value

    ordinary = actions.execute_action(
        actor_key="electrical_team_beta",
        action_key="inspect",
        target_key="central_hospital",
        parameters={},
        idempotency_key="post-relay-inspect",
    )
    assert ordinary.applied is not None
    assert ordinary.applied.outcome.failure is None


def test_relay_rejects_different_region_and_online_target(session: Session) -> None:
    runtime, scope, _definition = _runtime(session)
    logistics = session.get(
        GameInstanceActor,
        (runtime.instance.id, "logistics_team_alpha"),
    )
    target = session.get(
        GameInstanceActor,
        (runtime.instance.id, "electrical_team_beta"),
    )
    assert logistics is not None and target is not None
    logistics.current_node_key = "north_industrial_district"
    session.flush()
    with pytest.raises(GenericActionError) as locality_error:
        GenericActionService(session, scope).execute_action(
            actor_key="logistics_team_alpha",
            action_key="relay_message",
            target_key="electrical_team_beta",
            parameters={},
            idempotency_key="relay-wrong-region",
        )
    assert locality_error.value.code == "LOCALITY_ACTOR_REGION_INVALID"

    logistics.current_node_key = "central_district"
    target.command_reachability = CommandReachability.ONLINE.value
    session.flush()
    with pytest.raises(GenericActionError) as online_error:
        GenericActionService(session, scope).execute_action(
            actor_key="logistics_team_alpha",
            action_key="relay_message",
            target_key="electrical_team_beta",
            parameters={},
            idempotency_key="relay-online-target",
        )
    assert online_error.value.code == "RELAY_TARGET_NOT_DISCONNECTED"


def test_explicit_actor_reachability_effect_updates_multiple_non_target_actors(
    session: Session,
) -> None:
    runtime, scope, _definition = _runtime(session)

    applied = GenericActionService(session, scope).execute_action(
        actor_key="logistics_team_alpha",
        action_key="restore_comms",
        target_key="central_hospital",
        parameters={},
        idempotency_key="restore-explicit-actors",
    )

    assert applied.applied is not None
    assert applied.applied.outcome.failure is None
    for actor_key in ("electrical_team_beta", "municipal_repair_team_alpha"):
        actor = session.get(GameInstanceActor, (runtime.instance.id, actor_key))
        assert actor is not None
        assert actor.command_reachability == CommandReachability.ONLINE.value


class _RelayPlanProvider:
    model_name = "relay-plan-test-provider"

    def __init__(self, *, relay_first: bool) -> None:
        self.relay_first = relay_first
        self.requests: list[PlanRequest] = []

    def select_objectives(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("The exact Linjiang objective alias should resolve deterministically")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        relay = PlanStepProposal(
            action_key="relay_message",
            actor_key="logistics_team_alpha",
            target_key="electrical_team_beta",
        )
        repair = PlanStepProposal(
            action_key="repair_electrical",
            actor_key="electrical_team_beta",
            target_key="central_hospital",
        )
        return PlanProposal(
            plan_summary="relay before ordinary action",
            steps=(relay, repair) if self.relay_first else (repair, relay),
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
    ).create_task(runtime.session, "Restore emergency power to Central Hospital.")

    assert task.current_plan_version == 1
    request = provider.requests[0]
    assert request.planning_context is not None
    disconnected = next(
        item
        for item in request.planning_context.relevant_actors
        if item["actor_key"] == "electrical_team_beta"
    )
    assert disconnected["current_known_state"]["command_reachability"] == "DISCONNECTED"
    assert "electrical_team_beta" in {
        item["target_key"] for item in request.planning_context.relevant_targets
    }
    relay = next(
        item
        for item in request.planning_context.relevant_actions
        if item["action_key"] == "relay_message"
    )
    assert relay["target_kind"] == "ACTOR"


def test_plan_projection_rejects_ordinary_action_before_relay(session: Session) -> None:
    runtime, scope, _definition = _runtime(session)
    with pytest.raises(GenericAgentError, match="could not produce") as error:
        GenericAgentService(
            session,
            scope,
            provider=_RelayPlanProvider(relay_first=False),
        ).create_task(runtime.session, "Restore emergency power to Central Hospital.")

    assert error.value.code == "MODEL_PLAN_REJECTED"
