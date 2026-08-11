from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import authority_policy_errors, effective_authority_limits
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.domain.enums import AgentTaskStatus, SessionStatus
from app.infrastructure.db.models import (
    NPC,
    AgentPlan,
    AgentRun,
    AgentStep,
    AgentTask,
    ConversationMessage,
    ConversationSession,
    OfficerAppointment,
    PlayerDecisionRequest,
    PlayerDomainState,
    PlayerWorldFact,
    ToolExecution,
    WorldOperation,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService


class StrategicSnapshotService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def build(
        self,
        session_id: UUID,
        *,
        include_trace: bool,
        include_hidden_truth: bool,
    ) -> dict[str, object]:
        session = self._session(session_id)
        player = GameService(self.db).get_player(session.player_id)
        domain = self.db.get(PlayerDomainState, player.id)
        if domain is None:
            raise AppError("DOMAIN_STATE_NOT_INITIALIZED", "Strategic resources are missing")
        command_state = GameService(self.db).inspect_command_state(player.id)
        resources = dict(cast(dict[str, object], command_state["resources"]))
        world = dict(cast(dict[str, object], command_state["world"]))
        task = self._latest_task(player.id)
        serialized_task = TaskService(self.db).serialize(task) if task is not None else None
        trace = self._trace(task) if include_trace and task is not None else []
        messages = self._messages(session.id)
        officers = self._officers(player.id)
        known_world = self._known_world_state(player.id, world)
        snapshot_version = (
            f"p{player.version}-d{domain.version}-t{task.version if task is not None else 0}"
        )
        active_plan = None
        plan_history: list[dict[str, Any]] = []
        if isinstance(serialized_task, dict):
            plans = serialized_task.get("plans")
            if isinstance(plans, list):
                plan_history = plans
                active_plan = next(
                    (
                        plan
                        for plan in plans
                        if isinstance(plan, dict)
                        and plan.get("version") == serialized_task.get("current_plan_version")
                    ),
                    None,
                )
        active_decision = (
            serialized_task.get("pending_decision") if isinstance(serialized_task, dict) else None
        )
        pending_world_event = (
            serialized_task.get("pending_world_event")
            if isinstance(serialized_task, dict)
            else None
        )
        pending_player_action = (
            serialized_task.get("pending_player_action")
            if isinstance(serialized_task, dict)
            else None
        )
        status = serialized_task.get("status") if isinstance(serialized_task, dict) else None
        return {
            "runtime": {
                "environment": self.settings.app_env,
                "provider": self.settings.model_provider,
                "model": (
                    "mock-model"
                    if self.settings.model_provider == "mock"
                    else self.settings.model_name
                ),
            },
            "scenario": {
                "key": "starfire_command",
                "name": "Starfire Strategic Command",
            },
            "snapshot_version": snapshot_version,
            "player": {
                "id": str(player.id),
                "name": player.name,
                "level": player.level,
                "version": player.version,
            },
            "session": {
                "id": str(session.id),
                "status": session.status.value,
                "npc_id": str(session.npc_id),
                "commanding_officer": self._officer(session.npc_id),
            },
            "officers": officers,
            "resources": resources,
            "known_world_state": known_world,
            "hidden_world_truth": (
                self._hidden_world_truth(world) if include_hidden_truth else None
            ),
            "task": serialized_task,
            "active_plan": active_plan,
            "plan_history": plan_history,
            "active_decision": active_decision,
            "pending_player_action": pending_player_action,
            "pending_world_event": pending_world_event,
            "timeline": self._timeline(task, messages),
            "recent_messages": messages,
            "recent_traces": trace,
            "capabilities": {
                "can_issue_command": status in {None, "SUCCEEDED", "FAILED"},
                "can_resolve_decision": active_decision is not None,
                "can_resolve_world_event": (
                    isinstance(pending_world_event, dict)
                    and pending_world_event.get("status") == "PENDING"
                ),
                "shows_player_action": pending_player_action is not None,
            },
            "polling": {
                "recommended": status == AgentTaskStatus.ACTIVE.value,
                "interval_ms": 1800,
            },
        }

    def _session(self, session_id: UUID) -> ConversationSession:
        session = self.db.get(ConversationSession, session_id)
        if session is None:
            raise NotFoundError("session", session_id)
        if session.npc_id != seed_id("npc:shen_ce"):
            raise AppError(
                "STRATEGIC_SESSION_OWNER_INVALID",
                "The Strategic Command Console only accepts Shen Ce sessions",
                status_code=403,
            )
        if session.status != SessionStatus.ACTIVE:
            raise AppError("SESSION_CLOSED", "The strategic command session is closed")
        return session

    def _latest_task(self, player_id: UUID) -> AgentTask | None:
        return self.db.scalar(
            select(AgentTask)
            .where(
                AgentTask.player_id == player_id,
                AgentTask.scenario_key == "starfire_command",
            )
            .order_by(AgentTask.created_at.desc())
            .limit(1)
        )

    def _officer(self, officer_id: UUID) -> dict[str, object] | None:
        officer = self.db.get(NPC, officer_id)
        if officer is None:
            return None
        return {
            "id": str(officer.id),
            "key": officer.key,
            "name": officer.name,
            "role": officer.role.value,
        }

    def _officers(self, player_id: UUID) -> list[dict[str, object]]:
        rows = self.db.execute(
            select(OfficerAppointment, NPC)
            .join(NPC, NPC.id == OfficerAppointment.npc_id)
            .where(OfficerAppointment.player_id == player_id)
            .order_by(NPC.role, NPC.name)
        ).all()
        return [
            {
                **(self._officer(npc.id) or {}),
                "doctrine": npc.doctrine,
                "authority_limits": effective_authority_limits(
                    npc,
                    appointment.authority_overrides,
                ),
                "authority_policy_version": appointment.version,
                "authority_policy_status": (
                    "INVALID"
                    if authority_policy_errors(
                        npc.authority_limits,
                        appointment.authority_overrides,
                    )
                    else "VALID"
                ),
                "permissions": sorted(
                    name for name, allowed in npc.permission_profile.items() if allowed
                ),
            }
            for appointment, npc in rows
        ]

    def _known_world_state(
        self,
        player_id: UUID,
        world: dict[str, object],
    ) -> dict[str, object]:
        intelligence = str(world.get("valley_intelligence", "INCOMPLETE"))
        security = str(world.get("valley_security", "UNSAFE"))
        ambush_status = "UNKNOWN"
        if intelligence in {"PARTIAL", "COMPLETE"}:
            ambush_status = "CLEARED" if security == "SAFE" else "DISCOVERED"
        facts = {
            fact.key: fact.value
            for fact in self.db.scalars(
                select(PlayerWorldFact).where(PlayerWorldFact.player_id == player_id)
            ).all()
        }
        return {
            "village_relation": world.get("village_support", "NONE"),
            "village_support": world.get("village_support", "NONE"),
            "valley_intelligence": intelligence,
            "valley_security": security,
            "ambush_status": ambush_status,
            "enemy_supply_route": world.get("enemy_supply_route", "UNKNOWN"),
            "starfire_outpost_status": world.get("starfire_outpost_status", "DAMAGED"),
            "northern_trade_route_status": world.get(
                "northern_trade_route_status",
                "CLOSED",
            ),
            "fact_versions": {
                key: value.get("operation_id")
                for key, value in facts.items()
                if isinstance(value, dict) and value.get("operation_id") is not None
            },
        }

    def _hidden_world_truth(self, world: dict[str, object]) -> dict[str, object]:
        return {
            "classification": "DEVELOPER_ONLY",
            "ambush_status": ("CLEARED" if world.get("valley_security") == "SAFE" else "ACTIVE"),
            "enemy_supply_route": (
                "DISRUPTED" if world.get("enemy_supply_route") == "DISRUPTED" else "ACTIVE"
            ),
            "resolution_rules": {
                "first_clear_attempt": "DEFEAT_UNTIL_SUPPLY_DISRUPTED",
                "world_outcomes": "DETERMINED_BY_GAME_SERVICE",
            },
        }

    def _messages(self, session_id: UUID) -> list[dict[str, object]]:
        messages = list(
            self.db.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(30)
            ).all()
        )
        messages.reverse()
        return [
            {
                "id": str(message.id),
                "role": message.role.value,
                "content": message.content,
                "created_at": message.created_at,
            }
            for message in messages
        ]

    def _timeline(
        self,
        task: AgentTask | None,
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = [
            {
                "id": f"message:{message['id']}",
                "kind": "PLAYER_COMMAND" if message["role"] == "USER" else "STRATEGIST_REPORT",
                "actor": (
                    {"key": "player", "name": "主公", "role": "PLAYER"}
                    if message["role"] == "USER"
                    else self._officer(seed_id("npc:shen_ce"))
                ),
                "content": message["content"],
                "status": "RECORDED",
                "created_at": message["created_at"],
            }
            for message in messages
        ]
        if task is None:
            return items
        plans = list(
            self.db.scalars(
                select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
            ).all()
        )
        for plan in plans:
            items.append(
                {
                    "id": f"plan:{plan.id}",
                    "kind": "PLAN_CREATED" if plan.version == 1 else "REPLAN",
                    "actor": self._officer(plan.created_by_npc_id or task.owner_npc_id),
                    "content": plan.strategy_summary,
                    "status": plan.status.value,
                    "plan_version": plan.version,
                    "replan_reason": plan.replan_reason,
                    "created_at": plan.created_at,
                }
            )
            steps = self.db.scalars(
                select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
            ).all()
            for step in steps:
                if step.status.value in {"PENDING", "SKIPPED"}:
                    continue
                items.append(
                    {
                        "id": f"step:{step.id}",
                        "kind": (
                            "FINAL_REPORT"
                            if step.action_intent == "VERIFY_AND_REPORT"
                            and step.status.value == "SUCCEEDED"
                            else "OFFICER_ACTION"
                        ),
                        "actor": self._officer(step.assigned_npc_id or task.owner_npc_id),
                        "content": step.description,
                        "status": step.status.value,
                        "plan_version": plan.version,
                        "step_sequence": step.sequence,
                        "result": step.actual_result,
                        "failure_code": step.failure_code,
                        "created_at": step.completed_at or step.started_at or plan.created_at,
                    }
                )
        decisions = self.db.scalars(
            select(PlayerDecisionRequest)
            .where(PlayerDecisionRequest.task_id == task.id)
            .order_by(PlayerDecisionRequest.created_at)
        ).all()
        for decision in decisions:
            items.append(
                {
                    "id": f"decision:{decision.id}",
                    "kind": "DECISION_REQUEST",
                    "actor": self._officer(decision.requested_by_npc_id),
                    "content": decision.summary,
                    "status": decision.status.value,
                    "created_at": decision.created_at,
                }
            )
        operations = self.db.scalars(
            select(WorldOperation)
            .where(WorldOperation.task_id == task.id)
            .order_by(WorldOperation.created_at)
        ).all()
        for operation in operations:
            items.append(
                {
                    "id": f"operation:{operation.id}",
                    "kind": "WORLD_EVENT",
                    "actor": {"key": "game_service", "name": "GameService", "role": "SYSTEM"},
                    "content": f"{operation.operation_type}: {operation.target_key}",
                    "status": operation.status.value,
                    "result": operation.outcome,
                    "created_at": operation.resolved_at or operation.created_at,
                }
            )
        items.sort(key=lambda item: _timestamp(item.get("created_at")))
        return items[-60:]

    def _trace(self, task: AgentTask) -> list[dict[str, object]]:
        runs = self.db.scalars(
            select(AgentRun)
            .where(AgentRun.task_id == task.id)
            .order_by(AgentRun.started_at.desc())
            .limit(20)
        ).all()
        result: list[dict[str, object]] = []
        for run in reversed(list(runs)):
            tools = self.db.scalars(
                select(ToolExecution)
                .where(ToolExecution.agent_run_id == run.id)
                .order_by(ToolExecution.created_at)
            ).all()
            result.append(
                {
                    "id": str(run.id),
                    "actor_npc_id": str(run.actor_npc_id) if run.actor_npc_id else None,
                    "actor": self._officer(run.actor_npc_id) if run.actor_npc_id else None,
                    "purpose": run.purpose,
                    "status": run.status.value,
                    "model": run.model,
                    "token_usage": run.token_usage,
                    "termination_reason": (
                        run.termination_reason.value if run.termination_reason else None
                    ),
                    "plan_validation": {
                        "status": run.validation_status,
                        "errors": run.validation_errors,
                    },
                    "provider_response_summary": {
                        "rounds": run.actual_rounds,
                        "model_rounds": run.model_rounds,
                        "structured_output_present": run.structured_output is not None,
                    },
                    "raw": {
                        "structured_output": run.structured_output,
                        "input_message": run.input_message,
                    },
                    "tools": [
                        {
                            "id": str(tool.id),
                            "tool_name": tool.tool_name,
                            "arguments": tool.arguments,
                            "validation": tool.validation_status,
                            "authority": tool.authorization_status,
                            "authority_details": tool.authority_details,
                            "business_rule": tool.business_rule_status,
                            "execution": tool.execution_status,
                            "failure_code": tool.error_code,
                            "before_state": tool.before_state,
                            "after_state": tool.after_state,
                            "result": tool.result,
                            "duration_ms": tool.duration_ms,
                        }
                        for tool in tools
                    ],
                }
            )
        return result


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.min
