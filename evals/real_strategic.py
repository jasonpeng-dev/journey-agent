from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.providers import build_provider
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
)
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService


@dataclass
class RealStrategicResult:
    attempt: int
    goal: str
    passed: bool
    task_status: str
    initial_plan_source: str
    final_plan_source: str
    validation_status: str
    error_code: str | None
    latency_ms: float
    rounds: int
    token_usage: int
    step_count: int
    assigned_officers: list[str]
    selected_tools: list[str]
    replan_count: int
    replan_reasons: list[str]
    approvals: int
    resolved_world_events: int
    final_world: dict[str, object]
    diagnostic: str | None
    validation_rounds: list[dict[str, object]]


GOAL = "修复星火前哨并重新打通北方商路。"


def run_real_strategic_evaluations(
    settings: Settings,
    *,
    attempts: int = 1,
) -> dict[str, object]:
    if settings.model_provider != "openai_compatible":
        raise ValueError("Real strategic evaluation requires MODEL_PROVIDER=openai_compatible")
    results = [asyncio.run(_run_trial(settings, index + 1)) for index in range(attempts)]
    passed = sum(result.passed for result in results)
    return {
        "summary": {
            "mode": "real_strategic_e2e",
            "provider": settings.model_provider,
            "model": settings.model_name,
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": passed / len(results) if results else 0,
            "average_latency_ms": mean(result.latency_ms for result in results) if results else 0,
            "average_rounds": mean(result.rounds for result in results) if results else 0,
            "average_token_usage": mean(result.token_usage for result in results) if results else 0,
        },
        "results": [asdict(result) for result in results],
    }


async def _run_trial(settings: Settings, attempt: int) -> RealStrategicResult:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    started = perf_counter()
    with factory() as db:
        session = _seed_trial(db, attempt)
        orchestrator = TaskOrchestrator(db, build_provider(settings), settings)
        task, _initial_run, _event = await orchestrator.start(
            session,
            GOAL,
            "starfire_command",
            planning_mode="PROVIDER",
        )
        approvals = 0
        resolved_world_events = 0
        for index in range(100):
            task = TaskService(db).get_task(task.id)
            if task.status in {AgentTaskStatus.SUCCEEDED, AgentTaskStatus.BLOCKED}:
                break
            serialized = TaskService(db).serialize(task)
            if task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
                decision = serialized["pending_decision"]
                if not isinstance(decision, dict):
                    break
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
                        f"deepseek-e2e-resolution-{attempt}-{index:02d}",
                    )
                    resolved_world_events += 1
                    db.commit()
                    continue
            task, _run, _event = await orchestrator.advance(task.id, session)

        task = TaskService(db).get_task(task.id)
        plans = list(
            db.scalars(
                select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
            ).all()
        )
        steps = list(
            db.scalars(
                select(AgentStep)
                .join(AgentPlan, AgentPlan.id == AgentStep.plan_id)
                .where(AgentPlan.task_id == task.id)
                .order_by(AgentPlan.version, AgentStep.sequence)
            ).all()
        )
        runs = list(
            db.scalars(
                select(AgentRun).where(AgentRun.task_id == task.id).order_by(AgentRun.started_at)
            ).all()
        )
        officers = {
            officer.key
            for step in steps
            if step.assigned_npc_id is not None
            for officer in [db.get(NPC, step.assigned_npc_id)]
            if officer is not None
        }
        selected_tools = sorted(
            {str(step.selected_tool_name) for step in steps if step.selected_tool_name}
        )
        planning_runs = [run for run in runs if run.purpose in {"PLAN", "REPLAN"}]
        validation_rounds = [
            {
                "purpose": run.purpose,
                "round": item.get("round"),
                "token_usage": item.get("token_usage"),
                "validation_status": item.get("plan_validation_status"),
                "errors": item.get("plan_validation_errors", []),
                "proposal_summary": _proposal_summary(item.get("proposal")),
            }
            for run in planning_runs
            for item in run.model_rounds
            if isinstance(item, dict)
        ]
        diagnostic = _diagnostic(task.last_error_code, planning_runs)
        world = GameService(db).inspect_command_state(task.player_id)["world"]
        assert isinstance(world, dict)
        model_replan_created = any(
            plan.version > 1 and plan.source == "MODEL_PLANNER" for plan in plans
        )
        passed = bool(
            task.status == AgentTaskStatus.SUCCEEDED
            and plans
            and plans[0].source == "MODEL_PLANNER"
            and model_replan_created
            and task.replan_count >= 1
            and "ENCOUNTER_DEFEAT" in {plan.replan_reason for plan in plans}
            and {"shen_ce", "han_lie", "lu_ning"}.issubset(officers)
            and approvals >= 1
            and world.get("valley_security") == "SAFE"
            and world.get("starfire_outpost_status") in {"OPERATIONAL", "RESTORED"}
            and world.get("northern_trade_route_status") == "OPEN"
        )
        final_plan = plans[-1] if plans else None
        return RealStrategicResult(
            attempt=attempt,
            goal=GOAL,
            passed=passed,
            task_status=task.status.value,
            initial_plan_source=plans[0].source if plans else "NOT_CREATED",
            final_plan_source=final_plan.source if final_plan else "NOT_CREATED",
            validation_status=(
                final_plan.validation_status if final_plan is not None else "NOT_RECORDED"
            ),
            error_code=task.last_error_code,
            latency_ms=(perf_counter() - started) * 1000,
            rounds=sum(run.actual_rounds for run in runs),
            token_usage=sum(run.token_usage for run in runs),
            step_count=len(steps),
            assigned_officers=sorted(officers),
            selected_tools=selected_tools,
            replan_count=task.replan_count,
            replan_reasons=[
                str(plan.replan_reason) for plan in plans if plan.replan_reason is not None
            ],
            approvals=approvals,
            resolved_world_events=resolved_world_events,
            final_world={str(key): value for key, value in world.items()},
            diagnostic=diagnostic,
            validation_rounds=validation_rounds,
        )


