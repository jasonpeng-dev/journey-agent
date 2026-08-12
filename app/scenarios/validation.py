"""Publish-time validation for persisted Scenario definition documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.domain.scenario import ScenarioDefinition
from app.domain.world import FactValueType
from app.scenarios.behavior_registry import (
    DEFAULT_BEHAVIOR_BUNDLES,
    BehaviorBundleRegistry,
)
from app.scenarios.documents import ScenarioDefinitionDocument
from app.tools.catalog import build_registry


@dataclass(frozen=True, slots=True)
class ScenarioValidationIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ScenarioValidationResult:
    definition: ScenarioDefinition | None
    issues: tuple[ScenarioValidationIssue, ...]

    @property
    def passed(self) -> bool:
        return self.definition is not None and not self.issues


class ScenarioDefinitionValidator:
    def __init__(
        self,
        behavior_registry: BehaviorBundleRegistry = DEFAULT_BEHAVIOR_BUNDLES,
    ) -> None:
        self.behavior_registry = behavior_registry

    def validate(self, document: dict[str, Any]) -> ScenarioValidationResult:
        try:
            parsed = ScenarioDefinitionDocument.model_validate(document)
        except ValidationError as exc:
            return ScenarioValidationResult(
                definition=None,
                issues=tuple(
                    ScenarioValidationIssue(
                        code="SCENARIO_DOCUMENT_SCHEMA_INVALID",
                        path=".".join(str(part) for part in error["loc"]),
                        message=str(error["msg"]),
                    )
                    for error in exc.errors()[:20]
                ),
            )
        reference_issues = _document_reference_issues(parsed)
        if reference_issues:
            return ScenarioValidationResult(definition=None, issues=tuple(reference_issues))
        try:
            definition = parsed.to_domain()
        except ValueError as exc:
            return ScenarioValidationResult(
                definition=None,
                issues=(
                    ScenarioValidationIssue(
                        code="SCENARIO_DEFINITION_INVALID",
                        path="definition",
                        message=str(exc),
                    ),
                ),
            )

        issues = [
            *self._validate_behavior(definition),
            *self._validate_objectives(definition),
        ]
        return ScenarioValidationResult(
            definition=definition if not issues else None,
            issues=tuple(issues),
        )

    def _validate_behavior(
        self,
        definition: ScenarioDefinition,
    ) -> list[ScenarioValidationIssue]:
        bundle = self.behavior_registry.get(definition.behavior_bundle)
        if bundle is None:
            return [
                ScenarioValidationIssue(
                    code="SCENARIO_BEHAVIOR_BUNDLE_UNAVAILABLE",
                    path="behavior_bundle",
                    message=("The exact behavior bundle key and version are not registered"),
                )
            ]
        issues: list[ScenarioValidationIssue] = []
        for interaction in definition.world.interactions:
            if interaction.key not in bundle.interaction_tools:
                issues.append(
                    ScenarioValidationIssue(
                        code="SCENARIO_INTERACTION_UNSUPPORTED",
                        path=f"world.interactions.{interaction.key}",
                        message="The behavior bundle does not implement this interaction",
                    )
                )
        tool_registry = build_registry()
        for tool_name in bundle.required_tool_names:
            if tool_registry.get(tool_name) is None:
                issues.append(
                    ScenarioValidationIssue(
                        code="SCENARIO_BEHAVIOR_TOOL_UNAVAILABLE",
                        path="behavior_bundle",
                        message=f"Required tool {tool_name} is not registered",
                    )
                )
        return issues

    @staticmethod
    def _validate_objectives(
        definition: ScenarioDefinition,
    ) -> list[ScenarioValidationIssue]:
        facts = {
            (node.key, fact.key): fact for node in definition.world.nodes for fact in node.facts
        }
        objective_keys = {objective.key for objective in definition.objectives}
        issues: list[ScenarioValidationIssue] = []
        subsumption_graph: dict[str, frozenset[str]] = {}
        for objective in definition.objectives:
            path = f"objective_catalog.definitions.{objective.key}"
            requirements = [
                *objective.completion_requirements,
                *(
                    requirement
                    for prerequisite in objective.prerequisites
                    for requirement in prerequisite.requirements
                ),
            ]
            for requirement in requirements:
                fact = facts.get((requirement.node_key, requirement.fact_key))
                if fact is None:
                    issues.append(
                        ScenarioValidationIssue(
                            code="SCENARIO_OBJECTIVE_FACT_NOT_FOUND",
                            path=f"{path}.requirements.{requirement.key}",
                            message="The Objective requirement references an unknown Fact",
                        )
                    )
                    continue
                if fact.value_type == FactValueType.ENUM:
                    allowed = {str(value) for value in fact.allowed_values}
                    unsupported = requirement.accepted_values.difference(allowed)
                    if unsupported:
                        issues.append(
                            ScenarioValidationIssue(
                                code="SCENARIO_OBJECTIVE_VALUE_INVALID",
                                path=f"{path}.requirements.{requirement.key}.accepted_values",
                                message=(
                                    "Accepted Objective values are outside the Fact value domain"
                                ),
                            )
                        )
            missing = objective.subsumes.difference(objective_keys)
            if objective.key in objective.subsumes:
                issues.append(
                    ScenarioValidationIssue(
                        code="SCENARIO_OBJECTIVE_SUBSUMES_SELF",
                        path=f"{path}.subsumes",
                        message="An Objective cannot subsume itself",
                    )
                )
            if missing:
                issues.append(
                    ScenarioValidationIssue(
                        code="SCENARIO_OBJECTIVE_SUBSUMES_NOT_FOUND",
                        path=f"{path}.subsumes",
                        message=f"Unknown subsumed Objectives: {sorted(missing)}",
                    )
                )
            subsumption_graph[objective.key] = objective.subsumes
        issues.extend(_subsumption_cycle_issues(subsumption_graph))
        return issues


def _document_reference_issues(
    document: ScenarioDefinitionDocument,
) -> list[ScenarioValidationIssue]:
    interaction_keys = {interaction.key for interaction in document.world.interactions}
    node_keys = {node.key for node in document.world.nodes}
    issues: list[ScenarioValidationIssue] = []
    for node in document.world.nodes:
        for interaction_key in node.interaction_keys:
            if interaction_key not in interaction_keys:
                issues.append(
                    ScenarioValidationIssue(
                        code="SCENARIO_NODE_INTERACTION_NOT_FOUND",
                        path=f"world.nodes.{node.key}.interaction_keys",
                        message=f"Unknown Interaction {interaction_key}",
                    )
                )
    for index, relation in enumerate(document.world.relations):
        if relation.source_node_key not in node_keys or relation.target_node_key not in node_keys:
            issues.append(
                ScenarioValidationIssue(
                    code="SCENARIO_RELATION_NODE_NOT_FOUND",
                    path=f"world.relations.{index}",
                    message="A Relation endpoint does not exist in the Node catalog",
                )
            )
    return issues


def _subsumption_cycle_issues(
    graph: dict[str, frozenset[str]],
) -> list[ScenarioValidationIssue]:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> bool:
        if key in visiting:
            return True
        if key in visited:
            return False
        visiting.add(key)
        cyclic = any(child in graph and visit(child) for child in graph[key])
        visiting.remove(key)
        visited.add(key)
        return cyclic

    return (
        [
            ScenarioValidationIssue(
                code="SCENARIO_OBJECTIVE_SUBSUMPTION_CYCLE",
                path="objective_catalog.definitions",
                message="Objective subsumption must be acyclic",
            )
        ]
        if any(visit(key) for key in graph)
        else []
    )


__all__ = [
    "ScenarioDefinitionValidator",
    "ScenarioValidationIssue",
    "ScenarioValidationResult",
]
