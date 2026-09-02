"""Deterministic, snapshot-based evaluation of authored Derived World States."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import ResourcePoolAvailability, ResourcePoolVisibility
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    DerivedDependencyKind,
    DerivedStateDefinitionV2,
    DerivedStateDependencyV2,
    ScenarioDefinitionV2,
    StrictScalar,
    knowledge_gate_is_revealed,
)
from app.infrastructure.db.models import (
    GameInstanceFactState,
    GameInstanceResourceState,
)
from app.services.knowledge_projection import SharedKnowledgeProjection


@dataclass(frozen=True, slots=True)
class DerivedStateValue:
    """One computed state in both authoritative and player-visible domains."""

    truth_value: StrictScalar
    knowledge_value: StrictScalar | None

    @property
    def knowledge_status(self) -> str:
        return "UNKNOWN" if self.knowledge_value is None else "KNOWN"


@dataclass(frozen=True, slots=True)
class DerivedStateEvaluation:
    """The complete pure evaluation result for one Scenario snapshot."""

    values: dict[str, DerivedStateValue]

    @property
    def truth_values(self) -> dict[str, StrictScalar]:
        return {key: value.truth_value for key, value in self.values.items()}

    @property
    def knowledge_values(self) -> dict[str, StrictScalar | None]:
        return {key: value.knowledge_value for key, value in self.values.items()}

    def truth_value(self, key: str) -> StrictScalar:
        return self.values[key].truth_value

    def knowledge_value(self, key: str) -> StrictScalar | None:
        return self.values[key].knowledge_value


class DerivedStateEvaluator:
    """Evaluate all Derived States from one in-memory runtime/Knowledge snapshot.

    The evaluator deliberately loads base rows once and performs the dependency
    graph walk in memory.  It never writes a Derived row, creates a World
    Operation, increments runtime revision, or consults hidden Truth while
    computing the Knowledge projection.
    """

    def __init__(self, db: Session, scope: RuntimeScope, definition: ScenarioDefinitionV2) -> None:
        self.db = db
        self.scope = scope
        self.definition = definition

    def evaluate(self) -> DerivedStateEvaluation:
        if not self.definition.derived_states:
            return DerivedStateEvaluation(values={})

        fact_rows = tuple(
            self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id
                )
            )
        )
        resource_rows = tuple(
            self.db.scalars(
                select(GameInstanceResourceState).where(
                    GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
                )
            )
        )
        truth_facts = {
            (row.node_key, row.fact_key): cast(StrictScalar, row.truth_value) for row in fact_rows
        }
        knowledge_projection = SharedKnowledgeProjection(self.db, self.scope, self.definition)
        known_facts = {
            (row.node_key, row.fact_key): cast(StrictScalar, row.truth_value)
            for row in knowledge_projection.known_fact_rows()
        }
        knowledge_resources = knowledge_projection.resource_intelligence()
        truth_resources = self._truth_resources(resource_rows)
        hidden_resource_keys: set[tuple[str | None, str]] = {
            (row.scope_node_key, row.resource_key)
            for row in resource_rows
            if row.scope_node_key is not None and row.visibility != ResourcePoolVisibility.VISIBLE
        }
        states = self.definition.derived_state_definitions
        truth_cache: dict[str, StrictScalar] = {}
        knowledge_cache: dict[str, StrictScalar | None] = {}

        def dependency_truth(dependency: DerivedStateDependencyV2) -> bool:
            if dependency.kind == DerivedDependencyKind.FACT:
                assert dependency.node_key is not None and dependency.fact_key is not None
                return truth_facts.get((dependency.node_key, dependency.fact_key)) in (
                    dependency.accepted_values
                )
            if dependency.kind == DerivedDependencyKind.RESOURCE_AT_LEAST:
                assert dependency.region_key is not None and dependency.resource_key is not None
                assert dependency.minimum is not None
                amount = truth_resources.get((dependency.resource_key, dependency.region_key), 0)
                return amount >= dependency.minimum
            assert dependency.derived_key is not None
            return truth_cache[dependency.derived_key] in dependency.accepted_values

        def dependency_knowledge(dependency: DerivedStateDependencyV2) -> bool | None:
            gate_status = knowledge_gate_status(dependency)
            if gate_status is not True:
                return gate_status
            if dependency.kind == DerivedDependencyKind.FACT:
                assert dependency.node_key is not None and dependency.fact_key is not None
                value = known_facts.get((dependency.node_key, dependency.fact_key))
                if value is None:
                    return None
                return value in dependency.accepted_values
            if dependency.kind == DerivedDependencyKind.RESOURCE_AT_LEAST:
                assert dependency.region_key is not None and dependency.resource_key is not None
                assert dependency.minimum is not None
                amount = self._known_resource_amount(
                    knowledge_resources,
                    dependency.region_key,
                    dependency.resource_key,
                    hidden_resource_keys=hidden_resource_keys,
                )
                if amount is None:
                    return None
                return amount >= dependency.minimum
            assert dependency.derived_key is not None
            value = knowledge_cache[dependency.derived_key]
            if value is None:
                return None
            return value in dependency.accepted_values

        def knowledge_gate_status(dependency: DerivedStateDependencyV2) -> bool | None:
            gate = dependency.knowledge_gate
            if gate is None:
                return True
            value = known_facts.get((gate.node_key, gate.fact_key))
            return True if knowledge_gate_is_revealed(gate, value) else None

        def evaluate_truth(state: DerivedStateDefinitionV2) -> StrictScalar:
            if state.key in truth_cache:
                return truth_cache[state.key]
            # Definitions are validated as a DAG.  Recursive evaluation keeps
            # the implementation independent of authoring order while the
            # resulting map remains deterministic.
            for dependency in state.dependencies:
                if dependency.kind == DerivedDependencyKind.DERIVED_STATE:
                    assert dependency.derived_key is not None
                    evaluate_truth(states[dependency.derived_key])
            satisfied = all(dependency_truth(item) for item in state.dependencies)
            value = state.available_value if satisfied else state.unavailable_value
            truth_cache[state.key] = value
            return value

        def evaluate_knowledge(state: DerivedStateDefinitionV2) -> StrictScalar | None:
            if state.key in knowledge_cache:
                return knowledge_cache[state.key]
            for dependency in state.dependencies:
                if dependency.kind == DerivedDependencyKind.DERIVED_STATE:
                    assert dependency.derived_key is not None
                    evaluate_knowledge(states[dependency.derived_key])
            statuses = [dependency_knowledge(item) for item in state.dependencies]
            if any(status is False for status in statuses):
                satisfied: bool | None = False
            elif all(status is True for status in statuses):
                satisfied = True
            else:
                satisfied = None
            value = (
                state.available_value
                if satisfied is True
                else state.unavailable_value
                if satisfied is False
                else None
            )
            knowledge_cache[state.key] = value
            return value

        for key in sorted(states):
            state = states[key]
            evaluate_truth(state)
            evaluate_knowledge(state)
        return DerivedStateEvaluation(
            values={
                key: DerivedStateValue(
                    truth_value=truth_cache[key],
                    knowledge_value=knowledge_cache[key],
                )
                for key in sorted(states)
            }
        )

    @staticmethod
    def _truth_resources(
        rows: tuple[GameInstanceResourceState, ...],
    ) -> dict[tuple[str, str], int]:
        result: dict[tuple[str, str], int] = {}
        for row in rows:
            if row.scope_node_key is None or row.availability != ResourcePoolAvailability.AVAILABLE:
                continue
            identity = (row.resource_key, row.scope_node_key)
            result[identity] = result.get(identity, 0) + max(0, row.value - row.reserved_value)
        return result

    @staticmethod
    def _known_resource_amount(
        intelligence: dict[str, object],
        region_key: str,
        resource_key: str,
        *,
        hidden_resource_keys: set[tuple[str | None, str]] | None = None,
    ) -> int | None:
        if hidden_resource_keys is not None and (region_key, resource_key) in hidden_resource_keys:
            return None
        raw_regions = intelligence.get("regions")
        if not isinstance(raw_regions, dict):
            return None
        raw_region = raw_regions.get(region_key)
        if not isinstance(raw_region, dict):
            return None
        if raw_region.get("resource_inventory_visibility") != "VISIBLE":
            return None
        if raw_region.get("resource_survey_completed") is not True:
            return None
        raw_resources = raw_region.get("resources")
        if not isinstance(raw_resources, dict):
            return 0
        raw_resource = raw_resources.get(resource_key)
        if not isinstance(raw_resource, dict):
            return 0
        raw_amount = raw_resource.get("known_available")
        return (
            raw_amount if isinstance(raw_amount, int) and not isinstance(raw_amount, bool) else None
        )


def evaluate_derived_states(
    db: Session,
    scope: RuntimeScope,
    definition: ScenarioDefinitionV2,
) -> DerivedStateEvaluation:
    """Convenience entry point for completion and projection consumers."""

    return DerivedStateEvaluator(db, scope, definition).evaluate()


__all__ = [
    "DerivedStateEvaluation",
    "DerivedStateEvaluator",
    "DerivedStateValue",
    "evaluate_derived_states",
]
