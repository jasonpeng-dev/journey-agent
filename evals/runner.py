from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any, cast
from uuid import UUID

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.planning import PlanValidator
from app.agent.providers import MockModelProvider
from app.agent.task_orchestrator import TaskOrchestrator
from app.core.config import Settings
from app.domain.enums import AgentTaskStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    NPC,
    AgentPlan,
    AgentRun,
    AgentStep,
    ConversationSession,
    ToolExecution,
)
from app.scenarios.starfire.fallback_plans import initial_strategic_starfire_plan
from app.scenarios.starfire.objective_catalog import FULL_STARFIRE_SCOPE
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def load_scenarios(path: Path | None = None) -> list[dict[str, Any]]:
    scenario_path = path or Path(__file__).parent / "scenarios" / "core.yaml"
    loaded = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("Evaluation scenarios must be a list")
    return [dict(item) for item in loaded]


def run_evaluations() -> dict[str, object]:
    scenarios = load_scenarios()
    started = perf_counter()
    workflow_goals = {
        str(scenario.get("goal", _FULL_RECOVERY_GOAL))
        for scenario in scenarios
        if scenario["kind"] in {"workflow", "scoped_workflow"}
    }
    workflows = {goal: asyncio.run(_run_mock_workflow(goal)) for goal in sorted(workflow_goals)}
    results: list[dict[str, object]] = []
    for scenario in scenarios:
        case_started = perf_counter()
        if scenario["kind"] in {"workflow", "scoped_workflow"}:
            workflow = workflows[str(scenario.get("goal", _FULL_RECOVERY_GOAL))]
            passed, actual = _evaluate_workflow_assertion(
                workflow,
                str(scenario["assertion"]),
                scenario,
            )
            expected = str(scenario["assertion"])
        else:
            passed, actual = _run_plan_validation_case(scenario)
            expected = str(scenario["expected_code"])
        results.append(
            {
                "name": str(scenario["name"]),
                "category": str(scenario["category"]),
                "passed": passed,
                "expected": expected,
                "actual": actual,
                "latency_ms": round((perf_counter() - case_started) * 1000, 2),
            }
        )
    passed_count = sum(bool(result["passed"]) for result in results)
    return {
        "summary": {
            "mode": "strategic_mock_evaluation",
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "pass_rate": passed_count / len(results) if results else 0.0,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
        },
        "results": results,
    }


_FULL_RECOVERY_GOAL = "Full northern recovery"


async def _run_mock_workflow(goal: str = _FULL_RECOVERY_GOAL) -> dict[str, Any]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        model_provider="mock",
        model_name="mock-model",
        model_api_key=None,
    )
    with session_factory() as db:
        seed_demo_world(db)
        player = GameService(db).create_player("Evaluation Lord")
        player.level = 2
        player.gold = 80
        conversation = ConversationSession(
            player_id=player.id,
            npc_id=seed_id("npc:shen_ce"),
        )
        db.add(conversation)
        db.commit()
        orchestrator = TaskOrchestrator(db, MockModelProvider(), settings)
        task, _run, _event = await orchestrator.start(
            conversation,
            goal,
            "starfire_command",
        )
        approvals = 0
        transitions: list[str] = []
        for index in range(80):
            task = TaskService(db).get_task(task.id)
            if task.status in {AgentTaskStatus.SUCCEEDED, AgentTaskStatus.BLOCKED}:
                break
            serialized = TaskService(db).serialize(task)
            if task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
                decision = serialized["pending_decision"]
                assert isinstance(decision, dict)
                TaskService(db).resolve_player_decision(
                    task,
                    UUID(str(decision["id"])),
                    "APPROVE",
                )
                approvals += 1
                db.commit()
                continue
            if task.status == AgentTaskStatus.WAITING_FOR_WORLD_EVENT:
                operation = serialized["pending_world_event"]
                if isinstance(operation, dict):
                    GameService(db).resolve_world_operation(
                        UUID(str(operation["id"])),
                        f"evaluation-resolution-{index:02d}",
                    )
                    db.commit()
                    continue
            task, _run, event = await orchestrator.advance(task.id, conversation)
            transitions.append(event)
        task = TaskService(db).get_task(task.id)
        plans = db.scalars(
            select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
        ).all()
        steps = db.scalars(
            select(AgentStep)
            .join(AgentPlan, AgentPlan.id == AgentStep.plan_id)
            .where(AgentPlan.task_id == task.id)
        ).all()
        officer_ids = {step.assigned_npc_id for step in steps if step.assigned_npc_id is not None}
        officers = db.scalars(select(NPC).where(NPC.id.in_(officer_ids))).all()
        executions = db.scalars(
            select(ToolExecution)
            .join(AgentStep, AgentStep.id == ToolExecution.step_id)
            .join(AgentPlan, AgentPlan.id == AgentStep.plan_id)
            .where(AgentPlan.task_id == task.id)
        ).all()
        planning_runs = db.scalars(
            select(AgentRun)
            .where(AgentRun.task_id == task.id, AgentRun.purpose.in_(["PLAN", "REPLAN"]))
            .order_by(AgentRun.started_at)
        ).all()
        return {
            "goal": goal,
            "task_status": task.status.value,
            "last_error_code": task.last_error_code,
            "objective_scope": list(TaskService(db).require_frozen_scope(task).objective_keys),
            "plan_count": len(plans),
            "replan_reasons": [plan.replan_reason for plan in plans if plan.replan_reason],
            "officers": {officer.key for officer in officers},
            "approvals": approvals,
            "world": GameService(db).inspect_command_state(player.id)["world"],
            "state_audits": sum(
                execution.before_state is not None and execution.after_state is not None
                for execution in executions
            ),
            "transitions": transitions,
            "planning_errors": [run.validation_errors for run in planning_runs],
            "step_failures": [
                {
                    "tool": step.selected_tool_name,
                    "code": step.failure_code,
                    "result": step.actual_result,
                }
                for step in steps
                if step.failure_code is not None
            ],
            "selected_tools": sorted(
                {
                    str(step.selected_tool_name)
                    for step in steps
                    if step.selected_tool_name is not None
                }
            ),
        }


