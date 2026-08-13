"""Persistence boundary for authored Scenario definitions."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.scenario import ScenarioDefinition, ScenarioDefinitionAny
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import Scenario, ScenarioDraft
from app.scenarios.documents import ScenarioDefinitionDocument, parse_scenario_document


class ScenarioPersistenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScenarioDefinitionRepository:
    """Store and load complete definitions without introducing publish semantics."""

    def __init__(self, db: Session):
        self.db = db

    def persist_initial_draft(self, definition: ScenarioDefinitionAny) -> Scenario:
        """Insert one Scenario and its initial Draft, or verify an idempotent seed."""

        existing = self.find_scenario(definition.world.key)
        if existing is not None:
            loaded = self.load_draft(existing.id)
            if loaded != definition:
                raise ScenarioPersistenceError(
                    "SCENARIO_DEFINITION_CONFLICT",
                    "The Scenario key already owns a different persisted definition",
                )
            return existing
        document = _definition_document(definition)
        scenario_key, scenario_name = _definition_identity(definition)
        scenario = Scenario(
            key=scenario_key,
            name=scenario_name,
            status="DRAFT",
        )
        self.db.add(scenario)
        self.db.flush()
        self.db.add(
            ScenarioDraft(
                scenario_id=scenario.id,
                revision=1,
                definition_document=document.model_dump(mode="json"),
                validation_status="UNVALIDATED",
                validation_errors=[],
            )
        )
        self.db.flush()
        return scenario

    def find_scenario(self, scenario_key: str) -> Scenario | None:
        return self.db.scalar(select(Scenario).where(Scenario.key == scenario_key))

    def load_draft(self, scenario_id: UUID) -> ScenarioDefinitionAny:
        draft = self.db.get(ScenarioDraft, scenario_id)
        if draft is None:
            raise ScenarioPersistenceError(
                "SCENARIO_DRAFT_NOT_FOUND",
                "The Scenario does not have a persisted Draft",
            )
        try:
            document = parse_scenario_document(draft.definition_document)
            if isinstance(document, ScenarioDefinitionDocument):
                return document.to_domain()
            return document
        except (ValidationError, ValueError) as exc:
            raise ScenarioPersistenceError(
                "SCENARIO_DEFINITION_INVALID",
                "The persisted Scenario definition cannot be loaded safely",
            ) from exc


def _definition_document(
    definition: ScenarioDefinitionAny,
) -> ScenarioDefinitionDocument | ScenarioDefinitionV2:
    if isinstance(definition, ScenarioDefinition):
        return ScenarioDefinitionDocument.from_domain(definition)
    return definition


def _definition_identity(definition: ScenarioDefinitionAny) -> tuple[str, str]:
    if isinstance(definition, ScenarioDefinition):
        return definition.world.key, definition.world.name
    return definition.metadata.key, definition.metadata.name


__all__ = ["ScenarioDefinitionRepository", "ScenarioPersistenceError"]
