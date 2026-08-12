from __future__ import annotations

import asyncio
import json
import os
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
from app.agent.types import Message, ModelProvider, ModelResponse, ToolDefinition
from app.core.config import Settings
from app.domain.enums import AgentStepStatus, AgentTaskStatus, WorldOperationStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    NPC,
    AgentPlan,
    AgentRun,
    AgentStep,
    ConversationSession,
    WorldOperation,
)
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService


@dataclass
class RealStrategicResult:
    attempt: int
    goal: str
    objective_scope: list[str]
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
    plan_summaries: list[dict[str, object]]
    planning_context_audits: list[dict[str, object]]
    legacy_argument_uses: list[dict[str, object]]
    scoped_evaluation_completed: bool
    skipped_waits_due_to_scope: list[dict[str, object]]
    skipped_inspects_due_to_scope: list[dict[str, object]]
    settled_operation_wait_violations: list[dict[str, object]]


GOAL = "修复星火前哨并重新打通北方商路。"
RESTORE_ONLY_GOAL = "我要你帮我重建星火驿站"
_INITIAL_HIDDEN_TOKENS = (
    "ambush_status",
    "enemy_north_supply_route",
    "supply_status",
)
_LEGACY_ARGUMENT_KEYS = frozenset({"route_key"})
_LEGACY_TARGETS = frozenset({"valley_entrance", "ambush_valley"})


