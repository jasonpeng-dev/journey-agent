"""Fail-closed exact-version loading for immutable Scenario snapshots."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.domain.scenario import ScenarioVersionSnapshot
from app.infrastructure.db.models import ScenarioVersion
from app.scenarios.documents import (
    SCENARIO_DOCUMENT_SCHEMA_VERSION,
    ScenarioDefinitionDocument,
)
from app.scenarios.serialization import canonical_document, scenario_content_hash


class ScenarioVersionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScenarioVersionRepository:
    """Load only an explicitly identified version; no latest-version lookup exists."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def load(self, scenario_version_id: UUID) -> ScenarioVersionSnapshot:
        record = self.db.get(ScenarioVersion, scenario_version_id)
        if record is None:
            raise ScenarioVersionError(
                "SCENARIO_VERSION_NOT_FOUND",
                "The explicitly requested ScenarioVersion does not exist",
            )
        if record.schema_version != SCENARIO_DOCUMENT_SCHEMA_VERSION:
            raise ScenarioVersionError(
                "SCENARIO_VERSION_SCHEMA_UNSUPPORTED",
                "The ScenarioVersion snapshot schema is not supported",
            )
        try:
            document = ScenarioDefinitionDocument.model_validate(record.snapshot_document)
            canonical = canonical_document(record.snapshot_document)
        except (ValidationError, ValueError) as exc:
            raise ScenarioVersionError(
                "SCENARIO_VERSION_SNAPSHOT_INVALID",
                "The persisted ScenarioVersion snapshot is invalid",
            ) from exc
        canonical_payload = canonical.model_dump(mode="json")
        if record.snapshot_document != canonical_payload:
            raise ScenarioVersionError(
                "SCENARIO_VERSION_SNAPSHOT_NOT_CANONICAL",
                "The persisted ScenarioVersion snapshot is not canonical",
            )
        if scenario_content_hash(record.snapshot_document) != record.content_hash:
            raise ScenarioVersionError(
                "SCENARIO_VERSION_HASH_MISMATCH",
                "The persisted ScenarioVersion snapshot failed integrity verification",
            )
        if (
            document.behavior_bundle.key != record.behavior_bundle_key
            or document.behavior_bundle.version != record.behavior_bundle_version
        ):
            raise ScenarioVersionError(
                "SCENARIO_VERSION_BEHAVIOR_MISMATCH",
                "ScenarioVersion behavior metadata does not match its snapshot",
            )
        return ScenarioVersionSnapshot(
            id=record.id,
            scenario_id=record.scenario_id,
            version_number=record.version_number,
            schema_version=record.schema_version,
            content_hash=record.content_hash,
            published_at=record.published_at,
            definition=document.to_domain(),
        )


__all__ = [
    "ScenarioVersionError",
    "ScenarioVersionRepository",
]
