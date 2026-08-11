from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.planning import PlanValidator
from app.agent.providers import MockModelProvider
from app.agent.starfire_plans import initial_starfire_plan
from app.agent.task_orchestrator import TaskOrchestrator
from app.agent.task_router import TaskRouter
from app.agent.types import MockStep, ToolCall, ToolContext
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.enums import (
    AgentStepStatus,
    AgentTaskStatus,
    DecisionStatus,
    QuestStatus,
    RewardStatus,
    WorldOperationStatus,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    NPC,
    AgentRun,
    AgentStep,
    AgentTask,
    ConversationSession,
    PlayerDecisionRequest,
    Quest,
    QuestTemplate,
    WorldOperation,
)
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService
from app.tools.catalog import build_registry
from app.tools.executor import ToolExecutor


@dataclass
class EvalResult:
    name: str
    category: str
    passed: bool
    expected_code: str
    actual_code: str
    latency_ms: float


def load_scenarios(path: Path | None = None) -> list[dict[str, object]]:
    scenario_path = path or Path(__file__).parent / "scenarios" / "core.yaml"
    loaded = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("Scenario file must contain a list")
    return loaded


def run_evaluations() -> dict[str, object]:
    results = [_run_one(scenario) for scenario in load_scenarios()]
    passed = sum(result.passed for result in results)
    latencies = sorted(result.latency_ms for result in results)
    p95_index = max(0, int(len(latencies) * 0.95) - 1)
    categories: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = categories.setdefault(result.category, {"passed": 0, "total": 0})
        bucket["total"] += 1
        bucket["passed"] += int(result.passed)
    return {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": passed / len(results) if results else 0,
            "p95_latency_ms": latencies[p95_index] if latencies else 0,
            "categories": categories,
        },
        "results": [asdict(result) for result in results],
    }


def write_reports(report: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = report["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# Journey Agent Evaluation Report",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Pass rate: {summary['pass_rate']:.1%}",
        f"- P95 latency: {summary['p95_latency_ms']:.2f} ms",
    ]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_one(scenario: dict[str, object]) -> EvalResult:
    started = perf_counter()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        actual = _execute_scenario(db, scenario)
    expected = str(scenario["expected_code"])
    return EvalResult(
        name=str(scenario["name"]),
        category=str(scenario["category"]),
        passed=actual == expected,
        expected_code=expected,
        actual_code=actual,
        latency_ms=(perf_counter() - started) * 1000,
    )


def _execute_scenario(db: Session, scenario: dict[str, object]) -> str:
    with db.begin():
        seed_demo_world(db)
    workflow = scenario.get("workflow")
    if workflow:
        return _execute_task_workflow(db, str(workflow))
    planning_case = scenario.get("planning_case")
    if planning_case:
        return _execute_planning_case(db, str(planning_case))
    with db.begin():
        player = GameService(db).create_player("Evaluation Player")
        npc_key = str(scenario.get("npc_key", "guanyin"))
        conversation = ConversationSession(
            player_id=player.id,
            npc_id=seed_id(f"npc:{npc_key}"),
        )
        db.add(conversation)
        db.flush()
        run = AgentRun(
            request_id=uuid4(),
            session_id=conversation.id,
            model="eval-mock",
            input_message=str(scenario["name"]),
            max_rounds=1,
        )
        db.add(run)
    arguments = scenario.get("arguments", {})
    assert isinstance(arguments, dict)
    result = ToolExecutor(db, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=run.id,
            message_id=uuid4(),
        ),
        ToolCall(id=f"eval-{scenario['name']}", name=str(scenario["tool"]), arguments=arguments),
    )
    return result.code


