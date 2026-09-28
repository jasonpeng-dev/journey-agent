"""Application boundary for portable Scenario artifact operations.

HTTP, CLI and browser adapters must call this service rather than implementing
their own JSON, diff, identity or transaction semantics.  The service owns the
transaction for new Scenario imports; preview operations are intentionally
read-only and operate on detached in-memory documents.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.scenario_v3 import ScenarioDefinitionV3
from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.scenarios.documents import parse_scenario_document_versioned
from app.scenarios.portability import (
    ScenarioArtifact,
    ScenarioArtifactCandidate,
    ScenarioArtifactCodec,
    ScenarioPortabilityError,
    parse_scenario_artifact,
    rebind_root_identity,
)
from app.scenarios.serialization import scenario_content_hash
from app.scenarios.validation import ScenarioDefinitionValidator, ScenarioValidationResult
from app.services.scenarios import ScenarioLifecycleError, ScenarioService


@dataclass(frozen=True, slots=True)
class ImportedScenarioResult:
    """Rows created by a committed new-Scenario import."""

    scenario: Scenario
    draft: ScenarioDraft
    published_version: ScenarioVersion | None
    artifact: ScenarioArtifact
    content_hash: str


@dataclass(frozen=True, slots=True)
class NewScenarioArtifactPreview:
    """Pure preview of the records a new artifact import would create."""

    candidate: ScenarioArtifactCandidate
    target_document: dict[str, Any]
    target_scenario_key: str
    key_conflict: bool
    content_hash: str
    validation: ScenarioValidationResult
    records_to_create: tuple[str, ...]


class ScenarioPortabilityService:
    """Single backend authority for portable Scenario artifact semantics."""

    def __init__(
        self,
        db: Session,
        *,
        scenario_service: ScenarioService | None = None,
        validator: ScenarioDefinitionValidator | None = None,
    ) -> None:
        self.db = db
        self.validator = validator or ScenarioDefinitionValidator()
        self.scenarios = scenario_service or ScenarioService(db, validator=self.validator)

    def export_draft(self, scenario_id: UUID) -> ScenarioArtifact:
        """Export the persisted Draft definition, never an unsaved Working Copy."""

        self.scenarios.get_scenario(scenario_id)
        draft = self.scenarios.get_draft(scenario_id)
        candidate = self._exportable_candidate(draft.definition_document, content_type="draft")
        return candidate.artifact

    def export_release(self, scenario_id: UUID, version_id: UUID) -> ScenarioArtifact:
        """Export one explicitly selected current-schema immutable Version."""

        self.scenarios.get_scenario(scenario_id)
        record = self.db.get(ScenarioVersion, version_id)
        if record is None or record.scenario_id != scenario_id:
            raise ScenarioPortabilityError(
                "SCENARIO_VERSION_NOT_FOUND",
                "The explicitly requested ScenarioVersion does not belong to this Scenario",
                details={"version_id": str(version_id)},
                status_code=404,
            )
        if record.schema_version != 3:
            raise ScenarioPortabilityError(
                "UNSUPPORTED_SCENARIO_SCHEMA_VERSION",
                "Only current schema v3 Published Versions can be exported as portable artifacts",
                details={"schema_version": record.schema_version},
            )
        try:
            # Exact-version repository loading verifies canonicality, hash and
            # engine metadata without exposing the runtime V2 projection as the
            # exported semantic payload.
            self.scenarios.get_version(scenario_id, version_id)
        except ScenarioLifecycleError as exc:
            raise ScenarioPortabilityError(
                exc.code,
                exc.message,
                details=exc.details,
                status_code=404 if exc.code.endswith("_NOT_FOUND") else 409,
            ) from exc
        candidate = self._exportable_candidate(record.snapshot_document, content_type="release")
        return candidate.artifact

    def export_latest_release(self, scenario_id: UUID) -> ScenarioArtifact:
        """Export exactly the Scenario's current Published Version."""

        scenario = self.scenarios.get_scenario(scenario_id)
        if scenario.current_published_version_id is None:
            raise ScenarioPortabilityError(
                "NO_PUBLISHED_VERSION",
                "The Scenario has no published version to export",
                status_code=409,
            )
        return self.export_release(scenario_id, scenario.current_published_version_id)

    def export_draft_json(self, scenario_id: UUID) -> str:
        return ScenarioArtifactCodec.serialize(self.export_draft(scenario_id))

    def export_release_json(self, scenario_id: UUID, version_id: UUID) -> str:
        return ScenarioArtifactCodec.serialize(self.export_release(scenario_id, version_id))

    def preview_new_import(
        self,
        raw: bytes | str | Mapping[str, Any] | ScenarioArtifact | ScenarioArtifactCandidate,
        *,
        target_scenario_key: str | None = None,
    ) -> NewScenarioArtifactPreview:
        """Preview a new Scenario import without creating any database rows."""

        candidate = self._candidate(raw)
        target_document = self._target_document(
            candidate,
            target_scenario_key=target_scenario_key,
            target_scenario_name=None,
        )
        target_key = target_document["metadata"]["key"]
        existing_id = self.db.scalar(select(Scenario.id).where(Scenario.key == target_key))
        validation = self.validator.validate(target_document)
        records: tuple[str, ...] = ("Scenario", "Draft")
        if candidate.content_type == "release":
            records = (*records, "Published Version v1")
        return NewScenarioArtifactPreview(
            candidate=candidate,
            target_document=deepcopy(target_document),
            target_scenario_key=target_key,
            key_conflict=existing_id is not None,
            content_hash=scenario_content_hash(target_document),
            validation=validation,
            records_to_create=records,
        )

    def import_as_new(
        self,
        raw: bytes | str | Mapping[str, Any] | ScenarioArtifact | ScenarioArtifactCandidate,
        *,
        target_scenario_key: str | None = None,
        target_scenario_name: str | None = None,
    ) -> ImportedScenarioResult:
        """Dispatch one parsed artifact to the Draft or Release importer."""

        candidate = self._candidate(raw)
        if candidate.content_type == "draft":
            return self.import_draft_as_new(
                candidate,
                target_scenario_key=target_scenario_key,
                target_scenario_name=target_scenario_name,
            )
        return self.import_release_as_new(
            candidate,
            target_scenario_key=target_scenario_key,
            target_scenario_name=target_scenario_name,
        )

    def import_draft_as_new(
        self,
        raw: bytes | str | Mapping[str, Any] | ScenarioArtifact | ScenarioArtifactCandidate,
        *,
        target_scenario_key: str | None = None,
        target_scenario_name: str | None = None,
    ) -> ImportedScenarioResult:
        candidate = self._candidate(raw)
        self._require_content_type(candidate, "draft")
        target_document = self._target_document(
            candidate,
            target_scenario_key=target_scenario_key,
            target_scenario_name=target_scenario_name,
        )
        target_hash = scenario_content_hash(target_document)

        def operation() -> ImportedScenarioResult:
            scenario = self.scenarios.create_from_authored_document(
                key=target_document["metadata"]["key"],
                name=target_document["metadata"]["name"],
                definition_document=target_document,
            )
            draft = self.scenarios.get_draft(scenario.id)
            imported_artifact = ScenarioArtifactCodec.from_definition(
                target_document,
                content_type="draft",
            )
            return ImportedScenarioResult(
                scenario=scenario,
                draft=draft,
                published_version=None,
                artifact=imported_artifact,
                content_hash=target_hash,
            )

        return self._run_new_import(operation)

    def import_release_as_new(
        self,
        raw: bytes | str | Mapping[str, Any] | ScenarioArtifact | ScenarioArtifactCandidate,
        *,
        target_scenario_key: str | None = None,
        target_scenario_name: str | None = None,
    ) -> ImportedScenarioResult:
        candidate = self._candidate(raw)
        self._require_content_type(candidate, "release")
        target_document = self._target_document(
            candidate,
            target_scenario_key=target_scenario_key,
            target_scenario_name=target_scenario_name,
        )
        validation = self.validator.validate(target_document)
        if not validation.passed:
            raise ScenarioPortabilityError(
                "RELEASE_NOT_PUBLISHABLE",
                "The Release artifact did not pass the current publish/readiness gate",
                details={"issues": _validation_issues(validation)},
            )
        target_hash = scenario_content_hash(target_document)

        def operation() -> ImportedScenarioResult:
            scenario = self.scenarios.create_from_authored_document(
                key=target_document["metadata"]["key"],
                name=target_document["metadata"]["name"],
                definition_document=target_document,
            )
            draft = self.scenarios.get_draft(scenario.id)
            try:
                published = self.scenarios.publish_draft(
                    scenario.id,
                    expected_revision=draft.revision,
                    expected_content_hash=target_hash,
                )
            except ScenarioLifecycleError as exc:
                raise _release_error(exc) from exc
            if published.version.version_number != 1:
                raise ScenarioPortabilityError(
                    "INTERNAL_IMPORT_FAILURE",
                    "A new Release import did not create target Published v1",
                    details={"version_number": published.version.version_number},
                )
            imported_artifact = ScenarioArtifactCodec.from_definition(
                target_document,
                content_type="release",
            )
            return ImportedScenarioResult(
                scenario=scenario,
                draft=draft,
                published_version=published.version,
                artifact=imported_artifact,
                content_hash=target_hash,
            )

        return self._run_new_import(operation)

    def _candidate(
        self,
        raw: bytes | str | Mapping[str, Any] | ScenarioArtifact | ScenarioArtifactCandidate,
    ) -> ScenarioArtifactCandidate:
        if isinstance(raw, ScenarioArtifactCandidate):
            return raw
        if isinstance(raw, ScenarioArtifact):
            return parse_scenario_artifact(ScenarioArtifactCodec.serialize(raw))
        return parse_scenario_artifact(raw)

    @staticmethod
    def _require_content_type(
        candidate: ScenarioArtifactCandidate,
        expected: Literal["draft", "release"],
    ) -> None:
        if candidate.content_type != expected:
            raise ScenarioPortabilityError(
                "INVALID_CONTENT_TYPE",
                f"This operation requires a {expected} Scenario artifact",
                details={"expected": expected, "actual": candidate.content_type},
            )

    @staticmethod
    def _target_document(
        candidate: ScenarioArtifactCandidate,
        *,
        target_scenario_key: str | None,
        target_scenario_name: str | None,
    ) -> dict[str, Any]:
        key = candidate.scenario_key if target_scenario_key is None else target_scenario_key
        return rebind_root_identity(
            candidate.definition,
            target_scenario_key=key,
            target_scenario_name=target_scenario_name,
        )

    def _exportable_candidate(
        self,
        document: Mapping[str, Any],
        *,
        content_type: Literal["draft", "release"],
    ) -> ScenarioArtifactCandidate:
        if document.get("schema_version") != 3:
            raise ScenarioPortabilityError(
                "UNSUPPORTED_SCENARIO_SCHEMA_VERSION",
                "Only current schema v3 definitions can be exported as portable artifacts",
                details={"schema_version": document.get("schema_version")},
            )
        try:
            authored = parse_scenario_document_versioned(dict(document))
        except (ValidationError, ValueError) as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The persisted Scenario document cannot be exported",
                details={"reason": str(exc)},
            ) from exc
        if not isinstance(authored, ScenarioDefinitionV3):
            raise ScenarioPortabilityError(
                "UNSUPPORTED_SCENARIO_SCHEMA_VERSION",
                "Only current schema v3 definitions can be exported as portable artifacts",
                details={"schema_version": authored.schema_version},
            )
        artifact = ScenarioArtifactCodec.from_definition(authored, content_type=content_type)
        return parse_scenario_artifact(ScenarioArtifactCodec.serialize(artifact))

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        # A clean Session gets the importer-owned root transaction.  Test
        # fixtures and future request middleware may already have a transaction;
        # a savepoint keeps this operation atomic within that caller boundary.
        if self.db.in_transaction():
            with self.db.begin_nested():
                yield
        else:
            with self.db.begin():
                yield

    def _run_new_import(
        self, operation: Callable[[], ImportedScenarioResult]
    ) -> ImportedScenarioResult:
        try:
            with self._transaction():
                result = operation()
                return result
        except ScenarioPortabilityError:
            raise
        except ScenarioLifecycleError as exc:
            if exc.code == "SCENARIO_KEY_CONFLICT":
                raise ScenarioPortabilityError(
                    "SCENARIO_KEY_CONFLICT",
                    "The target Scenario key is already in use",
                    status_code=409,
                ) from exc
            raise ScenarioPortabilityError(
                "INTERNAL_IMPORT_FAILURE",
                "The Scenario import failed and was rolled back",
                details={"source_code": exc.code},
            ) from exc
        except IntegrityError as exc:
            raise ScenarioPortabilityError(
                "SCENARIO_KEY_CONFLICT",
                "The target Scenario key is already in use",
                status_code=409,
            ) from exc
        except Exception as exc:
            raise ScenarioPortabilityError(
                "INTERNAL_IMPORT_FAILURE",
                "The Scenario import failed and was rolled back",
            ) from exc


def _validation_issues(result: ScenarioValidationResult) -> list[dict[str, object]]:
    return [
        {
            "code": issue.code,
            "path": issue.path,
            "message": issue.message,
            "severity": issue.severity,
            "type": issue.type,
        }
        for issue in result.issues
    ]


def _release_error(exc: ScenarioLifecycleError) -> ScenarioPortabilityError:
    if exc.code in {"SCENARIO_DRAFT_INVALID", "SCENARIO_DRAFT_HASH_MISMATCH"}:
        return ScenarioPortabilityError(
            "RELEASE_NOT_PUBLISHABLE",
            "The imported Release failed the current publication gate",
            details=exc.details,
        )
    return ScenarioPortabilityError(
        "INTERNAL_IMPORT_FAILURE",
        "The Release import failed and was rolled back",
        details={"source_code": exc.code, **exc.details},
    )


__all__ = [
    "ImportedScenarioResult",
    "NewScenarioArtifactPreview",
    "ScenarioPortabilityService",
]
