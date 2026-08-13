"""Persistence boundary for authored ScenarioDefinition v2 documents."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import Scenario, ScenarioDraft
from app.scenarios.documents import parse_scenario_document


class ScenarioPersistenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScenarioDefinitionRepository:
    def __init__(self, db: Session):
        self.db = db

    def persist_initial_draft(self, definition: ScenarioDefinitionV2) -> Scenario:
        existing = self.find_scenario(definition.metadata.key)
        if existing is not None:
            if self.load_draft(existing.id) != definition:
                raise ScenarioPersistenceError(
                    "SCENARIO_DEFINITION_CONFLICT",
                    "The Scenario key already owns a different persisted definition",
                )
            return existing
        scenario = Scenario(
            key=definition.metadata.key,
            name=definition.metadata.name,
            status="DRAFT",
        )
        self.db.add(scenario)
        self.db.flush()
        self.db.add(
            ScenarioDraft(
                scenario_id=scenario.id,
                revision=1,
                definition_document=definition.model_dump(mode="json"),
                validation_status="UNVALIDATED",
                validation_errors=[],
            )
        )
        self.db.flush()
        return scenario

    def find_scenario(self, scenario_key: str) -> Scenario | None:
        return self.db.scalar(select(Scenario).where(Scenario.key == scenario_key))

    def load_draft(self, scenario_id: UUID) -> ScenarioDefinitionV2:
        draft = self.db.get(ScenarioDraft, scenario_id)
        if draft is None:
            raise ScenarioPersistenceError(
                "SCENARIO_DRAFT_NOT_FOUND",
                "The Scenario does not have a persisted Draft",
            )
        try:
            return parse_scenario_document(draft.definition_document)
        except (ValidationError, ValueError) as exc:
            raise ScenarioPersistenceError(
                "SCENARIO_DEFINITION_INVALID",
                "The persisted Scenario definition cannot be loaded safely",
            ) from exc


__all__ = ["ScenarioDefinitionRepository", "ScenarioPersistenceError"]
