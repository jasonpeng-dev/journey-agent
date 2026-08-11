from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.providers import build_provider
from app.agent.task_orchestrator import TaskOrchestrator
from app.core.config import Settings
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import ConversationSession
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService


@dataclass
class RealPlannerResult:
    attempt: int
    goal: str
    passed: bool
    validation_status: str
    error_code: str | None
    latency_ms: float
    rounds: int
    token_usage: int
    step_count: int


GOALS = (
    "Restore Starfire Outpost and obtain safe access for the player.",
    "Help me secure the Broken Lantern Road, restore Starfire Outpost, and gain access.",
    "Create a safe, auditable plan to reopen Starfire Outpost after clearing its road.",
)


def run_real_planner_evaluations(
    settings: Settings,
    *,
    attempts: int = 3,
) -> dict[str, object]:
    if settings.model_provider != "openai_compatible":
        raise ValueError("Real planner evaluation requires MODEL_PROVIDER=openai_compatible")
    results = [
        asyncio.run(_run_trial(settings, index + 1, GOALS[index % len(GOALS)]))
        for index in range(attempts)
    ]
    passed = sum(result.passed for result in results)
    return {
        "summary": {
            "mode": "real_planner",
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


async def _run_trial(settings: Settings, attempt: int, goal: str) -> RealPlannerResult:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    started = perf_counter()
    with factory() as db:
        with db.begin():
            seed_demo_world(db)
            player = GameService(db).create_player(f"Real Planner {attempt}")
            session = ConversationSession(
                player_id=player.id,
                npc_id=seed_id("npc:captain_aria"),
            )
            db.add(session)
        task, run, _ = await TaskOrchestrator(
            db,
            build_provider(settings),
            settings,
        ).start(
            session,
            goal,
            "starfire_outpost",
            planning_mode="PROVIDER",
        )
        plan = TaskService(db).current_plan(task)
        return RealPlannerResult(
            attempt=attempt,
            goal=goal,
            passed=(
                plan is not None
                and plan.validation_status == "PASSED"
                and plan.source == "MODEL_PLANNER"
            ),
            validation_status=(
                run.validation_status or "NOT_RECORDED" if run is not None else "NOT_RUN"
            ),
            error_code=task.last_error_code,
            latency_ms=(perf_counter() - started) * 1000,
            rounds=run.actual_rounds if run is not None else 0,
            token_usage=run.token_usage if run is not None else 0,
            step_count=len(TaskService(db).plan_steps(plan.id)) if plan is not None else 0,
        )


def write_real_planner_report(report: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = report["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# Journey Agent Real Planner Evaluation",
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
