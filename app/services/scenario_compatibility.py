"""Generic current-execution compatibility checks for immutable scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import AgentTask, ScenarioVersion
from app.scenarios.validation import ScenarioDefinitionValidator
from app.scenarios.versions import ScenarioVersionError, ScenarioVersionRepository


@dataclass(frozen=True, slots=True)
class ScenarioExecutionCompatibility:
    """Result of the current generic engine contract check."""

    compatible: bool
    reason_code: str | None = None
    reason: str | None = None


def check_scenario_version_execution_compatibility(
    db: Session,
    scenario_version_id: UUID,
) -> ScenarioExecutionCompatibility:
    """Check schema, engine contract, and publish-time playability generically.

    No scenario key or version number is part of this predicate.  The
    repository verifies the immutable snapshot/hash/engine metadata and the
    validator verifies the current generic readiness contract.
    """

    record = db.get(ScenarioVersion, scenario_version_id)
    if record is None:
        return ScenarioExecutionCompatibility(
            False,
            "SCENARIO_VERSION_NOT_FOUND",
            "The ScenarioVersion does not exist",
        )
    try:
        ScenarioVersionRepository(db).load(scenario_version_id)
    except ScenarioVersionError as exc:
        return ScenarioExecutionCompatibility(False, exc.code, exc.message)
    validation = ScenarioDefinitionValidator().validate(record.snapshot_document)
    if not validation.passed:
        issue = next(
            (item for item in validation.issues if item.severity == "ERROR"),
            None,
        )
        return ScenarioExecutionCompatibility(
            False,
            "SCENARIO_VERSION_NOT_CURRENT_PLAYABLE",
            issue.message if issue is not None else "The ScenarioVersion is not current-playable",
        )
    return ScenarioExecutionCompatibility(True)


def has_legacy_execution_tasks(db: Session, game_instance_id: UUID) -> bool:
    """Return whether an instance contains a Task without current contract data."""

    tasks = db.scalars(
        select(AgentTask).where(AgentTask.game_instance_id == game_instance_id)
    )
    for task in tasks:
        required_fields = (
            task.formal_goal_contract_schema_version,
            task.formal_goal_source_kind,
            task.formal_goal_contract_hash,
            task.formal_goal_scenario_version_id,
            task.formal_goal_scenario_content_hash,
            task.formal_goal_compiler_version,
        )
        if task.formal_goal_contract_json is None or any(
            value is None for value in required_fields
        ):
            return True
    return False


__all__ = [
    "ScenarioExecutionCompatibility",
    "check_scenario_version_execution_compatibility",
    "has_legacy_execution_tasks",
]