def _execute_task_workflow(db: Session, workflow: str) -> str:
    if workflow in {"strategic_command_e2e", "strategic_command_provider"}:
        return _execute_strategic_command_workflow(
            db,
            planning_mode=(
                "PROVIDER" if workflow == "strategic_command_provider" else "DETERMINISTIC_BASELINE"
            ),
        )
    with db.begin():
        player = GameService(db).create_player(f"Eval-{workflow}")
        player.level = 1 if workflow in {"starfire_replan_resume", "starfire_dynamic_replan"} else 2
        conversation = ConversationSession(
            player_id=player.id,
            npc_id=seed_id("npc:captain_aria"),
        )
        db.add(conversation)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock")
    orchestrator = TaskOrchestrator(db, MockModelProvider(), settings)
    task, _, _ = asyncio.run(
        orchestrator.start(
            conversation,
            "Restore Starfire Outpost and obtain verified access.",
            "starfire_outpost",
            planning_mode=(
                "PROVIDER" if workflow == "starfire_dynamic_replan" else "DETERMINISTIC_BASELINE"
            ),
        )
    )
    if workflow == "task_wrong_npc_stop":
        with db.begin():
            wrong = ConversationSession(
                player_id=player.id,
                npc_id=seed_id("npc:guanyin"),
            )
            db.add(wrong)
        try:
            asyncio.run(orchestrator.advance(task.id, wrong))
        except AppError as exc:
            return exc.code
        return "UNSAFE_RESUME_ACCEPTED"
    for _ in range(4):
        asyncio.run(orchestrator.advance(task.id, conversation))
    first_result = _play_starfire_turn(db, player.id)
    if workflow in {"starfire_replan_resume", "starfire_dynamic_replan"}:
        if first_result != "DEFEAT":
            return "EXPECTED_DEFEAT_MISSING"
        asyncio.run(orchestrator.advance(task.id, conversation))
        for _ in range(3):
            asyncio.run(orchestrator.advance(task.id, conversation))
        with db.begin():
            resumed = ConversationSession(
                player_id=player.id,
                npc_id=seed_id("npc:captain_aria"),
            )
            db.add(resumed)
        if _play_starfire_turn(db, player.id) != "VICTORY":
            return "EXPECTED_VICTORY_MISSING"
        asyncio.run(orchestrator.advance(task.id, resumed))
        active_session = resumed
    else:
        if first_result != "VICTORY":
            return "EXPECTED_VICTORY_MISSING"
        asyncio.run(orchestrator.advance(task.id, conversation))
        active_session = conversation
    for _ in range(10):
        current = db.get(AgentTask, task.id)
        assert current is not None
        if current.status == AgentTaskStatus.SUCCEEDED:
            return "TASK_SUCCEEDED"
        asyncio.run(orchestrator.advance(task.id, active_session))
    return "TASK_DID_NOT_COMPLETE"


def _execute_strategic_command_workflow(
    db: Session,
    *,
    planning_mode: str,
) -> str:
    with db.begin():
        player = GameService(db).create_player(f"Eval-strategic-{planning_mode}")
        player.gold = 80
        conversation = ConversationSession(
            player_id=player.id,
            npc_id=seed_id("npc:shen_ce"),
        )
        db.add(conversation)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock")
    orchestrator = TaskOrchestrator(db, MockModelProvider(), settings)
    task, _, event = asyncio.run(
        orchestrator.start(
            conversation,
            "Restore Starfire Outpost and reopen the northern trade route.",
            "starfire_command",
            planning_mode=planning_mode,
        )
    )
    if event != "PLANNED":
        return "STRATEGIC_PLAN_NOT_CREATED"

    for _ in range(100):
        db.expire_all()
        current = db.get(AgentTask, task.id)
        assert current is not None
        if current.status == AgentTaskStatus.SUCCEEDED:
            return _verify_strategic_command(db, current, conversation)
        if current.status in {AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED}:
            return current.last_error_code or "STRATEGIC_TASK_STOPPED"
        if current.status == AgentTaskStatus.ACTIVE:
            asyncio.run(orchestrator.advance(current.id, conversation))
            continue
        if current.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
            decision = db.scalar(
                select(PlayerDecisionRequest).where(
                    PlayerDecisionRequest.task_id == current.id,
                    PlayerDecisionRequest.status == DecisionStatus.PENDING,
                )
            )
            if decision is None:
                return "PENDING_DECISION_MISSING"
            TaskService(db).resolve_player_decision(current, decision.id, "APPROVE")
            db.commit()
            continue
        if current.status == AgentTaskStatus.WAITING_FOR_WORLD_EVENT:
            operation = db.scalar(
                select(WorldOperation).where(
                    WorldOperation.task_id == current.id,
                    WorldOperation.status == WorldOperationStatus.PENDING,
                )
            )
            if operation is None:
                asyncio.run(orchestrator.advance(current.id, conversation))
            else:
                GameService(db).resolve_world_operation(
                    operation.id,
                    f"eval-resolve-{operation.id}",
                )
                db.commit()
            continue
        if current.status == AgentTaskStatus.WAITING_FOR_PLAYER_ACTION:
            return "UNEXPECTED_PLAYER_ACTION_WAIT"
        return f"UNEXPECTED_STRATEGIC_STATUS_{current.status.value}"
    return "STRATEGIC_TASK_TICK_LIMIT"


