from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import authority_policy_errors, effective_authority_limits
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.domain.enums import AgentTaskStatus, NodeStatus, SessionStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.world import AccessState, Visibility
from app.infrastructure.db.models import (
    NPC,
    AgentPlan,
    AgentRun,
    AgentStep,
    AgentTask,
    ConversationMessage,
    ConversationSession,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceOfficerAppointment,
    GameInstanceWorldFact,
    OfficerAppointment,
    PlayerDecisionRequest,
    PlayerNodeState,
    PlayerWorldFact,
    PlayerWorldFactState,
    ToolExecution,
    WorldNode,
    WorldOperation,
)
from app.scenarios.contracts import project_known_relation_payloads
from app.scenarios.runtime_binding import scenario_binding_for_session, scenario_binding_for_task
from app.scenarios.starfire.ruleset import StarfireKnowledgeState
from app.services.game import GameService, seed_id
from app.services.game_instances import GameInstanceService
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
        scope = (
            GameInstanceService(self.db).load(GameInstanceId(session.game_instance_id))
            if session.game_instance_id is not None
            else None
        )
        game = GameService(self.db, scope)
        player = game.get_player(session.player_id)
        command_state = game.inspect_command_state(player.id)
        resources = dict(cast(dict[str, object], command_state["resources"]))
        world = dict(cast(dict[str, object], command_state["world"]))
        task = self._latest_task(session.game_instance_id, player.id)
        serialized_task = TaskService(self.db).serialize(task) if task is not None else None
        objective_evaluation = self._objective_evaluation(task)
        operation_wait_pairs = self._operation_wait_pairs(task)
        early_stop = self._early_stop(task)
        trace = self._trace(task) if include_trace and task is not None else []
        messages = self._messages(session.id)
        officers = self._officers(session.game_instance_id, player.id)
        known_world = self._known_world_state(session.game_instance_id, player.id, world)
        binding = scenario_binding_for_session(
            self.db, session, expected_scenario_key="starfire_command"
        )
        player_world = self._player_world_projection(
            game.scenario_known_state(player.id), binding.world
        )
        observer_world = (
            self._observer_world_projection(
                session.game_instance_id, player.id, task, binding.world
            )
            if include_hidden_truth
            else None
        )
        snapshot_version = (
            f"i{scope.game_instance_id if scope else 'legacy'}"
            f"-v{scope.scenario_version_id if scope else 'registry'}"
            f"-t{task.version if task is not None else 0}"
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
                "version_id": str(scope.scenario_version_id) if scope else None,
            },
            "game_instance_id": str(scope.game_instance_id) if scope else None,
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
                "actor_key": session.actor_key,
                "commanding_officer": (
                    self._officer(session.npc_id) if session.npc_id is not None else None
                ),
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
            "objective_evaluation": objective_evaluation,
            "operation_wait_pairs": operation_wait_pairs,
            "early_stop": early_stop,
            "active_decision": active_decision,
            "pending_player_action": pending_player_action,
            "pending_world_event": pending_world_event,
            "timeline": self._timeline(task, messages, objective_evaluation, early_stop),
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

    def _latest_task(self, game_instance_id: UUID | None, player_id: UUID) -> AgentTask | None:
        owner = (
            AgentTask.game_instance_id == game_instance_id
            if game_instance_id is not None
            else AgentTask.player_id == player_id
        )
        return self.db.scalar(
            select(AgentTask)
            .where(
                owner,
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

    def _officers(self, game_instance_id: UUID | None, player_id: UUID) -> list[dict[str, object]]:
        appointment_type = (
            GameInstanceOfficerAppointment if game_instance_id is not None else OfficerAppointment
        )
        owner = (
            GameInstanceOfficerAppointment.game_instance_id == game_instance_id
            if game_instance_id is not None
            else OfficerAppointment.player_id == player_id
        )
        rows = self.db.execute(
            select(appointment_type, NPC)
            .join(NPC, NPC.id == appointment_type.npc_id)
            .where(owner)
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
        game_instance_id: UUID | None,
        player_id: UUID,
        world: dict[str, object],
    ) -> dict[str, object]:
        fact_type = GameInstanceWorldFact if game_instance_id is not None else PlayerWorldFact
        owner = (
            GameInstanceWorldFact.game_instance_id == game_instance_id
            if game_instance_id is not None
            else PlayerWorldFact.player_id == player_id
        )
        fact_rows: Any = self.db.scalars(select(fact_type).where(owner)).all()
        facts = {fact.key: fact.value for fact in fact_rows}
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
    def _player_world_projection(
        known: StarfireKnowledgeState, world_definition: Any
    ) -> dict[str, object]:
        facts = known.facts
        node_access = known.node_access
        nodes = []
        for definition in world_definition.nodes:
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
            "known_relations": [
                dict(relation)
                for relation in project_known_relation_payloads(
                    world_definition.relations,
                    node_access,
                )
            ],
        }

    def _observer_world_projection(
        self,
        game_instance_id: UUID | None,
        player_id: UUID,
        task: AgentTask | None,
        world_definition: Any,
    ) -> dict[str, object]:
        if game_instance_id is not None:
            rows = self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == game_instance_id
                )
            ).all()
            state_by_key = {state.node_key: state for state in rows}
            fact_rows: Any = self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == game_instance_id
                )
            ).all()

            def fact_node_key(fact: Any) -> str:
                return str(fact.node_key)
        else:
            legacy_rows = self.db.execute(
                select(WorldNode, PlayerNodeState)
                .join(PlayerNodeState, PlayerNodeState.node_id == WorldNode.id)
                .where(PlayerNodeState.player_id == player_id)
            ).all()
            state_by_key = {node.key: state for node, state in legacy_rows}
            node_keys = {node.id: node.key for node, _state in legacy_rows}
            fact_rows = self.db.scalars(
                select(PlayerWorldFactState).where(PlayerWorldFactState.player_id == player_id)
            ).all()

            def fact_node_key(fact: Any) -> str:
                return str(node_keys[fact.node_id])

        facts: dict[str, list[dict[str, object]]] = {}
        reveal_sources = self._fact_reveal_sources(task)
        for fact in fact_rows:
            node_key = fact_node_key(fact)
            fact_payload: dict[str, object] = {
                "key": fact.fact_key,
                "truth": fact.truth_value,
                "knowledge": fact.visibility.value,
            }
            reveal_source = reveal_sources.get((node_key, fact.fact_key))
            if reveal_source is not None:
                fact_payload["revealed_by"] = reveal_source
            facts.setdefault(node_key, []).append(fact_payload)
        known_access = {
            key: AccessState(self._node_access(state.status))
            for key, state in state_by_key.items()
            if state.visibility == Visibility.KNOWN
        }
        visible_relations = {
            (
                str(item["source_node_key"]),
                str(item["relation_type"]),
                str(item["target_node_key"]),
            )
            for item in project_known_relation_payloads(world_definition.relations, known_access)
        }
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
                for definition in world_definition.nodes
                if definition.key in state_by_key
            ],
            "relations": [
                {
                    "source_node_key": relation.source_node_key,
                    "relation_type": relation.relation_type.value,
                    "target_node_key": relation.target_node_key,
                    "planner_visible": (
                        relation.source_node_key,
                        relation.relation_type.value,
                        relation.target_node_key,
                    )
                    in visible_relations,
                }
                for relation in world_definition.relations
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
        objective_evaluation: dict[str, object] | None,
        early_stop: dict[str, object] | None,
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
                    "kind": "PLAN" if plan.version == 1 else "REPLAN",
                    "actor": self._officer(plan.created_by_npc_id or task.owner_npc_id),
                    "content": plan.strategy_summary,
                    "status": plan.status.value,
                    "plan_version": plan.version,
                    "source_plan_version": plan.version - 1 if plan.version > 1 else None,
                    "new_plan_version": plan.version,
                    "replan_reason": plan.replan_reason,
                    "source": plan.source,
                    "planner_model": plan.planner_model,
                    "created_at": plan.created_at,
                }
            )
            steps = self.db.scalars(
                select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
            ).all()
            for step in steps:
                if step.status.value == "PENDING":
                    continue
                if step.execution_type.value == "WAIT_FOR_WORLD_EVENT":
                    kind = "WAIT_RESULT"
                elif step.status.value == "FAILED":
                    kind = "FAILURE"
                else:
                    kind = "TOOL_CALL"
                items.append(
                    {
                        "id": f"step:{step.id}",
                        "kind": kind,
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
                if (
                    step.execution_type.value == "WAIT_FOR_WORLD_EVENT"
                    and step.status.value == "FAILED"
                ):
                    items.append(
                        {
                            "id": f"failure:{step.id}",
                            "kind": "FAILURE",
                            "actor": self._officer(step.assigned_npc_id or task.owner_npc_id),
                            "content": step.description,
                            "status": step.status.value,
                            "plan_version": plan.version,
                            "step_sequence": step.sequence,
                            "result": step.actual_result,
                            "failure_code": step.failure_code,
                            "created_at": (step.completed_at or step.started_at or plan.created_at),
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
                    "kind": "WORLD_OPERATION",
                    "actor": {"key": "game_service", "name": "GameService", "role": "SYSTEM"},
                    "content": f"{operation.operation_type}: {operation.target_key}",
                    "status": operation.status.value,
                    "result": operation.outcome,
                    "created_at": operation.resolved_at or operation.created_at,
                }
            )
            outcome = operation.outcome if isinstance(operation.outcome, dict) else {}
            discovered = outcome.get("facts_discovered")
            if isinstance(discovered, list) and discovered:
                items.append(
                    {
                        "id": f"knowledge:{operation.id}",
                        "kind": "KNOWLEDGE_REVEALED",
                        "actor": {"key": "game_service", "name": "GameService", "role": "SYSTEM"},
                        "content": "Knowledge revealed by operation result",
                        "status": "RECORDED",
                        "result": {
                            "operation_id": str(operation.id),
                            "facts_discovered": discovered,
                            "failure_code": outcome.get("failure_code"),
                        },
                        "created_at": operation.resolved_at or operation.created_at,
                    }
                )
        if objective_evaluation is not None:
            items.append(
                {
                    "id": f"objective-evaluation:{task.id}",
                    "kind": "OBJECTIVE_EVALUATION",
                    "actor": {
                        "key": "game_service",
                        "name": "ObjectiveEvaluator",
                        "role": "SYSTEM",
                    },
                    "content": "Frozen objective scope evaluated by backend",
                    "status": (
                        "SUCCEEDED" if objective_evaluation["scope_satisfied"] else "INCOMPLETE"
                    ),
                    "result": objective_evaluation,
                    "created_at": task.completed_at or task.updated_at,
                }
            )
        if early_stop is not None and early_stop["triggered"]:
            items.append(
                {
                    "id": f"early-stop:{task.id}",
                    "kind": "EARLY_STOP",
                    "actor": {"key": "game_service", "name": "TaskService", "role": "SYSTEM"},
                    "content": "Objective scope satisfied; only future steps were skipped",
                    "status": "RECORDED",
                    "result": early_stop,
                    "created_at": task.completed_at or task.updated_at,
                }
            )
        if task.status.value == "SUCCEEDED":
            items.append(
                {
                    "id": f"task-completed:{task.id}",
                    "kind": "TASK_COMPLETED",
                    "actor": {"key": "game_service", "name": "TaskService", "role": "SYSTEM"},
                    "content": "Task completed after authoritative objective evaluation",
                    "status": task.status.value,
                    "created_at": task.completed_at or task.updated_at,
                }
            )
        items.sort(key=lambda item: _timestamp(item.get("created_at")))
        return items[-60:]

    def _objective_evaluation(self, task: AgentTask | None) -> dict[str, object] | None:
        if task is None:
            return None
        service = TaskService(self.db)
        if service.load_frozen_scope(task) is None:
            return None
        evaluation = service.evaluate_scope(task)
        scope = (
            GameInstanceService(self.db).load(GameInstanceId(task.game_instance_id))
            if task.game_instance_id is not None
            else None
        )
        known = GameService(self.db, scope).scenario_known_state(task.player_id)
        scoped_refs = {
            (item.requirement.node_key, item.requirement.fact_key)
            for objective in evaluation.objectives
            for item in objective.requirements
        }
        outside_scope_state: list[dict[str, object]] = []
        seen_refs: set[tuple[str, str]] = set()
        catalog = scenario_binding_for_task(self.db, task).objective_catalog
        for definition in catalog.definitions.values():
            for requirement in definition.completion_requirements:
                ref = (requirement.node_key, requirement.fact_key)
                if ref in scoped_refs or ref in seen_refs or not known.fact_known(*ref):
                    continue
                seen_refs.add(ref)
                outside_scope_state.append(
                    {
                        "node_key": requirement.node_key,
                        "fact_key": requirement.fact_key,
                        "actual_value": known.fact_value(*ref),
                        "scope_relation": "OUTSIDE_CURRENT_SCOPE",
                    }
                )
        return {
            "source": "BACKEND_SCOPED_OBJECTIVE_EVALUATOR",
            "scope_satisfied": evaluation.completed,
            "evaluated_at": task.completed_at or task.updated_at,
            "outside_scope_state": outside_scope_state,
            "objectives": [
                {
                    "objective_key": objective.objective_key,
                    "satisfied": objective.completed,
                    "requirements": [
                        {
                            "key": item.requirement.key,
                            "node_key": item.requirement.node_key,
                            "fact_key": item.requirement.fact_key,
                            "description": item.requirement.description,
                            "accepted_values": sorted(item.requirement.accepted_values),
                            "actual_value": item.actual_value,
                            "satisfied": item.satisfied,
                        }
                        for item in objective.requirements
                    ],
                }
                for objective in evaluation.objectives
            ],
        }

    def _early_stop(self, task: AgentTask | None) -> dict[str, object] | None:
        if task is None:
            return None
        plans = self.db.scalars(
            select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
        ).all()
        skipped: list[dict[str, object]] = []
        for plan in plans:
            for step in self.db.scalars(
                select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
            ).all():
                if step.actual_result == {"skip_reason": "OBJECTIVE_SCOPE_SATISFIED"}:
                    skipped.append(
                        {
                            "plan_version": plan.version,
                            "step_sequence": step.sequence,
                            "description": step.description,
                            "execution_type": step.execution_type.value,
                            "selected_tool_name": step.selected_tool_name,
                        }
                    )
        return {
            "triggered": bool(skipped),
            "reason": "OBJECTIVE_SCOPE_SATISFIED" if skipped else None,
            "skipped_future_step_count": len(skipped),
            "skipped_future_steps": skipped,
        }

    def _operation_wait_pairs(self, task: AgentTask | None) -> list[dict[str, object]]:
        if task is None:
            return []
        plans = self.db.scalars(
            select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
        ).all()
        steps = [
            (plan, step)
            for plan in plans
            for step in self.db.scalars(
                select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
            ).all()
        ]
        step_by_id = {step.id: (plan, step) for plan, step in steps}
        waits_by_source: dict[tuple[UUID, int], AgentStep] = {}
        for plan, step in steps:
            condition = step.resume_condition if isinstance(step.resume_condition, dict) else {}
            source_sequence = condition.get("source_step_sequence")
            if step.execution_type.value == "WAIT_FOR_WORLD_EVENT" and isinstance(
                source_sequence, int
            ):
                waits_by_source[(plan.id, source_sequence)] = step
        operations = self.db.scalars(
            select(WorldOperation)
            .where(WorldOperation.task_id == task.id)
            .order_by(WorldOperation.created_at)
        ).all()
        result: list[dict[str, object]] = []
        for operation in operations:
            source = step_by_id.get(operation.source_step_id) if operation.source_step_id else None
            source_plan: AgentPlan | None = source[0] if source is not None else None
            operation_step: AgentStep | None = source[1] if source is not None else None
            wait_step = (
                waits_by_source.get((source_plan.id, operation_step.sequence))
                if source_plan is not None and operation_step is not None
                else None
            )
            result.append(
                {
                    "operation_id": str(operation.id),
                    "operation_type": operation.operation_type,
                    "target_key": operation.target_key,
                    "operation_status": operation.status.value,
                    "operation_outcome": operation.outcome,
                    "plan_version": source_plan.version if source_plan is not None else None,
                    "operation_step_sequence": operation_step.sequence if operation_step else None,
                    "wait_step_sequence": wait_step.sequence if wait_step else None,
                    "wait_status": wait_step.status.value if wait_step else None,
                    "wait_result": wait_step.actual_result if wait_step else None,
                    "paired": wait_step is not None,
                }
            )
        return result

    def _fact_reveal_sources(
        self,
        task: AgentTask | None,
    ) -> dict[tuple[str, str], dict[str, object]]:
        if task is None:
            return {}
        result: dict[tuple[str, str], dict[str, object]] = {}
        operations = self.db.scalars(
            select(WorldOperation)
            .where(WorldOperation.task_id == task.id)
            .order_by(WorldOperation.created_at)
        ).all()
        for operation in operations:
            outcome = operation.outcome if isinstance(operation.outcome, dict) else {}
            discovered = outcome.get("facts_discovered")
            if not isinstance(discovered, list):
                continue
            for fact_key in discovered:
                canonical = {
                    "enemy_supply_route": ("enemy_north_supply_route", "supply_status")
                }.get(str(fact_key))
                if canonical is not None:
                    result[canonical] = {
                        "operation_id": str(operation.id),
                        "operation_type": operation.operation_type,
                        "failure_code": outcome.get("failure_code"),
                    }
        return result

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
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)