class AuditedProvider:
    """Record non-secret planner payload invariants around a real provider."""

    def __init__(self, provider: ModelProvider, settings: Settings):
        self._provider = provider
        self._settings = settings
        self.name = provider.name
        self.planning_context_audits: list[dict[str, object]] = []
        self._call_index = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        self._assert_payload_safe(messages, tools)
        self._audit_planning_context(messages, tools)
        return await self._provider.complete(messages, tools)

    def _assert_payload_safe(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> None:
        """Fail closed before the evaluation sends secrets or hidden initial truth."""
        payload = json.dumps(
            {
                "messages": [message.model_dump(mode="json") for message in messages],
                "tools": [tool.model_dump(mode="json") for tool in tools],
            },
            ensure_ascii=False,
        )
        forbidden_values = self._sensitive_local_values()
        if any(value and value in payload for value in forbidden_values):
            raise RuntimeError("Real evaluation payload safety check failed")

        marker_message = next(
            (
                message.content
                for message in messages
                if message.content is not None and "PLANNER_REQUEST_JSON:" in message.content
            ),
            None,
        )
        if marker_message is None:
            return
        raw_request = marker_message.split("PLANNER_REQUEST_JSON:", 1)[1]
        try:
            request = json.loads(raw_request)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Real evaluation planner payload was not valid JSON") from exc
        if request.get("kind") == "PLAN" and any(
            token in raw_request for token in _INITIAL_HIDDEN_TOKENS
        ):
            raise RuntimeError("Real evaluation initial payload contained hidden world data")

    def _sensitive_local_values(self) -> set[str]:
        api_key = self._settings.model_api_key
        values = {
            api_key.get_secret_value() if api_key is not None else "",
            self._settings.database_url,
            str(Path.cwd()),
            str(Path.home()),
            Path.home().name,
        }
        sensitive_name_parts = (
            "API_KEY",
            "TOKEN",
            "SECRET",
            "PASSWORD",
            "DATABASE_URL",
            "CONNECTION_STRING",
        )
        values.update(
            value
            for name, value in os.environ.items()
            if len(value) >= 8 and any(part in name.upper() for part in sensitive_name_parts)
        )
        return {value for value in values if len(value) >= 4}

    def _audit_planning_context(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> None:
        self._call_index += 1
        provider_payload = json.dumps(
            {
                "messages": [message.model_dump(mode="json") for message in messages],
                "tools": [tool.model_dump(mode="json") for tool in tools],
            },
            ensure_ascii=False,
        )
        marker_message = next(
            (
                message.content
                for message in messages
                if message.content is not None and "PLANNER_REQUEST_JSON:" in message.content
            ),
            None,
        )
        if marker_message is None:
            audit = self._payload_audit("OFFICER", provider_payload)
            audit["known_fact_values"] = self._officer_known_fact_values(messages)
            self.planning_context_audits.append(audit)
            return
        raw_request = marker_message.split("PLANNER_REQUEST_JSON:", 1)[1]
        try:
            request = json.loads(raw_request)
        except json.JSONDecodeError:
            self.planning_context_audits.append({"kind": "INVALID", "request_json_valid": False})
            return
        kind = str(request.get("kind", "UNKNOWN"))
        if any(item.get("kind") == kind for item in self.planning_context_audits):
            return
        constraints = request.get("constraints")
        canonical = constraints.get("canonical_facts", {}) if isinstance(constraints, dict) else {}
        audit = self._payload_audit(kind, provider_payload)
        audit.update(
            {
                "request_json_valid": True,
                "canonical_fact_keys": (
                    sorted(str(key) for key in canonical) if isinstance(canonical, dict) else []
                ),
                "known_fact_values": (
                    {str(key): str(value) for key, value in canonical.items()}
                    if isinstance(canonical, dict)
                    else {}
                ),
            }
        )
        self.planning_context_audits.append(audit)

    def _payload_audit(self, kind: str, provider_payload: str) -> dict[str, object]:
        return {
            "call_index": self._call_index,
            "kind": kind,
            "hidden_tokens_present": [
                token for token in _INITIAL_HIDDEN_TOKENS if token in provider_payload
            ],
            "supply_target_advertised": "enemy_north_supply_route" in provider_payload,
            "legacy_targets_present": [
                target for target in sorted(_LEGACY_TARGETS) if target in provider_payload
            ],
            "legacy_route_key_present": '"route_key"' in provider_payload,
        }

    @staticmethod
    def _officer_known_fact_values(messages: list[Message]) -> dict[str, str]:
        marker = "Verified player/agent knowledge (hidden truth is excluded): "
        suffix = ". You are executing one assigned step"
        content = next(
            (
                message.content
                for message in messages
                if message.content and marker in message.content
            ),
            None,
        )
        if content is None:
            return {}
        raw = content.split(marker, 1)[1].split(suffix, 1)[0]
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        world = state.get("world") if isinstance(state, dict) else None
        if not isinstance(world, dict):
            return {}
        audited_keys = {"ambush_status", "enemy_supply_route", "valley_security"}
        return {str(key): str(value) for key, value in world.items() if key in audited_keys}


def run_real_strategic_evaluations(
    settings: Settings,
    *,
    attempts: int = 1,
    goal: str = GOAL,
) -> dict[str, object]:
    if settings.model_provider != "openai_compatible":
        raise ValueError("Real strategic evaluation requires MODEL_PROVIDER=openai_compatible")
    results = [asyncio.run(_run_trial(settings, index + 1, goal=goal)) for index in range(attempts)]
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


async def _run_trial(
    settings: Settings,
    attempt: int,
    *,
    goal: str = GOAL,
) -> RealStrategicResult:
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
        provider = AuditedProvider(build_provider(settings), settings)
        orchestrator = TaskOrchestrator(db, provider, settings)
        task, _initial_run, _event = await orchestrator.start(
            session,
            goal,
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
        context_audits = provider.planning_context_audits
        initial_audit = next(
            (item for item in context_audits if item.get("kind") == "PLAN"),
            None,
        )
        replan_audit = next(
            (item for item in context_audits if item.get("kind") == "REPLAN"),
            None,
        )
        post_recon_audit = next(
            (
                item
                for item in context_audits
                if item.get("kind") == "OFFICER"
                and _audit_fact_value(item, "ambush_status") == "ACTIVE"
                and _audit_fact_value(item, "enemy_supply_route") is None
            ),
            None,
        )
        post_disrupt_audit = next(
            (
                item
                for item in context_audits
                if item.get("kind") == "OFFICER"
                and _audit_fact_value(item, "enemy_supply_route") == "DISRUPTED"
            ),
            None,
        )
        post_clear_audit = next(
            (
                item
                for item in context_audits
                if item.get("kind") == "OFFICER"
                and _audit_fact_value(item, "ambush_status") == "CLEARED"
                and _audit_fact_value(item, "valley_security") == "SAFE"
            ),
            None,
        )
        legacy_argument_uses = _legacy_argument_uses(plans, steps)
        scope = TaskService(db).require_frozen_scope(task)
        scoped_evaluation_completed = TaskService(db).evaluate_scope(task).completed
        skipped_waits_due_to_scope = _scope_skips(
            steps,
            execution_type="WAIT_FOR_WORLD_EVENT",
        )
        skipped_inspects_due_to_scope = _scope_skips(
            steps,
            selected_tool_name="inspect_command_state",
        )
        settled_operation_wait_violations = _settled_operation_wait_violations(
            db,
            task.id,
            steps,
        )
        common_passed = bool(
            task.status == AgentTaskStatus.SUCCEEDED
            and plans
            and plans[0].source == "MODEL_PLANNER"
            and model_replan_created
            and task.replan_count >= 1
            and "ENCOUNTER_DEFEAT" in {plan.replan_reason for plan in plans}
            and world.get("valley_security") == "SAFE"
            and world.get("starfire_outpost_status") in {"OPERATIONAL", "RESTORED"}
            and scoped_evaluation_completed
            and settled_operation_wait_violations == []
            and skipped_inspects_due_to_scope == []
            and initial_audit is not None
            and initial_audit.get("hidden_tokens_present") == []
            and initial_audit.get("supply_target_advertised") is False
            and initial_audit.get("legacy_targets_present") == []
            and initial_audit.get("legacy_route_key_present") is False
            and post_recon_audit is not None
            and replan_audit is not None
            and replan_audit.get("supply_target_advertised") is True
            and _audit_fact_value(
                replan_audit,
                "enemy_north_supply_route.supply_status",
            )
            == "ACTIVE"
            and _audit_token_set(replan_audit) == set(_INITIAL_HIDDEN_TOKENS)
            and replan_audit.get("legacy_targets_present") == []
            and replan_audit.get("legacy_route_key_present") is False
            and post_disrupt_audit is not None
            and post_clear_audit is not None
            and legacy_argument_uses == []
        )
        if goal == RESTORE_ONLY_GOAL:
            passed = bool(
                common_passed
                and scope.objective_keys == ("RESTORE_STARFIRE_OUTPOST",)
                and world.get("northern_trade_route_status") == "CLOSED"
            )
        else:
            passed = bool(
                common_passed
                and {"shen_ce", "han_lie", "lu_ning"}.issubset(officers)
                and approvals >= 1
                and world.get("northern_trade_route_status") == "OPEN"
            )
        final_plan = plans[-1] if plans else None
        return RealStrategicResult(
            attempt=attempt,
            goal=goal,
            objective_scope=list(scope.objective_keys),
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
            plan_summaries=_plan_summaries(db, plans, steps),
            planning_context_audits=context_audits,
            legacy_argument_uses=legacy_argument_uses,
            scoped_evaluation_completed=scoped_evaluation_completed,
            skipped_waits_due_to_scope=skipped_waits_due_to_scope,
            skipped_inspects_due_to_scope=skipped_inspects_due_to_scope,
            settled_operation_wait_violations=settled_operation_wait_violations,
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


def _audit_token_set(audit: dict[str, object]) -> set[str]:
    tokens = audit.get("hidden_tokens_present")
    return {str(token) for token in tokens} if isinstance(tokens, list) else set()


def _audit_fact_value(audit: dict[str, object], key: str) -> str | None:
    values = audit.get("known_fact_values")
    value = values.get(key) if isinstance(values, dict) else None
    return str(value) if value is not None else None


def _plan_summaries(
    db: Session,
    plans: list[AgentPlan],
    steps: list[AgentStep],
) -> list[dict[str, object]]:
    result = []
    for plan in plans:
        plan_steps = [step for step in steps if step.plan_id == plan.id]
        result.append(
            {
                "version": plan.version,
                "source": plan.source,
                "replan_reason": plan.replan_reason,
                "strategy_summary": plan.strategy_summary,
                "steps": [
                    {
                        "sequence": step.sequence,
                        "officer": (
                            officer.key
                            if step.assigned_npc_id is not None
                            and (officer := db.get(NPC, step.assigned_npc_id)) is not None
                            else None
                        ),
                        "execution_type": step.execution_type.value,
                        "tool": step.selected_tool_name,
                        "target_key": step.tool_arguments.get("target_key"),
                        "mission_type": step.tool_arguments.get("mission_type"),
                        "status": step.status.value,
                    }
                    for step in plan_steps
                ],
            }
        )
    return result


def _legacy_argument_uses(
    plans: list[AgentPlan],
    steps: list[AgentStep],
) -> list[dict[str, object]]:
    plan_versions = {plan.id: plan.version for plan in plans}
    uses: list[dict[str, object]] = []
    for step in steps:
        arguments = step.tool_arguments
        legacy_keys = sorted(key for key in arguments if key in _LEGACY_ARGUMENT_KEYS)
        target = arguments.get("target_key")
        legacy_target = target if isinstance(target, str) and target in _LEGACY_TARGETS else None
        if legacy_keys or legacy_target is not None:
            uses.append(
                {
                    "plan_version": plan_versions.get(step.plan_id),
                    "step_sequence": step.sequence,
                    "legacy_argument_keys": legacy_keys,
                    "legacy_target": legacy_target,
                }
            )
    return uses


def _scope_skips(
    steps: list[AgentStep],
    *,
    execution_type: str | None = None,
    selected_tool_name: str | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "plan_id": str(step.plan_id),
            "sequence": step.sequence,
            "execution_type": step.execution_type.value,
            "tool": step.selected_tool_name,
        }
        for step in steps
        if (execution_type is None or step.execution_type.value == execution_type)
        and (selected_tool_name is None or step.selected_tool_name == selected_tool_name)
        and step.actual_result == {"skip_reason": "OBJECTIVE_SCOPE_SATISFIED"}
    ]


def _settled_operation_wait_violations(
    db: Session,
    task_id: UUID,
    steps: list[AgentStep],
) -> list[dict[str, object]]:
    steps_by_source = {step.id: step for step in steps}
    steps_by_position = {(step.plan_id, step.sequence): step for step in steps}
    violations: list[dict[str, object]] = []
    operations = db.scalars(
        select(WorldOperation).where(
            WorldOperation.task_id == task_id,
            WorldOperation.status == WorldOperationStatus.RESOLVED,
        )
    ).all()
    for operation in operations:
        source = (
            steps_by_source.get(operation.source_step_id)
            if operation.source_step_id is not None
            else None
        )
        wait = (
            steps_by_position.get((source.plan_id, source.sequence + 1))
            if source is not None
            else None
        )
        result = operation.outcome.get("result") if isinstance(operation.outcome, dict) else None
        success_outcomes = (
            wait.resume_condition.get("success_outcomes", [])
            if wait is not None and isinstance(wait.resume_condition, dict)
            else []
        )
        if result in success_outcomes and (
            wait is None or wait.status != AgentStepStatus.SUCCEEDED
        ):
            violations.append(
                {
                    "operation_id": str(operation.id),
                    "source_step_id": str(operation.source_step_id),
                    "wait_step_id": str(wait.id) if wait is not None else None,
                    "wait_status": wait.status.value if wait is not None else "MISSING",
                }
            )
    return violations