def _verify_strategic_command(
    db: Session,
    task: AgentTask,
    conversation: ConversationSession,
) -> str:
    service = TaskService(db)
    serialized = service.serialize(task)
    plans = serialized["plans"]
    if not isinstance(plans, list) or len(plans) != 2:
        return "STRATEGIC_REPLAN_LINEAGE_INVALID"
    first, second = plans
    if (
        first["status"] != "SUPERSEDED"
        or second["status"] != "SUCCEEDED"
        or second["supersedes_plan_id"] != first["id"]
        or second["replan_reason"] != "ENCOUNTER_DEFEAT"
    ):
        return "STRATEGIC_REPLAN_LINEAGE_INVALID"

    operations = list(
        db.scalars(
            select(WorldOperation)
            .where(WorldOperation.task_id == task.id)
            .order_by(WorldOperation.created_at)
        ).all()
    )
    if len(operations) != 6 or any(
        operation.status != WorldOperationStatus.RESOLVED for operation in operations
    ):
        return "STRATEGIC_WORLD_EVENTS_INVALID"
    if [operation.outcome["result"] for operation in operations if operation.outcome] != [
        "PARTIAL_SUCCESS",
        "DEFEAT",
        "VICTORY",
        "VICTORY",
        "COMPLETED",
        "COMPLETED",
    ]:
        return "STRATEGIC_WORLD_EVENTS_INVALID"

    decisions = list(
        db.scalars(
            select(PlayerDecisionRequest).where(PlayerDecisionRequest.task_id == task.id)
        ).all()
    )
    if (
        len(decisions) != 1
        or decisions[0].status != DecisionStatus.CONSUMED
        or decisions[0].selected_option != "APPROVE"
        or decisions[0].action_arguments.get("food_offer") != 35
    ):
        return "STRATEGIC_AUTHORITY_DECISION_INVALID"

    for run in db.scalars(select(AgentRun).where(AgentRun.task_id == task.id)).all():
        if run.session_id != conversation.id:
            return "STRATEGIC_SESSION_OWNERSHIP_INVALID"
        if run.step_id is not None:
            step = db.get(AgentStep, run.step_id)
            if step is None or run.actor_npc_id != step.assigned_npc_id:
                return "STRATEGIC_ACTOR_ASSIGNMENT_INVALID"
        elif run.purpose in {"PLAN", "REPLAN"}:
            actor = db.get(NPC, run.actor_npc_id) if run.actor_npc_id else None
            if actor is None or actor.key != "shen_ce":
                return "STRATEGIC_PLAN_OWNER_INVALID"

    state = GameService(db).inspect_command_state(task.player_id)
    world = state["world"]
    resources = state["resources"]
    if not isinstance(world, dict) or not isinstance(resources, dict):
        return "STRATEGIC_FINAL_STATE_INVALID"
    if (
        world.get("valley_security") != "SAFE"
        or world.get("enemy_supply_route") != "DISRUPTED"
        or world.get("village_support") != "GUIDE"
        or world.get("starfire_outpost_status") != "OPERATIONAL"
        or world.get("northern_trade_route_status") != "OPEN"
        or resources.get("soldiers_total") != 277
        or resources.get("soldiers_committed") != 0
        or resources.get("food") != 45
        or resources.get("gold") != 60
        or resources.get("morale") != 58
    ):
        return "STRATEGIC_FINAL_STATE_INVALID"
    return "STRATEGIC_COMMAND_VERIFIED"


