"""Generic Objective requirement evaluation across Truth and public Knowledge."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import ResourcePoolAvailability
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ObjectiveRequirementKind,
    ObjectiveRequirementV2,
    ScenarioDefinitionV2,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import GameInstanceFactState, GameInstanceResourceState
from app.services.derived_state import DerivedStateEvaluation, evaluate_derived_states
from app.services.knowledge_projection import SharedKnowledgeProjection


def requirement_gate_is_public(
    db: Session, scope: RuntimeScope, requirement: ObjectiveRequirementV2
) -> bool:
    gate = requirement.knowledge_gate
    if gate is None:
        return True
    row = db.get(GameInstanceFactState, (scope.game_instance_id, gate.node_key, gate.fact_key))
    return bool(
        row and row.visibility == Visibility.KNOWN and row.truth_value in gate.accepted_values
    )


def truth_requirement_value(
    db: Session,
    scope: RuntimeScope,
    requirement: ObjectiveRequirementV2,
    *,
    derived_evaluation: DerivedStateEvaluation | None = None,
) -> object:
    if requirement.kind == ObjectiveRequirementKind.FACT:
        assert requirement.node_key is not None and requirement.fact_key is not None
        row = db.get(
            GameInstanceFactState,
            (scope.game_instance_id, requirement.node_key, requirement.fact_key),
        )
        if row is None:
            raise LookupError("Objective Truth is missing from this Instance")
        return row.truth_value
    if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
        assert requirement.derived_key is not None
        if derived_evaluation is None:
            raise ValueError("Derived requirement evaluation is required")
        return derived_evaluation.truth_value(requirement.derived_key)
    assert requirement.region_key is not None and requirement.resource_key is not None
    rows = db.scalars(
        select(GameInstanceResourceState).where(
            GameInstanceResourceState.game_instance_id == scope.game_instance_id,
            GameInstanceResourceState.scope_node_key == requirement.region_key,
            GameInstanceResourceState.resource_key == requirement.resource_key,
            GameInstanceResourceState.availability == ResourcePoolAvailability.AVAILABLE,
        )
    )
    return sum(max(0, row.value - row.reserved_value) for row in rows)


def truth_requirement_satisfied(
    db: Session,
    scope: RuntimeScope,
    requirement: ObjectiveRequirementV2,
    *,
    derived_evaluation: DerivedStateEvaluation | None = None,
) -> tuple[object, bool]:
    if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
        assert requirement.derived_key is not None
        if derived_evaluation is None:
            raise ValueError("Derived requirement evaluation is required")
        value = derived_evaluation.truth_value(requirement.derived_key)
        return value, value in requirement.accepted_values
    requirement_value = truth_requirement_value(db, scope, requirement)
    if requirement.kind == ObjectiveRequirementKind.FACT:
        return requirement_value, requirement_value in requirement.accepted_values
    assert requirement.minimum is not None
    return (
        requirement_value,
        isinstance(requirement_value, int) and requirement_value >= requirement.minimum,
    )


def known_requirement_satisfied(
    db: Session,
    scope: RuntimeScope,
    definition: ScenarioDefinitionV2,
    requirement: ObjectiveRequirementV2,
    *,
    derived_evaluation: DerivedStateEvaluation | None = None,
) -> bool:
    if not requirement_gate_is_public(db, scope, requirement):
        return False
    if requirement.kind == ObjectiveRequirementKind.FACT:
        assert requirement.node_key is not None and requirement.fact_key is not None
        row = db.get(
            GameInstanceFactState,
            (scope.game_instance_id, requirement.node_key, requirement.fact_key),
        )
        return bool(
            row
            and row.visibility == Visibility.KNOWN
            and row.truth_value in requirement.accepted_values
        )
    if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
        assert requirement.derived_key is not None
        evaluation = derived_evaluation or evaluate_derived_states(db, scope, definition)
        value = evaluation.knowledge_value(requirement.derived_key)
        return value in requirement.accepted_values
    assert requirement.region_key is not None and requirement.resource_key is not None
    assert requirement.minimum is not None
    projection = SharedKnowledgeProjection(db, scope, definition).planner_resources()
    resource = projection["resources"].get(requirement.resource_key, {})
    region = resource.get("regions", {}).get(requirement.region_key, {})
    return int(region.get("known_available", 0)) >= requirement.minimum


__all__ = [
    "known_requirement_satisfied",
    "requirement_gate_is_public",
    "truth_requirement_satisfied",
    "truth_requirement_value",
]