def _seed_trial(db: Session, attempt: int) -> ConversationSession:
    seed_demo_world(db)
    player = GameService(db).create_player(f"DeepSeek Strategic {attempt}")
    player.level = 2
    player.gold = 80
    shen_ce = db.get(NPC, seed_id("npc:shen_ce"))
    if shen_ce is None:
        raise RuntimeError("Strategic officer content has not been seeded")
    session = ConversationSession(player_id=player.id, npc_id=shen_ce.id)
    db.add(session)
    db.commit()
    return session


def _diagnostic(error_code: str | None, runs: list[AgentRun]) -> str | None:
    for run in reversed(runs):
        if run.validation_errors:
            first = run.validation_errors[0]
            if isinstance(first, dict) and first.get("message"):
                return str(first["message"])
    return error_code


def write_real_strategic_report(report: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = report["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# DeepSeek Strategic E2E",
        "",
        f"- Model: {summary['model']}",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Pass rate: {summary['pass_rate']:.1%}",
        f"- Average latency: {summary['average_latency_ms']:.2f} ms",
        f"- Average rounds: {summary['average_rounds']:.2f}",
        f"- Average token usage: {summary['average_token_usage']:.2f}",
    ]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _proposal_summary(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    steps = value.get("steps")
    if not isinstance(steps, list):
        return {"step_count": 0, "steps_type": type(steps).__name__}
    return {
        "step_count": len(steps),
        "execution_types": [step.get("execution_type") for step in steps if isinstance(step, dict)],
        "selected_tools": [
            step.get("selected_tool_name") for step in steps if isinstance(step, dict)
        ],
        "non_object_fields": [
            f"steps.{index}" for index, step in enumerate(steps) if not isinstance(step, dict)
        ],
    }
