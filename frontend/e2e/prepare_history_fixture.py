"""Create deterministic browser-only PLAY history without calling a Provider."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import select

from app.agent.generic import GenericAgentService, GenericGoalResolution
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    StepExecutionType,
    WorldOperationStatus,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    PlayerExecutionCheckpoint,
    Scenario,
    ScenarioDraft,
    ScenarioVersion,
    WorldOperation,
)
from app.infrastructure.db.session import SessionLocal
from app.scenarios.migration import preview_v2_to_v3
from app.scenarios.target_applicability_migration import (
    migrate_target_applicability_document,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleService
from app.services.generic_game import GenericGameService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import GENERIC_TEST

SCENARIO_KEY = "generic_authoring_e2e"
SCENARIO_NAME = "Generic Authoring Scenario"
TARGET_SCENARIO_KEY = "target_applicability_e2e"
TARGET_SCENARIO_NAME = "Target Applicability E2E Scenario"
PORTABILITY_SCENARIO_KEY = "scenario_portability_e2e"
PORTABILITY_SCENARIO_NAME = "Scenario Portability E2E"
EMPTY_QUICK_INPUT_SCENARIO_KEY = "scenario_portability_empty_quick_inputs_e2e"
EMPTY_QUICK_INPUT_SCENARIO_NAME = "Scenario Portability Empty Quick Inputs E2E"


def generic_authoring_definition() -> ScenarioDefinitionV2:
    """Add a typed binary enum to the isolated browser authoring scenario."""

    payload = GENERIC_TEST.model_dump(mode="json")
    payload.setdefault("goal_resolution", {})["quick_inputs"] = [
        "E2E quick target one",
        "E2E quick target two",
    ]
    nodes = payload.get("world", {}).get("nodes", [])
    node = next(
        candidate
        for candidate in nodes
        if any(fact.get("value_type") == "BOOLEAN" for fact in candidate.get("facts", []))
    )
    node.setdefault("facts", []).append(
        {
            "key": "e2e_binary_availability",
            "name": "E2E 可用状态",
            "description": "",
            "value_type": "ENUM",
            "initial_value": "AVAILABLE",
            "initial_visibility": "KNOWN",
            "goal_addressable": False,
            "allowed_values": ["AVAILABLE", "UNAVAILABLE"],
        }
    )
    return ScenarioDefinitionV2.model_validate(payload)


def ensure_generic_scenario(db):  # type: ignore[no-untyped-def]
    scenario = db.scalar(select(Scenario).where(Scenario.key == SCENARIO_KEY))
    if scenario is None:
        service = ScenarioService(db)
        scenario = service.create_from_definition(
            key=SCENARIO_KEY,
            name=SCENARIO_NAME,
            definition=generic_authoring_definition(),
        )
        db.flush()
        draft = db.get(ScenarioDraft, scenario.id)
        if draft is None:
            raise RuntimeError("generic E2E Scenario Draft was not created")
        service.publish_draft(scenario.id, expected_revision=draft.revision)
        db.commit()
    if scenario.current_published_version_id is None:
        raise RuntimeError("generic E2E ScenarioVersion was not published")
    version = db.get(ScenarioVersion, scenario.current_published_version_id)
    if version is None:
        raise RuntimeError("generic E2E ScenarioVersion is unavailable")
    return scenario, version


def portability_v3_document() -> dict[str, object]:
    """Build a current-schema document only for the isolated portability E2E."""

    document = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json")).target_document
    document["metadata"]["key"] = PORTABILITY_SCENARIO_KEY
    document["metadata"]["name"] = PORTABILITY_SCENARIO_NAME
    document["world"]["key"] = PORTABILITY_SCENARIO_KEY
    document["world"]["name"] = PORTABILITY_SCENARIO_NAME
    document["planning"]["instructions"] = [
        "Portability browser fixture instruction",
    ]
    document["goal_resolution"]["quick_inputs"] = [
        "Portability browser fixture goal v1",
    ]
    return document


def ensure_portability_scenario(db):  # type: ignore[no-untyped-def]
    """Create a published v3 source for browser-only portability coverage."""

    scenario = db.scalar(select(Scenario).where(Scenario.key == PORTABILITY_SCENARIO_KEY))
    service = ScenarioService(db)
    if scenario is None:
        document = portability_v3_document()
        scenario = service.create_from_authored_document(
            key=PORTABILITY_SCENARIO_KEY,
            name=PORTABILITY_SCENARIO_NAME,
            definition_document=document,
        )
        db.flush()
    if scenario.current_published_version_id is None:
        draft = db.get(ScenarioDraft, scenario.id)
        if draft is None:
            raise RuntimeError("portability E2E Scenario Draft was not created")
        service.publish_draft(scenario.id, expected_revision=draft.revision)
        db.commit()
    versions = tuple(
        db.scalars(
            select(ScenarioVersion)
            .where(ScenarioVersion.scenario_id == scenario.id)
            .order_by(ScenarioVersion.version_number)
        )
    )
    if len(versions) == 1:
        draft = db.get(ScenarioDraft, scenario.id)
        if draft is None:
            raise RuntimeError("portability E2E Scenario Draft is unavailable")
        changed = deepcopy(draft.definition_document)
        instructions = list(changed.get("planning", {}).get("instructions", []))
        instructions.append("Portability browser fixture historical change")
        changed.setdefault("planning", {})["instructions"] = instructions
        changed["goal_resolution"]["quick_inputs"] = [
            "Portability browser fixture goal v2",
        ]
        service.replace_draft(
            scenario.id,
            expected_revision=draft.revision,
            definition_document=changed,
        )
        updated = db.get(ScenarioDraft, scenario.id)
        if updated is None:
            raise RuntimeError("portability E2E Scenario Draft update failed")
        service.publish_draft(scenario.id, expected_revision=updated.revision)
        db.commit()
    version = db.get(ScenarioVersion, scenario.current_published_version_id)
    if version is None:
        raise RuntimeError("portability E2E ScenarioVersion is unavailable")
    return scenario, version


def ensure_empty_quick_input_scenario(db):  # type: ignore[no-untyped-def]
    """Create a V3 Published scenario whose authoritative quick-input list is empty."""

    scenario = db.scalar(select(Scenario).where(Scenario.key == EMPTY_QUICK_INPUT_SCENARIO_KEY))
    if scenario is None:
        document = portability_v3_document()
        document["metadata"]["key"] = EMPTY_QUICK_INPUT_SCENARIO_KEY
        document["metadata"]["name"] = EMPTY_QUICK_INPUT_SCENARIO_NAME
        document["world"]["key"] = EMPTY_QUICK_INPUT_SCENARIO_KEY
        document["world"]["name"] = EMPTY_QUICK_INPUT_SCENARIO_NAME
        document["goal_resolution"]["quick_inputs"] = []
        service = ScenarioService(db)
        scenario = service.create_from_authored_document(
            key=EMPTY_QUICK_INPUT_SCENARIO_KEY,
            name=EMPTY_QUICK_INPUT_SCENARIO_NAME,
            definition_document=document,
        )
        db.flush()
        draft = db.get(ScenarioDraft, scenario.id)
        if draft is None:
            raise RuntimeError("empty quick-input Scenario Draft was not created")
        service.publish_draft(scenario.id, expected_revision=draft.revision)
        db.commit()
    version = db.get(ScenarioVersion, scenario.current_published_version_id)
    if version is None:
        raise RuntimeError("empty quick-input ScenarioVersion is unavailable")
    return scenario, version


def target_applicability_definition() -> ScenarioDefinitionV2:
    """Build the isolated browser fixture from the deterministic migration copy."""

    source_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "scenarios"
        / "data"
        / "linjiang_infrastructure_recovery_v2_0.yaml"
    )
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise RuntimeError("target applicability fixture source is not an object")
    migrated = migrate_target_applicability_document(deepcopy(source), apply_to_document=True)
    if migrated.blocked or migrated.document is None:
        raise RuntimeError(f"target applicability fixture migration blocked: {migrated.errors}")
    migrated.document["metadata"]["key"] = TARGET_SCENARIO_KEY
    migrated.document["metadata"]["name"] = TARGET_SCENARIO_NAME
    migrated.document["world"]["key"] = TARGET_SCENARIO_KEY
    migrated.document["world"]["name"] = TARGET_SCENARIO_NAME
    return ScenarioDefinitionV2.model_validate(migrated.document)


def ensure_target_applicability_scenario(db):  # type: ignore[no-untyped-def]
    scenario = db.scalar(select(Scenario).where(Scenario.key == TARGET_SCENARIO_KEY))
    if scenario is None:
        service = ScenarioService(db)
        scenario = service.create_from_definition(
            key=TARGET_SCENARIO_KEY,
            name=TARGET_SCENARIO_NAME,
            definition=target_applicability_definition(),
        )
        db.flush()
        draft = db.get(ScenarioDraft, scenario.id)
        if draft is None:
            raise RuntimeError("target applicability E2E Scenario Draft was not created")
        service.publish_draft(scenario.id, expected_revision=draft.revision)
        db.commit()
    if scenario.current_published_version_id is None:
        raise RuntimeError("target applicability E2E ScenarioVersion was not published")
    version = db.get(ScenarioVersion, scenario.current_published_version_id)
    if version is None:
        raise RuntimeError("target applicability E2E ScenarioVersion is unavailable")
    return scenario, version


def create_target_applicability_fixture() -> dict[str, str]:
    db = SessionLocal()
    try:
        scenario, version = ensure_target_applicability_scenario(db)
        definition = ScenarioVersionRepository(db).load(version.id).definition
        action = next(item for item in definition.actions if item.key == "repair_facility")
        contracts = {item.target_key: item for item in action.target_contracts}
        known_target = next(
            key for key, item in contracts.items() if item.initial_visibility == Visibility.KNOWN
        )
        hidden_target = next(
            key for key, item in contracts.items() if item.initial_visibility == Visibility.HIDDEN
        )
        runtime = GameLifecycleService(db).create(
            scenario_version_id=version.id,
            idempotency_key=f"target-applicability-{uuid4()}",
        )
        db.commit()
        return {
            "gameId": str(runtime.instance.id),
            "scenarioName": scenario.name,
            "actionKey": action.key,
            "actionName": action.name,
            "knownTargetKey": known_target,
            "hiddenTargetKey": hidden_target,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reveal_target_contract_fixture(game_id: str, target_key: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        game = db.get(GameInstance, UUID(game_id))
        if game is None:
            raise RuntimeError("target applicability fixture GameInstance is unavailable")
        version = db.get(ScenarioVersion, game.scenario_version_id)
        if version is None:
            raise RuntimeError("target applicability fixture ScenarioVersion is unavailable")
        scope = GameInstanceService(db).load(GameInstanceId(game.id))
        actor = db.scalar(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == game.id,
                GameInstanceActor.is_primary.is_(True),
            )
        )
        if actor is None:
            raise RuntimeError("target applicability fixture primary Actor is unavailable")
        GenericGameService(db, scope).execute(
            actor_key=actor.actor_key,
            action_key="inspect",
            target_node_key=target_key,
            parameters={},
        )
        db.commit()
        return {"gameId": game_id, "targetKey": target_key}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_fixture(kind: str) -> dict[str, str]:
    if kind not in {"presentation", "fork"}:
        raise ValueError(f"unsupported fixture kind: {kind}")

    db = SessionLocal()
    try:
        scenario, version = ensure_generic_scenario(db)
        definition = ScenarioVersionRepository(db).load(version.id).definition
        if not definition.objectives:
            raise RuntimeError("generic E2E Scenario has no authored objective")
        objective = definition.objectives[0]
        action = definition.actions[0]
        target = next(
            (item for item in definition.world.nodes if item.facts),
            definition.world.nodes[0],
        )
        outcome_code = action.expected_outcomes[0].code if action.expected_outcomes else "SUCCESS"

        runtime = GameLifecycleService(db).create(
            scenario_version_id=version.id,
            idempotency_key=f"browser-smoke-{kind}-{uuid4()}",
        )
        db.flush()
        task = GenericAgentService(
            db,
            GameInstanceService(db).load(GameInstanceId(runtime.instance.id)),
        ).create_task(
            runtime.session,
            "E2E presentation history",
            resolved_goal=GenericGoalResolution(
                "RESOLVED",
                objective.key,
                (objective.key,),
            ),
            initialize_plan=False,
        )
        now = datetime.now(UTC)
        plan = AgentPlan(
            task_id=task.id,
            version=1,
            status=(
                AgentPlanStatus.ACTIVE if kind == "presentation" else AgentPlanStatus.SUCCEEDED
            ),
            strategy_summary="Deterministic browser presentation fixture",
            replan_reason=None,
            supersedes_plan_id=None,
            created_by_run_id=None,
            created_by_actor_key=runtime.session.actor_key,
            source="E2E_FIXTURE",
            planner_model=None,
            validation_status="PASSED",
            validation_errors=[],
            stop_reason="INFORMATION_BOUNDARY",
            created_at=now,
        )
        db.add(plan)
        db.flush()
        step = AgentStep(
            plan_id=plan.id,
            sequence=1,
            planner_step_id="e2e-inspect-step",
            description=action.name,
            execution_type=StepExecutionType.TOOL,
            status=AgentStepStatus.SUCCEEDED,
            assigned_actor_key=runtime.session.actor_key,
            action_intent=action.key,
            constraints={"fixture": True},
            allowed_tool_names=["execute_action"],
            selected_tool_name="execute_action",
            tool_arguments={
                "action_key": action.key,
                "target_key": target.key,
                "parameters": {},
            },
            expected_outcome={"outcome_code": outcome_code},
            actual_result={"success": True},
            attempts=1,
            started_at=now,
            completed_at=now,
        )
        db.add(step)
        db.flush()

        changes = [
            {
                "kind": "FACT_REVEALED",
                "key": f"{target.key}.{fact.key}",
                "name": fact.name,
                "value": fact.model_dump(mode="json").get("initial_value"),
            }
            for fact in target.facts[:2]
        ]
        db.add(
            WorldOperation(
                player_id=runtime.instance.player_id,
                game_instance_id=runtime.instance.id,
                task_id=task.id,
                source_step_id=step.id,
                actor_key=runtime.session.actor_key,
                action_key=action.key,
                execution_mode="IMMEDIATE",
                target_key=target.key,
                status=WorldOperationStatus.RESOLVED,
                parameters={},
                outcome={
                    "success": True,
                    "outcome_code": outcome_code,
                    "knowledge_changes": changes,
                },
                idempotency_key=f"browser-smoke-operation-{uuid4()}",
                resolved_at=now,
            )
        )
        for fact in target.facts[:2]:
            fact_state = db.get(
                GameInstanceFactState,
                (runtime.instance.id, target.key, fact.key),
            )
            if fact_state is not None:
                fact_state.visibility = Visibility.KNOWN

        task.current_plan_version = 1
        if kind == "presentation":
            task.status = AgentTaskStatus.WAITING_FOR_PLAYER_ACTION
            db.add(
                PlayerExecutionCheckpoint(
                    task_id=task.id,
                    game_instance_id=runtime.instance.id,
                    phase="AWAITING_DEBRIEF_ACK",
                    last_action_step_id=step.id,
                    version=1,
                )
            )
        else:
            task.status = AgentTaskStatus.SUCCEEDED
            task.completed_at = now
        db.flush()
        db.commit()
        return {
            "gameId": str(runtime.instance.id),
            "scenarioName": scenario.name,
            "targetName": target.name,
            "actionName": action.name,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def append_post_fork_task(game_id: str) -> dict[str, str]:
    """Add one deterministic post-fork task without invoking planning."""

    db = SessionLocal()
    try:
        game = db.get(GameInstance, UUID(game_id))
        if game is None:
            raise RuntimeError("forked fixture GameInstance is unavailable")
        session = db.scalar(
            select(ConversationSession)
            .where(ConversationSession.game_instance_id == game.id)
            .order_by(ConversationSession.created_at, ConversationSession.id)
        )
        if session is None:
            raise RuntimeError("forked fixture ConversationSession is unavailable")
        version = db.get(ScenarioVersion, game.scenario_version_id)
        if version is None:
            raise RuntimeError("forked generic ScenarioVersion is unavailable")
        definition = ScenarioVersionRepository(db).load(version.id).definition
        objective = definition.objectives[0]
        task = GenericAgentService(
            db,
            GameInstanceService(db).load(GameInstanceId(game.id)),
        ).create_task(
            session,
            "E2E post-fork task",
            resolved_goal=GenericGoalResolution(
                "RESOLVED",
                objective.key,
                (objective.key,),
            ),
            initialize_plan=False,
        )
        task.status = AgentTaskStatus.WAITING_FOR_PLAYER_ACTION
        db.commit()
        return {"taskId": str(task.id)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "scenario":
        db = SessionLocal()
        try:
            ensure_generic_scenario(db)
        finally:
            db.close()
        return
    if len(sys.argv) == 2 and sys.argv[1] == "portability":
        db = SessionLocal()
        try:
            scenario, version = ensure_portability_scenario(db)
            ensure_empty_quick_input_scenario(db)
            print(
                json.dumps(
                    {
                        "scenarioId": str(scenario.id),
                        "key": scenario.key,
                        "versionId": str(version.id),
                    }
                )
            )
        finally:
            db.close()
        return
    if len(sys.argv) == 2 and sys.argv[1] in {"presentation", "fork"}:
        print(json.dumps(create_fixture(sys.argv[1])))
        return
    if len(sys.argv) == 2 and sys.argv[1] == "target":
        print(json.dumps(create_target_applicability_fixture()))
        return
    if len(sys.argv) == 4 and sys.argv[1] == "target-reveal":
        print(json.dumps(reveal_target_contract_fixture(sys.argv[2], sys.argv[3])))
        return
    if len(sys.argv) == 3 and sys.argv[1] == "append":
        print(json.dumps(append_post_fork_task(sys.argv[2])))
        return
    raise ValueError(
        "usage: prepare_history_fixture.py scenario|portability|presentation|fork|target | "
        "target-reveal GAME_ID TARGET_KEY | append GAME_ID"
    )


if __name__ == "__main__":
    main()
