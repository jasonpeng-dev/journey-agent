"""Compose an exact persisted ScenarioVersion with its executable behavior bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.runtime_scope import GameInstanceId, RuntimeScope
from app.domain.scenario import BehaviorBundleRef, ScenarioVersionSnapshot
from app.domain.world import WorldDefinition
from app.infrastructure.db.models import AgentTask, ConversationSession
from app.scenarios.contracts import (
    ScenarioFallbackPlans,
    ScenarioObjectiveCatalog,
    ScenarioObjectiveEvaluator,
    ScenarioPlanningPolicy,
)
from app.scenarios.persisted_objectives import PersistedObjectiveCatalog
from app.scenarios.registry import (
    NodeKeyResolver,
    ScenarioBinding,
    ScenarioWorldBinding,
    TargetInteractionGuard,
    scenario_binding,
)
from app.scenarios.starfire.compatibility import (
    canonical_node_key,
    legacy_target_supports_interaction,
)
from app.scenarios.starfire.fallback_plans import STARFIRE_FALLBACK_PLANS
from app.scenarios.starfire.objectives import STARFIRE_OBJECTIVES
from app.scenarios.starfire.planning_policy import STARFIRE_PLANNING_POLICY
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.interaction_targets import InteractionTargetResolver


class ScenarioRuntimeBindingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class BehaviorRuntimeImplementation:
    """Executable policies only; Objective definitions never live here."""

    ref: BehaviorBundleRef
    resolve_node_key: NodeKeyResolver
    raw_target_supports_interaction: TargetInteractionGuard
    planning_policy: ScenarioPlanningPolicy
    objective_evaluator: ScenarioObjectiveEvaluator
    fallback_plans: ScenarioFallbackPlans


@dataclass(frozen=True, slots=True)
class VersionedScenarioBinding:
    snapshot: ScenarioVersionSnapshot
    world: WorldDefinition
    resolve_node_key: NodeKeyResolver
    raw_target_supports_interaction: TargetInteractionGuard
    planning_policy: ScenarioPlanningPolicy
    objective_catalog: ScenarioObjectiveCatalog
    objective_evaluator: ScenarioObjectiveEvaluator
    fallback_plans: ScenarioFallbackPlans


STARFIRE_RUNTIME_IMPLEMENTATION = BehaviorRuntimeImplementation(
    ref=BehaviorBundleRef(key="starfire", version="1"),
    resolve_node_key=canonical_node_key,
    raw_target_supports_interaction=legacy_target_supports_interaction,
    planning_policy=STARFIRE_PLANNING_POLICY,
    objective_evaluator=STARFIRE_OBJECTIVES,
    fallback_plans=STARFIRE_FALLBACK_PLANS,
)
_IMPLEMENTATIONS = {
    (STARFIRE_RUNTIME_IMPLEMENTATION.ref.key, STARFIRE_RUNTIME_IMPLEMENTATION.ref.version): (
        STARFIRE_RUNTIME_IMPLEMENTATION
    )
}


def scenario_binding_for_task(
    db: Session,
    task: AgentTask,
) -> ScenarioBinding | VersionedScenarioBinding:
    if task.game_instance_id is None:
        legacy = scenario_binding(task.scenario_key)
        if legacy is None:
            raise ScenarioRuntimeBindingError(
                "SCENARIO_RUNTIME_BEHAVIOR_UNAVAILABLE",
                "The legacy Scenario behavior is unavailable",
            )
        return legacy
    return _versioned_binding(
        db,
        game_instance_id=task.game_instance_id,
        expected_player_id=task.player_id,
        expected_scenario_key=task.scenario_key,
    )


def runtime_scope_for_task(db: Session, task: AgentTask) -> RuntimeScope | None:
    if task.game_instance_id is None:
        return None
    scope = GameInstanceService(db).load(GameInstanceId(task.game_instance_id))
    if scope.player_id != task.player_id:
        raise ScenarioRuntimeBindingError(
            "SCENARIO_RUNTIME_PLAYER_MISMATCH",
            "Runtime task Player does not match its GameInstance",
        )
    return scope


def interaction_resolver_for_task(
    db: Session,
    task: AgentTask,
) -> InteractionTargetResolver:
    binding = scenario_binding_for_task(db, task)
    return InteractionTargetResolver({task.scenario_key: cast(ScenarioWorldBinding, binding)})


def scenario_binding_for_session(
    db: Session,
    session: ConversationSession,
    *,
    expected_scenario_key: str,
) -> ScenarioBinding | VersionedScenarioBinding:
    if session.game_instance_id is None:
        legacy = scenario_binding(expected_scenario_key)
        if legacy is None:
            raise ScenarioRuntimeBindingError(
                "SCENARIO_RUNTIME_BEHAVIOR_UNAVAILABLE",
                "The legacy Scenario behavior is unavailable",
            )
        return legacy
    return _versioned_binding(
        db,
        game_instance_id=session.game_instance_id,
        expected_player_id=session.player_id,
        expected_scenario_key=expected_scenario_key,
    )


def _versioned_binding(
    db: Session,
    *,
    game_instance_id: UUID,
    expected_player_id: UUID,
    expected_scenario_key: str,
) -> VersionedScenarioBinding:
    scope = GameInstanceService(db).load(GameInstanceId(game_instance_id))
    if scope.player_id != expected_player_id:
        raise ScenarioRuntimeBindingError(
            "SCENARIO_RUNTIME_PLAYER_MISMATCH",
            "Runtime record Player does not match its GameInstance",
        )
    snapshot = ScenarioVersionRepository(db).load(scope.scenario_version_id)
    definition = snapshot.definition
    if definition.world.key != expected_scenario_key:
        raise ScenarioRuntimeBindingError(
            "SCENARIO_RUNTIME_SCENARIO_MISMATCH",
            "Runtime record Scenario key does not match its ScenarioVersion",
        )
    implementation = _IMPLEMENTATIONS.get(
        (definition.behavior_bundle.key, definition.behavior_bundle.version)
    )
    if implementation is None:
        raise ScenarioRuntimeBindingError(
            "SCENARIO_RUNTIME_BEHAVIOR_UNAVAILABLE",
            "The exact ScenarioVersion behavior implementation is unavailable",
        )
    catalog = PersistedObjectiveCatalog(
        scenario_key=definition.world.key,
        catalog_version=definition.objective_catalog_version,
        definitions=definition.objective_definitions,
    )
    return VersionedScenarioBinding(
        snapshot=snapshot,
        world=definition.world,
        resolve_node_key=implementation.resolve_node_key,
        raw_target_supports_interaction=implementation.raw_target_supports_interaction,
        planning_policy=implementation.planning_policy,
        objective_catalog=catalog,
        objective_evaluator=implementation.objective_evaluator,
        fallback_plans=implementation.fallback_plans,
    )


__all__ = [
    "BehaviorRuntimeImplementation",
    "ScenarioRuntimeBindingError",
    "VersionedScenarioBinding",
    "interaction_resolver_for_task",
    "runtime_scope_for_task",
    "scenario_binding_for_session",
    "scenario_binding_for_task",
]
