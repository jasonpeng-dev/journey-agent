"""Publish-time validation for generic ScenarioDefinition v2 documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from app.domain.scenario_v2 import DerivedDependencyKind, ScenarioDefinitionV2
from app.scenarios.documents import parse_scenario_document


@dataclass(frozen=True, slots=True)
class ScenarioValidationIssue:
    code: str
    path: str
    message: str
    severity: Literal["ERROR", "WARNING"] = "ERROR"


@dataclass(frozen=True, slots=True)
class ScenarioValidationResult:
    definition: ScenarioDefinitionV2 | None
    issues: tuple[ScenarioValidationIssue, ...]

    @property
    def passed(self) -> bool:
        return self.definition is not None and not any(
            issue.severity == "ERROR" for issue in self.issues
        )


class ScenarioDefinitionValidator:
    def validate(self, document: dict[str, Any]) -> ScenarioValidationResult:
        try:
            definition = parse_scenario_document(document)
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
        except ValueError as exc:
            return ScenarioValidationResult(
                definition=None,
                issues=(
                    ScenarioValidationIssue(
                        code="SCENARIO_DOCUMENT_SCHEMA_UNSUPPORTED",
                        path="schema_version",
                        message=str(exc),
                    ),
                ),
            )
        if (
            definition.engine_contract.key,
            definition.engine_contract.version,
        ) != ("declarative-rule-engine", "1"):
            return ScenarioValidationResult(
                definition=None,
                issues=(
                    ScenarioValidationIssue(
                        code="SCENARIO_ENGINE_CONTRACT_UNAVAILABLE",
                        path="engine_contract",
                        message="The exact generic engine contract is not supported",
                    ),
                ),
            )
        return ScenarioValidationResult(
            definition=definition,
            issues=_readiness_issues(definition),
        )


def _readiness_issues(definition: ScenarioDefinitionV2) -> tuple[ScenarioValidationIssue, ...]:
    issues: list[ScenarioValidationIssue] = []
    if not definition.objectives:
        issues.append(
            ScenarioValidationIssue(
                code="SCENARIO_OBJECTIVE_REQUIRED",
                path="objectives",
                message="A publishable Scenario needs at least one Objective",
            )
        )
    if not definition.actions:
        issues.append(
            ScenarioValidationIssue(
                code="SCENARIO_ACTION_REQUIRED",
                path="actions",
                message="A publishable Scenario needs at least one Action",
            )
        )
    resolve_action_keys = {
        rule.action_key for rule in definition.rules if rule.phase.value == "RESOLVE"
    }
    objective_facts: set[tuple[str, str]] = set()
    objective_resources: set[tuple[str, str]] = set()
    visited_derived: set[str] = set()

    def collect_derived_dependencies(derived_key: str) -> None:
        if derived_key in visited_derived:
            return
        state = definition.derived_state_definitions.get(derived_key)
        if state is None:
            return
        visited_derived.add(derived_key)
        for dependency in state.dependencies:
            if dependency.kind == DerivedDependencyKind.FACT:
                assert dependency.node_key is not None and dependency.fact_key is not None
                objective_facts.add((dependency.node_key, dependency.fact_key))
            elif dependency.kind == DerivedDependencyKind.RESOURCE_AT_LEAST:
                assert dependency.region_key is not None and dependency.resource_key is not None
                objective_resources.add((dependency.region_key, dependency.resource_key))
            elif dependency.kind == DerivedDependencyKind.DERIVED_STATE:
                assert dependency.derived_key is not None
                collect_derived_dependencies(dependency.derived_key)

    for objective in definition.objectives:
        for requirement in objective.completion_requirements:
            if requirement.fact_ref is not None:
                objective_facts.add(requirement.fact_ref)
            if requirement.kind.value == "RESOURCE_AT_LEAST":
                assert requirement.region_key is not None and requirement.resource_key is not None
                objective_resources.add((requirement.region_key, requirement.resource_key))
            if requirement.derived_ref is not None:
                collect_derived_dependencies(requirement.derived_ref)
    projected_facts = {
        (reference.node_key, reference.fact_key)
        for action in definition.actions
        if action.key in resolve_action_keys
        for reference in (*action.planning.terminal_effects, *action.planning.supporting_effects)
    }
    projected_resource_effects: set[tuple[str, str]] = set()
    for action in definition.actions:
        if action.key not in resolve_action_keys:
            continue
        # The readiness contract is based on the Action's public planning
        # projection.  A rule-only resource mutation must not make a Scenario
        # appear playable when its planning projections have been removed.
        if not (action.planning.terminal_effects or action.planning.supporting_effects):
            continue
        for rule in definition.rules:
            if rule.action_key != action.key or rule.phase.value != "RESOLVE":
                continue
            for effect in rule.effects:
                if effect.kind.value != "ADJUST_RESOURCE":
                    continue
                if effect.resource_key is None or effect.resource_scope is None:
                    continue
                if effect.resource_scope.kind.value == "EXPLICIT":
                    if effect.resource_scope.node_key is not None:
                        projected_resource_effects.add(
                            (effect.resource_scope.node_key, effect.resource_key)
                        )
                elif effect.resource_scope.kind.value == "CURRENT_TARGET_REGION":
                    # A localized Action can advance any resource obligation in
                    # its target Region; the runtime resolves the concrete target.
                    projected_resource_effects.update(
                        (region_key, effect.resource_key)
                        for region_key, _resource_key in objective_resources
                        if _resource_key == effect.resource_key
                    )
    if (
        definition.actions
        and definition.objectives
        and not (
            objective_facts.intersection(projected_facts)
            or objective_resources.intersection(projected_resource_effects)
        )
    ):
        issues.append(
            ScenarioValidationIssue(
                code="SCENARIO_MINIMUM_PLAYABLE_REQUIRED",
                path="actions",
                message=(
                    "No resolved Action planning projection advances an Objective requirement"
                ),
            )
        )
    for action in definition.actions:
        if action.key not in resolve_action_keys:
            issues.append(
                ScenarioValidationIssue(
                    code="SCENARIO_ACTION_WITHOUT_RESOLVE_RULE",
                    path=f"actions.{action.key}",
                    message=f"Action {action.key} has no RESOLVE Rule",
                    severity="WARNING",
                )
            )
    return tuple(issues)


__all__ = [
    "ScenarioDefinitionValidator",
    "ScenarioValidationIssue",
    "ScenarioValidationResult",
]
