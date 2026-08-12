from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import authority_policy_errors, effective_authority_limits
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.domain.enums import AgentTaskStatus, NodeStatus, SessionStatus
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
    PlayerNodeState,
    PlayerWorldFact,
    PlayerWorldFactState,
    ToolExecution,
    WorldNode,
    WorldOperation,
)
from app.scenarios.starfire.definition import STARFIRE_WORLD
from app.scenarios.starfire.ruleset import StarfireKnowledgeState
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
        game = GameService(self.db)
        command_state = game.inspect_command_state(player.id)
        resources = dict(cast(dict[str, object], command_state["resources"]))
        world = dict(cast(dict[str, object], command_state["world"]))
        task = self._latest_task(player.id)
        serialized_task = TaskService(self.db).serialize(task) if task is not None else None
        trace = self._trace(task) if include_trace and task is not None else []
        messages = self._messages(session.id)
        officers = self._officers(player.id)
        known_world = self._known_world_state(player.id, world)
        player_world = self._player_world_projection(game.scenario_known_state(player.id))
        observer_world = (
            self._observer_world_projection(player.id) if include_hidden_truth else None
        )
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
        objective_status = (
            serialized_task.get("objective_resolution", {}).get("status")
            if isinstance(serialized_task, dict)
            and isinstance(serialized_task.get("objective_resolution"), dict)
            else None
        )
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
            "player_world_state": player_world,
            "observer_world_state": observer_world,
            "hidden_world_truth": observer_world,
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
                "requires_goal_clarification": objective_status == "NEEDS_CLARIFICATION",
                "shows_player_action": pending_player_action is not None,
            },
            "polling": {
                "recommended": (
                    status == AgentTaskStatus.ACTIVE.value
                    and objective_status in {None, "CONFIRMED"}
                ),
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
        facts = {
            fact.key: fact.value
            for fact in self.db.scalars(
                select(PlayerWorldFact).where(PlayerWorldFact.player_id == player_id)
            ).all()
        }
        return {
            **world,
            "village_relation": world.get("village_support", "NONE"),
            "fact_versions": {
                key: value.get("operation_id")
                for key, value in facts.items()
                if key in world
                and isinstance(value, dict)
                and value.get("operation_id") is not None
            },
        }

    @staticmethod
    def _player_world_projection(known: StarfireKnowledgeState) -> dict[str, object]:
        facts = known.facts
        node_access = known.node_access
        nodes = []
        for definition in STARFIRE_WORLD.nodes:
            if definition.key not in node_access:
                continue
            nodes.append(
                {
                    "key": definition.key,
                    "name": definition.name,
                    "access": node_access[definition.key].value,
                    "available_interactions": (
                        [interaction.key for interaction in definition.interactions]
                        if node_access[definition.key].value == "AVAILABLE"
                        else []
                    ),
                    "facts": {
                        fact.key: facts[(definition.key, fact.key)]
                        for fact in definition.facts
                        if (definition.key, fact.key) in facts
                    },
                }
            )
        return {
            "classification": "PLAYER_AGENT_KNOWLEDGE",
            "nodes": nodes,
        }

    def _observer_world_projection(self, player_id: UUID) -> dict[str, object]:
        rows = self.db.execute(
            select(WorldNode, PlayerNodeState)
            .join(PlayerNodeState, PlayerNodeState.node_id == WorldNode.id)
            .where(PlayerNodeState.player_id == player_id)
        ).all()
        state_by_key = {node.key: state for node, state in rows}
        node_ids = {node.id: node.key for node, _state in rows}
        fact_rows = self.db.scalars(
            select(PlayerWorldFactState).where(PlayerWorldFactState.player_id == player_id)
        ).all()
        facts: dict[str, list[dict[str, object]]] = {}
        for fact in fact_rows:
            node_key = node_ids.get(fact.node_id)
            if node_key is None:
                continue
            facts.setdefault(node_key, []).append(
                {
                    "key": fact.fact_key,
                    "truth": fact.truth_value,
                    "knowledge": fact.visibility.value,
                }
            )
        return {
            "classification": "DEVELOPER_ONLY_READ_ONLY",
            "nodes": [
                {
                    "key": definition.key,
                    "name": definition.name,
                    "truth": "EXISTS",
                    "knowledge": state_by_key[definition.key].visibility.value,
                    "access": self._node_access(state_by_key[definition.key].status),
                    "supported_interactions": [
                        interaction.key for interaction in definition.interactions
                    ],
                    "facts": sorted(
                        facts.get(definition.key, []), key=lambda item: str(item["key"])
                    ),
                }
                for definition in STARFIRE_WORLD.nodes
                if definition.key in state_by_key
            ],
            "resolution_rules": {
                "first_clear_attempt": "DEFEAT_UNTIL_SUPPLY_DISRUPTED",
                "world_outcomes": "DETERMINED_BY_GAME_SERVICE",
            },
        }

    @staticmethod
    def _node_access(status: NodeStatus) -> str:
        return "LOCKED" if status == NodeStatus.LOCKED else "AVAILABLE"

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
        scope = TaskService(self.db).load_frozen_scope(task)
        scope_snapshot = (
            None
            if scope is None
            else {
                "scenario_key": scope.scenario_key,
                "catalog_version": scope.catalog_version,
                "objective_keys": list(scope.objective_keys),
                "frozen": True,
            }
        )
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
                    "objective_scope": scope_snapshot,
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
