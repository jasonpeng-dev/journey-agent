"""Application service for Scenario Draft validation and publication."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.scenarios.serialization import canonical_document, scenario_content_hash
from app.scenarios.validation import (
    ScenarioDefinitionValidator,
    ScenarioValidationIssue,
    ScenarioValidationResult,
)


class ScenarioLifecycleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ScenarioPublishResult:
    status: Literal["PUBLISHED", "NO_CHANGES"]
    version: ScenarioVersion


class ScenarioService:
    def __init__(
        self,
        db: Session,
        validator: ScenarioDefinitionValidator | None = None,
    ) -> None:
        self.db = db
        self.validator = validator or ScenarioDefinitionValidator()

    def replace_draft(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        definition_document: dict[str, Any],
    ) -> ScenarioDraft:
        """Optimistically replace a Draft without requiring it to be publishable."""

        changed = self.db.execute(
            update(ScenarioDraft)
            .where(
                ScenarioDraft.scenario_id == scenario_id,
                ScenarioDraft.revision == expected_revision,
            )
            .values(
                revision=ScenarioDraft.revision + 1,
                definition_document=deepcopy(definition_document),
                validation_status="UNVALIDATED",
                validation_errors=[],
                content_hash=None,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(changed, "rowcount", 0) != 1:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_CONFLICT",
                "The Scenario Draft revision changed before this update",
            )
        self.db.flush()
        draft = self.db.get(ScenarioDraft, scenario_id)
        assert draft is not None
        self.db.refresh(draft)
        return draft

    def validate_draft(self, scenario_id: UUID) -> ScenarioValidationResult:
        draft = self._draft(scenario_id, lock=False)
        result = self._validate_record(draft)
        self.db.flush()
        return result

    def publish_draft(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        expected_content_hash: str | None = None,
    ) -> ScenarioPublishResult:
        """Validate and publish one Draft in the caller's atomic transaction."""

        scenario = self.db.scalar(
            select(Scenario).where(Scenario.id == scenario_id).with_for_update()
        )
        if scenario is None:
            raise ScenarioLifecycleError(
                "SCENARIO_NOT_FOUND",
                "The Scenario does not exist",
            )
        draft = self._draft(scenario_id, lock=True)
        if draft.revision != expected_revision:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_CONFLICT",
                "The Scenario Draft revision changed before publication",
            )
        validation = self._validate_record(draft)
        if not validation.passed:
            self.db.flush()
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_INVALID",
                "The Scenario Draft did not pass publication validation",
            )
        content_hash = scenario_content_hash(draft.definition_document)
        if expected_content_hash is not None and expected_content_hash != content_hash:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_HASH_MISMATCH",
                "The validated Draft content changed before publication",
            )
        current = (
            self.db.get(ScenarioVersion, scenario.current_published_version_id)
            if scenario.current_published_version_id is not None
            else None
        )
        if current is not None and current.content_hash == content_hash:
            draft.base_scenario_version_id = current.id
            return ScenarioPublishResult(status="NO_CHANGES", version=current)

        latest_number = self.db.scalar(
            select(ScenarioVersion.version_number)
            .where(ScenarioVersion.scenario_id == scenario.id)
            .order_by(ScenarioVersion.version_number.desc())
            .limit(1)
        )
        canonical = canonical_document(draft.definition_document)
        version = ScenarioVersion(
            scenario_id=scenario.id,
            version_number=(latest_number or 0) + 1,
            schema_version=canonical.schema_version,
            snapshot_document=canonical.model_dump(mode="json"),
            content_hash=content_hash,
            engine_contract_key=canonical.engine_contract.key,
            engine_contract_version=canonical.engine_contract.version,
            published_at=datetime.now(UTC),
        )
        self.db.add(version)
        self.db.flush()
        scenario.current_published_version_id = version.id
        scenario.status = "PUBLISHED"
        scenario.version += 1
        draft.base_scenario_version_id = version.id
        self.db.flush()
        return ScenarioPublishResult(status="PUBLISHED", version=version)

    def _validate_record(self, draft: ScenarioDraft) -> ScenarioValidationResult:
        result = self.validator.validate(draft.definition_document)
        draft.validation_status = "PASSED" if result.passed else "FAILED"
        draft.validation_errors = [_issue_payload(issue) for issue in result.issues]
        draft.content_hash = (
            scenario_content_hash(draft.definition_document) if result.passed else None
        )
        return result

    def _draft(self, scenario_id: UUID, *, lock: bool) -> ScenarioDraft:
        query = select(ScenarioDraft).where(ScenarioDraft.scenario_id == scenario_id)
        if lock:
            query = query.with_for_update()
        draft = self.db.scalar(query)
        if draft is None:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_NOT_FOUND",
                "The Scenario does not have a Draft",
            )
        return draft


def _issue_payload(issue: ScenarioValidationIssue) -> dict[str, str]:
    return {
        "code": issue.code,
        "path": issue.path,
        "message": issue.message,
    }


__all__ = ["ScenarioLifecycleError", "ScenarioPublishResult", "ScenarioService"]