def _evaluate_workflow_assertion(
    workflow: dict[str, Any],
    assertion: str,
    scenario: dict[str, Any],
) -> tuple[bool, str]:
    world = workflow["world"]
    expected_scope = [str(key) for key in scenario.get("expected_scope", [])]
    forbidden_tools = {str(name) for name in scenario.get("forbidden_tools", [])}
    selected_tools = set(workflow["selected_tools"])
    checks = {
        "task_succeeded": workflow["task_status"] == "SUCCEEDED",
        "replan_created": (
            workflow["plan_count"] >= 2 and "ENCOUNTER_DEFEAT" in workflow["replan_reasons"]
        ),
        "all_officers_assigned": workflow["officers"] == {"shen_ce", "han_lie", "lu_ning"},
        "player_approval_observed": workflow["approvals"] >= 1,
        "final_world_verified": (
            world.get("valley_security") == "SAFE"
            and world.get("starfire_outpost_status") in {"OPERATIONAL", "RESTORED"}
            and world.get("northern_trade_route_status") == "OPEN"
        ),
        "state_audit_recorded": workflow["state_audits"] > 0,
        "scope_succeeded_without_extra_tools": (
            workflow["task_status"] == "SUCCEEDED"
            and workflow["objective_scope"] == expected_scope
            and selected_tools.isdisjoint(forbidden_tools)
        ),
    }
    passed = bool(checks.get(assertion, False))
    return passed, assertion if passed else json.dumps(workflow, ensure_ascii=False, default=list)


def _run_plan_validation_case(scenario: dict[str, Any]) -> tuple[bool, str]:
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_world(db)
        player = GameService(db).create_player("Plan Evaluation Lord")
        conversation = ConversationSession(
            player_id=player.id,
            npc_id=seed_id("npc:shen_ce"),
        )
        db.add(conversation)
        db.flush()
        tasks = TaskService(db)
        task = tasks.create_task(
            conversation,
            "修复星火前哨并重新打通北方商路。",
            "starfire_command",
        )
        tasks.resolve_and_freeze_scope(
            task,
            FULL_STARFIRE_SCOPE,
            resolver_source="EVAL_FIXTURE",
            resolver_version="v1",
            confirmation_source="EVAL_FIXTURE",
            freeze_source="EVAL_FIXTURE",
        )
        proposal = deepcopy(initial_strategic_starfire_plan(task.id, FULL_STARFIRE_SCOPE))
        tool_name = "create_task_plan"
        mutation = str(scenario["mutation"])
        operation = next(
            step
            for step in proposal["steps"]
            if step["selected_tool_name"] == "start_recon_operation"
        )
        military_index = next(
            index
            for index, step in enumerate(proposal["steps"])
            if step["selected_tool_name"] == "start_military_operation"
        )
        if mutation == "unknown_tool":
            operation["selected_tool_name"] = "patch_database"
        elif mutation == "unauthorized_officer":
            proposal["steps"][military_index]["assigned_officer_key"] = "lu_ning"
        elif mutation == "invalid_world_outcome":
            wait = proposal["steps"][military_index + 1]
            wait["resume_condition"]["success_outcomes"] = ["DEFEAT"]
            wait["expected_outcome"] = {"operation_result_in": ["DEFEAT"]}
        elif mutation == "broken_operation_pair":
            wait = proposal["steps"].pop(1)
            proposal["steps"].insert(3, wait)
        elif mutation == "model_idempotency_key":
            operation["tool_arguments"]["idempotency_key"] = "model-owned-key"
        elif mutation == "missing_final_verification":
            proposal["steps"][-1]["action_intent"] = "REPORT_ONLY"
        elif mutation == "invalid_operation_type":
            operation["expected_outcome"]["operation_type"] = "MILITARY"
        elif mutation == "wrong_submission_tool":
            tool_name = "replan_task"
        result = PlanValidator(
            db,
            build_registry(),
            Settings(database_url="sqlite+pysqlite:///:memory:"),
        ).validate(
            task=task,
            session=conversation,
            tool_name=tool_name,
            arguments=proposal,
        )
        codes = {issue.code for issue in result.errors}
        expected = str(scenario["expected_code"])
        return expected in codes, ",".join(sorted(codes)) or result.status


def write_reports(report: dict[str, object], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = cast(dict[str, Any], report["summary"])
    results = cast(list[dict[str, Any]], report["results"])
    lines = [
        "# Strategic Mock Evaluation",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        "",
        "| Scenario | Category | Result | Actual |",
        "|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['name']} | {item['category']} | "
            f"{'PASS' if item['passed'] else 'FAIL'} | {item['actual']} |"
        )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
