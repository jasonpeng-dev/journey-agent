from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.providers import build_provider
from app.agent.task_orchestrator import TaskOrchestrator
from app.core.config import Settings
from app.domain.enums import AgentTaskStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import NPC, ConversationSession
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService


@dataclass
class RealStrategicResult:
    attempt: int
    goal: str
    passed: bool
    task_status: str
    plan_source: str
    validation_status: str
    error_code: str | None
    latency_ms: float
    rounds: int
    token_usage: int
    step_count: int
    assigned_officers: list[str]
    selected_tools: list[str]
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
            "mode": "real_strategic_plan",
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
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    started = perf_counter()
    with factory() as db:
        _seed_trial(db, attempt)
        session = db.scalar(
            select(ConversationSession).where(ConversationSession.npc_id == seed_id("npc:shen_ce"))
        )
        if session is None:
            raise RuntimeError("Strategic trial session was not created")
        task, run, _ = await TaskOrchestrator(
            db,
            build_provider(settings),
            settings,
        ).start(
            session,
            GOAL,
            "starfire_command",
            planning_mode="PROVIDER",
        )
        plan = TaskService(db).current_plan(task)
        steps = TaskService(db).plan_steps(plan.id) if plan is not None else []
        assigned_officers = sorted(
            {
                officer.key
                for step in steps
                if step.assigned_npc_id is not None
                for officer in [db.get(NPC, step.assigned_npc_id)]
                if officer is not None
            }
        )
        selected_tools = [
            str(step.selected_tool_name) for step in steps if step.selected_tool_name is not None
        ]
        diagnostic = None
        if run is not None and run.validation_errors:
            first_error = run.validation_errors[0]
            if isinstance(first_error, dict) and first_error.get("message"):
                diagnostic = str(first_error["message"])
        validation_rounds = []
        if run is not None:
            validation_rounds = [
                {
                    "round": item.get("round"),
                    "token_usage": item.get("token_usage"),
                    "validation_status": item.get("plan_validation_status"),
                    "errors": item.get("plan_validation_errors", []),
                    "proposal_summary": _proposal_summary(item.get("proposal")),
                }
                for item in run.model_rounds
                if isinstance(item, dict)
            ]
        plan_source = plan.source if plan is not None else "NOT_CREATED"
        validation_status = plan.validation_status if plan is not None else "NOT_RECORDED"
        passed = bool(
            run is not None
            and run.status.value == "COMPLETED"
            and task.status == AgentTaskStatus.ACTIVE
            and plan is not None
            and plan_source == "MODEL_PLANNER"
            and validation_status == "PASSED"
            and len(steps) >= 1
            and len(assigned_officers) >= 2
        )
        return RealStrategicResult(
            attempt=attempt,
            goal=GOAL,
            passed=passed,
            task_status=task.status.value,
            plan_source=plan_source,
            validation_status=validation_status,
            error_code=task.last_error_code,
            latency_ms=(perf_counter() - started) * 1000,
            rounds=run.actual_rounds if run is not None else 0,
            token_usage=run.token_usage if run is not None else 0,
            step_count=len(steps),
            assigned_officers=assigned_officers,
            selected_tools=selected_tools,
            diagnostic=diagnostic,
            validation_rounds=validation_rounds,
        )


def _seed_trial(db: Session, attempt: int) -> None:
    with db.begin():
        seed_demo_world(db)
        player = GameService(db).create_player(f"DeepSeek Strategic {attempt}")
        player.level = 2
        player.gold = 80
        shen_ce = db.get(NPC, seed_id("npc:shen_ce"))
        if shen_ce is None:
            raise RuntimeError("Strategic officer content has not been seeded")
        db.add(ConversationSession(player_id=player.id, npc_id=shen_ce.id))


def write_real_strategic_report(report: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = report["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# DeepSeek Strategic Plan Smoke Test",
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
    execution_types: list[object] = []
    selected_tools: list[object] = []
    non_object_fields: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            non_object_fields.append(f"steps.{index}")
            continue
        execution_types.append(step.get("execution_type"))
        selected_tools.append(step.get("selected_tool_name"))
        for field in ("constraints", "tool_arguments", "expected_outcome", "resume_condition"):
            field_value = step.get(field)
            if field_value is not None and not isinstance(field_value, dict):
                non_object_fields.append(f"steps.{index}.{field}")
    return {
        "step_count": len(steps),
        "execution_types": execution_types,
        "selected_tools": selected_tools,
        "non_object_fields": non_object_fields,
    }