def _execute_planning_case(db: Session, planning_case: str) -> str:
    if planning_case == "route_simple":
        return TaskRouter().route("What is the current Starfire Outpost status?").mode
    if planning_case == "route_complex":
        return TaskRouter().route("Help me restore Starfire Outpost and obtain safe access.").mode
    if planning_case == "route_strategic":
        route = TaskRouter().route("恢复星火驿站, 并重新开放北方商路。")
        return route.scenario_key or "NO_SCENARIO"
    with db.begin():
        player = GameService(db).create_player(f"Eval-planning-{planning_case}")
        npc_key = "guanyin" if planning_case == "unauthorized_tool" else "captain_aria"
        conversation = ConversationSession(
            player_id=player.id,
            npc_id=seed_id(f"npc:{npc_key}"),
        )
        db.add(conversation)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock")
    task = TaskService(db).create_task(
        conversation,
        "Restore Starfire Outpost and obtain verified access.",
        "starfire_outpost",
    )
    db.commit()
    if planning_case == "valid_mock":
        orchestrator = TaskOrchestrator(db, MockModelProvider(), settings)
        task, run, event = asyncio.run(
            orchestrator.start(
                conversation,
                task.goal_description,
                task.scenario_key,
            )
        )
        plan = TaskService(db).current_plan(task)
        if (
            event == "PLANNED"
            and run is not None
            and run.validation_status == "PASSED"
            and plan is not None
            and plan.source == "MOCK_PLANNER"
        ):
            return "PLAN_VALIDATED"
        return "PLAN_NOT_VALIDATED"
    if planning_case == "malformed_safe_stop":
        orchestrator = TaskOrchestrator(
            db,
            MockModelProvider(
                steps=[MockStep(content="invalid"), MockStep(content="still invalid")]
            ),
            settings,
        )
        task, run, _ = asyncio.run(
            orchestrator.start(
                conversation,
                task.goal_description,
                task.scenario_key,
            )
        )
        return (
            task.last_error_code or "PLANNING_FAILED_WITHOUT_CODE"
            if run is not None and TaskService(db).current_plan(task) is None
            else "UNSAFE_PLAN_ACCEPTED"
        )
    proposal = initial_starfire_plan(task.id)
    if planning_case == "multi_tool":
        return (
            "MULTI_TOOL_PLAN"
            if len(
                {
                    step["selected_tool_name"]
                    for step in proposal["steps"]
                    if step["selected_tool_name"] is not None
                }
            )
            >= 5
            else "PLAN_TOO_SIMPLE"
        )
    if planning_case == "unknown_tool":
        proposal["steps"][0]["selected_tool_name"] = "delete_database"
    elif planning_case == "unauthorized_tool":
        pass
    elif planning_case == "invalid_arguments":
        proposal["steps"][1]["tool_arguments"]["difficulty"] = "IMPOSSIBLE"
    elif planning_case == "step_limit":
        for index in range(3):
            extra = deepcopy(proposal["steps"][0])
            extra["description"] = f"Extra inspection {index}"
            proposal["steps"].append(extra)
    elif planning_case == "invalid_wait":
        proposal["steps"][3]["resume_condition"]["encounter_key"] = "missing_encounter"
    elif planning_case == "completed_write_repeat":
        initial = initial_starfire_plan(task.id)
        old_plan = TaskService(db).create_plan(
            task.id,
            initial["strategy_summary"],
            initial["steps"],
            created_by_run_id=uuid4(),
        )
        step = TaskService(db).plan_steps(old_plan.id)[1]
        step.status = AgentStepStatus.SUCCEEDED
        proposal["replan_reason"] = "ENCOUNTER_DEFEAT"
        proposal["idempotency_key"] = f"task-replan-{task.id}-v2"
        db.flush()
    validator = PlanValidator(db, build_registry(), settings)
    result = validator.validate(
        task=task,
        session=conversation,
        tool_name=(
            "replan_task" if planning_case == "completed_write_repeat" else "create_task_plan"
        ),
        arguments=proposal,
        replan_reason=("ENCOUNTER_DEFEAT" if planning_case == "completed_write_repeat" else None),
    )
    return result.errors[0].code if result.errors else "PLAN_VALIDATED"


def _play_starfire_turn(db: Session, player_id: UUID) -> str:
    service = GameService(db)
    player = service.get_player(player_id)
    template = db.scalar(select(QuestTemplate).where(QuestTemplate.key == "secure_starfire_road"))
    assert template is not None
    quest = db.scalar(
        select(Quest).where(Quest.player_id == player.id, Quest.template_id == template.id)
    )
    assert quest is not None
    if quest.status == QuestStatus.AVAILABLE:
        service.accept_quest(player.id, quest.id)
    road_id = seed_id("node:starfire_road")
    if player.current_node_id != road_id:
        service.enter_node(player.id, seed_id("node:starfire_crossroads"))
        service.enter_node(player.id, road_id)
    nonce = uuid4().hex
    run = service.start_encounter(
        player.id,
        seed_id("encounter:starfire_road_raiders"),
        f"eval-start-{nonce}",
    )
    run = service.attempt_encounter(run.id, "CAUTIOUS", f"eval-attempt-{nonce}")
    if run.result == "VICTORY":
        db.refresh(quest)
        if quest.reward_status == RewardStatus.ELIGIBLE:
            service.claim_reward(player.id, quest.id)
    db.commit()
    return run.result or "NO_RESULT"
