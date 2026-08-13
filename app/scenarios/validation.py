"""Publish-time validation for generic ScenarioDefinition v2 documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.scenarios.documents import parse_scenario_document


@dataclass(frozen=True, slots=True)
class ScenarioValidationIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ScenarioValidationResult:
    definition: ScenarioDefinitionV2 | None
    issues: tuple[ScenarioValidationIssue, ...]

    @property
    def passed(self) -> bool:
        return self.definition is not None and not self.issues


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
        return ScenarioValidationResult(definition=definition, issues=())


__all__ = [
    "ScenarioDefinitionValidator",
    "ScenarioValidationIssue",
    "ScenarioValidationResult",
]
