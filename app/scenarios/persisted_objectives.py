"""Objective catalog behavior over definitions loaded from a ScenarioVersion snapshot."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from app.scenarios.contracts import (
    ObjectiveContractError,
    ObjectiveDefinition,
    ObjectiveKey,
    ObjectivePrerequisite,
    ObjectiveRequirementEvaluation,
    ObjectiveScope,
    ObjectiveVerificationRequirement,
    PerObjectiveEvaluation,
    ScenarioRuntimeState,
    ScopedObjectiveEvaluation,
)


class PersistedObjectiveCatalog:
    """Generic evaluator whose complete content comes only from a version snapshot."""

    def __init__(
        self,
        *,
        scenario_key: str,
        catalog_version: str,
        definitions: Mapping[ObjectiveKey, ObjectiveDefinition],
    ) -> None:
        self.scenario_key = scenario_key
        self.catalog_version = catalog_version
        self.definitions: Mapping[ObjectiveKey, ObjectiveDefinition] = MappingProxyType(
            dict(definitions)
        )

    def scope(self, objective_keys: Iterable[ObjectiveKey]) -> ObjectiveScope:
        scope = ObjectiveScope(
            scenario_key=self.scenario_key,
            catalog_version=self.catalog_version,
            objective_keys=tuple(sorted(set(str(key) for key in objective_keys))),
        )
        self._validate_scope(scope)
        return scope

    def evaluate(
        self,
        scope: ObjectiveScope,
        state: ScenarioRuntimeState,
    ) -> ScopedObjectiveEvaluation:
        self._validate_scope(scope)
        return ScopedObjectiveEvaluation(
            scope=scope,
            objectives=tuple(
                PerObjectiveEvaluation(
                    objective_key=objective_key,
                    requirements=tuple(
                        self._evaluate_requirement(requirement, state)
                        for requirement in self.definition(objective_key).completion_requirements
                    ),
                )
                for objective_key in scope.objective_keys
            ),
        )

    def definition(self, objective_key: ObjectiveKey) -> ObjectiveDefinition:
        definition = self.definitions.get(str(objective_key))
        if definition is None:
            raise ObjectiveContractError(
                "OBJECTIVE_NOT_SUPPORTED",
                f"Objective {objective_key!s} is not present in the ScenarioVersion snapshot",
            )
        return definition

    def verification_requirements(
        self,
        scope: ObjectiveScope,
    ) -> tuple[ObjectiveVerificationRequirement, ...]:
        self._validate_scope(scope)
        requirements: dict[str, ObjectiveVerificationRequirement] = {}
        for objective_key in scope.objective_keys:
            for requirement in self.definition(objective_key).completion_requirements:
                requirements.setdefault(requirement.key, requirement)
        return tuple(requirements.values())

    def prerequisites(self, scope: ObjectiveScope) -> tuple[ObjectivePrerequisite, ...]:
        self._validate_scope(scope)
        prerequisites: dict[str, ObjectivePrerequisite] = {}
        for objective_key in scope.objective_keys:
            for prerequisite in self.definition(objective_key).prerequisites:
                prerequisites.setdefault(prerequisite.key, prerequisite)
        return tuple(prerequisites.values())

    def _validate_scope(self, scope: ObjectiveScope) -> None:
        if scope.scenario_key != self.scenario_key:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_SCENARIO_MISMATCH",
                "Objective scope belongs to a different Scenario",
            )
        if scope.catalog_version != self.catalog_version:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_CATALOG_MISMATCH",
                "Objective scope belongs to a different ScenarioVersion catalog",
            )
        for objective_key in scope.objective_keys:
            self.definition(objective_key)
        selected = frozenset(scope.objective_keys)
        for objective_key in scope.objective_keys:
            redundant = self.definition(objective_key).subsumes.intersection(selected)
            if redundant:
                raise ObjectiveContractError(
                    "OBJECTIVE_SCOPE_REDUNDANT",
                    "An objective cannot be combined with objectives it subsumes",
                )

    @staticmethod
    def _evaluate_requirement(
        requirement: ObjectiveVerificationRequirement,
        state: ScenarioRuntimeState,
    ) -> ObjectiveRequirementEvaluation:
        actual = (
            state.fact_value(requirement.node_key, requirement.fact_key)
            if state.fact_known(requirement.node_key, requirement.fact_key)
            else None
        )
        return ObjectiveRequirementEvaluation(
            requirement=requirement,
            actual_value=actual,
            satisfied=actual in requirement.accepted_values,
        )


__all__ = ["PersistedObjectiveCatalog"]
