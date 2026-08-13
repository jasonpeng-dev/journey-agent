"""Publish-time validation for generic ScenarioDefinition v2 documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from app.domain.scenario_v2 import ScenarioDefinitionV2
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
    objective_facts = {
        (requirement.node_key, requirement.fact_key)
        for objective in definition.objectives
        for requirement in objective.completion_requirements
    }
    projected_facts = {
        (reference.node_key, reference.fact_key)
        for action in definition.actions
        if action.key in resolve_action_keys
        for reference in (*action.planning.terminal_effects, *action.planning.supporting_effects)
    }
    if (
        definition.actions
        and definition.objectives
        and not objective_facts.intersection(projected_facts)
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
