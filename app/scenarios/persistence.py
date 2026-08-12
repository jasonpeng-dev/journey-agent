"""Persistence boundary for authored Scenario definitions."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.scenario import ScenarioDefinition
from app.infrastructure.db.models import Scenario, ScenarioDraft
from app.scenarios.documents import ScenarioDefinitionDocument


class ScenarioPersistenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScenarioDefinitionRepository:
    """Store and load complete definitions without introducing publish semantics."""

    def __init__(self, db: Session):
        self.db = db

    def persist_initial_draft(self, definition: ScenarioDefinition) -> Scenario:
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
        document = ScenarioDefinitionDocument.from_domain(definition)
        scenario = Scenario(
            key=definition.world.key,
            name=definition.world.name,
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

    def load_draft(self, scenario_id: UUID) -> ScenarioDefinition:
        draft = self.db.get(ScenarioDraft, scenario_id)
        if draft is None:
            raise ScenarioPersistenceError(
                "SCENARIO_DRAFT_NOT_FOUND",
                "The Scenario does not have a persisted Draft",
            )
        try:
            document = ScenarioDefinitionDocument.model_validate(draft.definition_document)
            return document.to_domain()
        except (ValidationError, ValueError) as exc:
            raise ScenarioPersistenceError(
                "SCENARIO_DEFINITION_INVALID",
                "The persisted Scenario definition cannot be loaded safely",
            ) from exc


__all__ = ["ScenarioDefinitionRepository", "ScenarioPersistenceError"]
