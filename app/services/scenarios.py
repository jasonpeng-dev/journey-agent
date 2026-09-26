"""Application service for Scenario Draft validation and publication."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.scenario_v2 import (
    ScenarioDefinitionV2,
    normalize_resource_source_hint_document,
)
from app.domain.scenario_v3 import ScenarioDefinitionV3
from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.scenarios.authoring import (
    DraftAuthoringError,
    ReferenceEdge,
    delete_fact,
    delete_nested_object,
    delete_object,
    delete_root_collection_item,
    locator_for_path,
    reference_index,
    rename_key,
    validate_generic_identity_transition,
)
from app.scenarios.completeness import CompletenessResult, evaluate_completeness
from app.scenarios.documents import parse_scenario_document_versioned
from app.scenarios.serialization import canonical_document_payload, scenario_content_hash
from app.scenarios.validation import (
    ScenarioDefinitionValidator,
    ScenarioValidationIssue,
    ScenarioValidationResult,
)
from app.scenarios.versions import ScenarioVersionError, ScenarioVersionRepository


class ScenarioLifecycleError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class ScenarioPublishResult:
    status: Literal["PUBLISHED"]
    version: ScenarioVersion


class ScenarioService:
    def __init__(
        self,
        db: Session,
        validator: ScenarioDefinitionValidator | None = None,
    ) -> None:
        self.db = db
        self.validator = validator or ScenarioDefinitionValidator()

    def list_scenarios(self, *, include_archived: bool = False) -> tuple[Scenario, ...]:
        query = select(Scenario)
        if not include_archived:
            query = query.where(Scenario.status != "ARCHIVED")
        return tuple(self.db.scalars(query.order_by(Scenario.updated_at.desc(), Scenario.key)))

    def get_scenario(self, scenario_id: UUID) -> Scenario:
        return self._scenario(scenario_id, lock=False)

    def get_draft(self, scenario_id: UUID) -> ScenarioDraft:
        self._scenario(scenario_id, lock=False)
        return self._draft(scenario_id, lock=False)

    def list_versions(self, scenario_id: UUID) -> tuple[ScenarioVersion, ...]:
        self._scenario(scenario_id, lock=False)
        return tuple(
            self.db.scalars(
                select(ScenarioVersion)
                .where(ScenarioVersion.scenario_id == scenario_id)
                .order_by(ScenarioVersion.version_number.desc())
            )
        )

    def get_version(self, scenario_id: UUID, version_id: UUID) -> ScenarioVersion:
        self._scenario(scenario_id, lock=False)
        try:
            snapshot = ScenarioVersionRepository(self.db).load(version_id)
        except ScenarioVersionError as exc:
            raise ScenarioLifecycleError(exc.code, exc.message) from exc
        if snapshot.scenario_id != scenario_id:
            raise ScenarioLifecycleError(
                "SCENARIO_VERSION_NOT_FOUND",
                "The ScenarioVersion does not belong to this Scenario",
            )
        version = self.db.get(ScenarioVersion, version_id)
        assert version is not None
        return version

    def create_blank(self, *, key: str, name: str) -> Scenario:
        return self._create(
            key=key,
            name=name,
            definition_document=_blank_document(key=key, name=name),
        )

    def create_from_definition(
        self,
        *,
        key: str,
        name: str,
        definition: ScenarioDefinitionV2,
    ) -> Scenario:
        document = definition.model_dump(mode="json")
        document["metadata"]["key"] = key
        document["metadata"]["name"] = name
        document["world"]["key"] = key
        document["world"]["name"] = name
        return self._create(key=key, name=name, definition_document=document)

    def clone_version(self, *, key: str, name: str, version_id: UUID) -> Scenario:
        version = self.db.get(ScenarioVersion, version_id)
        if version is None:
            raise ScenarioLifecycleError(
                "SCENARIO_VERSION_NOT_FOUND",
                "The source ScenarioVersion does not exist",
            )
        document = deepcopy(version.snapshot_document)
        document["metadata"]["key"] = key
        document["metadata"]["name"] = name
        document["world"]["key"] = key
        document["world"]["name"] = name
        return self._create(
            key=key,
            name=name,
            definition_document=document,
            base_scenario_version_id=version.id,
        )

    def replace_draft(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        definition_document: dict[str, Any],
    ) -> ScenarioDraft:
        """Optimistically replace a Draft without requiring it to be publishable."""

        try:
            normalized_document = normalize_resource_source_hint_document(definition_document)
        except ValueError as exc:
            raise ScenarioLifecycleError(
                "SCENARIO_RESOURCE_SOURCE_HINT_NORMALIZATION_FAILED",
                str(exc),
            ) from exc
        if not isinstance(normalized_document, dict):
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_DOCUMENT_INVALID",
                "The Scenario Draft document must be an object",
            )
        definition_document = normalized_document
        scenario = self._scenario(scenario_id, lock=False)
        self._require_mutable(scenario)
        _require_scenario_identity(definition_document, scenario.key)
        current = self._draft(scenario_id, lock=False)
        if current.revision != expected_revision:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_CONFLICT",
                "The Scenario Draft revision changed before this update",
            )
        try:
            validate_generic_identity_transition(current.definition_document, definition_document)
        except DraftAuthoringError as exc:
            raise ScenarioLifecycleError(
                exc.code,
                exc.message,
                details=_authoring_error_details(exc),
            ) from exc
        return self._replace_draft_document(
            scenario_id,
            expected_revision=expected_revision,
            definition_document=definition_document,
        )

    def _replace_draft_document(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        definition_document: dict[str, Any],
    ) -> ScenarioDraft:
        """Persist a document after an explicit, already-authorized transform."""

        scenario = self._scenario(scenario_id, lock=False)
        self._require_mutable(scenario)
        _require_scenario_identity(definition_document, scenario.key)

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
        metadata = definition_document.get("metadata")
        if isinstance(metadata, dict):
            draft_name = metadata.get("name")
            if isinstance(draft_name, str) and draft_name.strip():
                scenario.name = draft_name
        return draft

    def validate_draft(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int | None = None,
    ) -> ScenarioValidationResult:
        scenario = self._scenario(scenario_id, lock=False)
        self._require_mutable(scenario)
        draft = self._draft(scenario_id, lock=False)
        if expected_revision is not None and draft.revision != expected_revision:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_CONFLICT",
                "The Scenario Draft revision changed before validation",
            )
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
        self._require_mutable(scenario)
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
        assert validation.definition is not None
        if validation.definition.metadata.key != scenario.key:
            raise ScenarioLifecycleError(
                "SCENARIO_KEY_IMMUTABLE",
                "The Draft Scenario key cannot differ from its stable Scenario identity",
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
            raise ScenarioLifecycleError(
                "SCENARIO_PUBLISH_NO_CHANGES",
                "The Draft has no semantic changes from the current published Version",
            )

        latest_number = self.db.scalar(
            select(ScenarioVersion.version_number)
            .where(ScenarioVersion.scenario_id == scenario.id)
            .order_by(ScenarioVersion.version_number.desc())
            .limit(1)
        )
        canonical_payload = canonical_document_payload(draft.definition_document)
        canonical_authored = parse_scenario_document_versioned(canonical_payload)
        canonical = (
            canonical_authored.to_v2()
            if isinstance(canonical_authored, ScenarioDefinitionV3)
            else canonical_authored
        )
        version = ScenarioVersion(
            scenario_id=scenario.id,
            version_number=(latest_number or 0) + 1,
            schema_version=int(canonical_payload["schema_version"]),
            snapshot_document=canonical_payload,
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

    def restore_version(
        self,
        scenario_id: UUID,
        *,
        version_id: UUID,
        expected_revision: int,
    ) -> ScenarioDraft:
        scenario = self._scenario(scenario_id, lock=False)
        self._require_mutable(scenario)
        version = self.get_version(scenario_id, version_id)
        draft = self._replace_draft_document(
            scenario_id,
            expected_revision=expected_revision,
            definition_document=version.snapshot_document,
        )
        draft.base_scenario_version_id = version.id
        self.db.flush()
        return draft

    def archive(self, scenario_id: UUID) -> Scenario:
        scenario = self._scenario(scenario_id, lock=True)
        if scenario.status != "ARCHIVED":
            scenario.status = "ARCHIVED"
            scenario.version += 1
            self.db.flush()
        return scenario

    def references(self, scenario_id: UUID) -> tuple[ReferenceEdge, ...]:
        return reference_index(self.get_draft(scenario_id).definition_document)

    def analyze_working_copy_references(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        definition_document: dict[str, Any],
    ) -> tuple[ReferenceEdge, ...]:
        """Analyze a browser working copy without changing the persisted Draft."""

        self._require_working_copy(
            scenario_id,
            expected_revision=expected_revision,
            definition_document=definition_document,
        )
        return reference_index(definition_document)

    def completeness_working_copy(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        definition_document: dict[str, Any],
    ) -> CompletenessResult:
        """Return unsaved authoring guidance for the browser working copy.

        This is deliberately a read-only preview.  The revision check protects
        the browser from applying guidance to a stale Draft, while the
        evaluator itself consumes the supplied raw document so unrelated
        temporary schema errors do not disable editor guidance.
        """

        self._require_working_copy(
            scenario_id,
            expected_revision=expected_revision,
            definition_document=definition_document,
        )
        return evaluate_completeness(definition_document)

    def transform_working_copy(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        definition_document: dict[str, Any],
        operation_kind: Literal[
            "RENAME_KEY",
            "DELETE_OBJECT",
            "DELETE_FACT",
            "DELETE_ROOT_COLLECTION_ITEM",
            "DELETE_NESTED",
        ],
        object_kind: str,
        old_key: str | None = None,
        new_key: str | None = None,
        object_key: str | None = None,
        node_key: str | None = None,
        fact_key: str | None = None,
        collection: str | None = None,
        identity: str | None = None,
        parent_kind: str | None = None,
        parent_key: str | None = None,
        nested_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply one authoring transform to a client working copy only.

        The persisted Draft revision is checked as an optimistic concurrency
        guard, but this method deliberately does not call ``replace_draft`` or
        commit anything. The browser must explicitly save the returned document.
        """

        draft = self._require_working_copy(
            scenario_id,
            expected_revision=expected_revision,
            definition_document=definition_document,
        )
        try:
            if operation_kind == "RENAME_KEY":
                if old_key is None or new_key is None:
                    raise DraftAuthoringError(
                        "SCENARIO_AUTHORING_OPERATION_INVALID",
                        "RENAME_KEY requires old_key and new_key",
                    )
                return rename_key(
                    definition_document,
                    object_kind=object_kind,
                    old_key=old_key,
                    new_key=new_key,
                    protected_document=self._published_base_document(draft),
                )
            if operation_kind == "DELETE_FACT":
                if object_kind != "node" or node_key is None or fact_key is None:
                    raise DraftAuthoringError(
                        "SCENARIO_AUTHORING_OPERATION_INVALID",
                        "DELETE_FACT requires node object_kind, node_key, and fact_key",
                    )
                return delete_fact(
                    definition_document,
                    node_key=node_key,
                    fact_key=fact_key,
                )
            if operation_kind == "DELETE_ROOT_COLLECTION_ITEM":
                if collection is None or identity is None:
                    raise DraftAuthoringError(
                        "SCENARIO_AUTHORING_OPERATION_INVALID",
                        "DELETE_ROOT_COLLECTION_ITEM requires collection and identity",
                    )
                return delete_root_collection_item(
                    definition_document,
                    collection=collection,
                    identity=identity,
                )
            if operation_kind == "DELETE_NESTED":
                if (
                    parent_kind is None
                    or parent_key is None
                    or collection is None
                    or nested_key is None
                ):
                    raise DraftAuthoringError(
                        "SCENARIO_AUTHORING_OPERATION_INVALID",
                        "DELETE_NESTED requires its scoped identity fields",
                    )
                return delete_nested_object(
                    definition_document,
                    parent_kind=parent_kind,
                    parent_key=parent_key,
                    collection=collection,
                    nested_key=nested_key,
                )
            if object_key is None:
                raise DraftAuthoringError(
                    "SCENARIO_AUTHORING_OPERATION_INVALID",
                    "DELETE_OBJECT requires object_key",
                )
            return delete_object(
                definition_document,
                object_kind=object_kind,
                object_key=object_key,
            )
        except DraftAuthoringError as exc:
            raise ScenarioLifecycleError(
                exc.code,
                exc.message,
                details=_authoring_error_details(exc),
            ) from exc

    def rename_draft_key(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        object_kind: str,
        old_key: str,
        new_key: str,
    ) -> ScenarioDraft:
        draft = self.get_draft(scenario_id)
        try:
            changed = rename_key(
                draft.definition_document,
                object_kind=object_kind,
                old_key=old_key,
                new_key=new_key,
                protected_document=self._published_base_document(draft),
            )
        except DraftAuthoringError as exc:
            raise ScenarioLifecycleError(
                exc.code,
                exc.message,
                details=_authoring_error_details(exc),
            ) from exc
        return self._replace_draft_document(
            scenario_id,
            expected_revision=expected_revision,
            definition_document=changed,
        )

    def delete_draft_object(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        object_kind: str,
        object_key: str,
    ) -> ScenarioDraft:
        draft = self.get_draft(scenario_id)
        try:
            changed = delete_object(
                draft.definition_document,
                object_kind=object_kind,
                object_key=object_key,
            )
        except DraftAuthoringError as exc:
            raise ScenarioLifecycleError(
                exc.code,
                exc.message,
                details=_authoring_error_details(exc),
            ) from exc
        return self._replace_draft_document(
            scenario_id,
            expected_revision=expected_revision,
            definition_document=changed,
        )

    def _require_working_copy(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        definition_document: dict[str, Any],
    ) -> ScenarioDraft:
        scenario = self._scenario(scenario_id, lock=False)
        self._require_mutable(scenario)
        draft = self._draft(scenario_id, lock=False)
        if draft.revision != expected_revision:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_CONFLICT",
                "The Scenario Draft revision changed before working-copy analysis",
            )
        _require_scenario_identity(definition_document, scenario.key)
        return draft

    def _published_base_document(self, draft: ScenarioDraft) -> dict[str, Any] | None:
        if draft.base_scenario_version_id is None:
            return None
        version = self.db.get(ScenarioVersion, draft.base_scenario_version_id)
        return deepcopy(version.snapshot_document) if version is not None else None

    def _create(
        self,
        *,
        key: str,
        name: str,
        definition_document: dict[str, Any],
        base_scenario_version_id: UUID | None = None,
    ) -> Scenario:
        existing = self.db.scalar(select(Scenario.id).where(Scenario.key == key))
        if existing is not None:
            raise ScenarioLifecycleError(
                "SCENARIO_KEY_CONFLICT",
                "The Scenario key is already in use",
            )
        scenario = Scenario(key=key, name=name, status="DRAFT")
        self.db.add(scenario)
        self.db.flush()
        self.db.add(
            ScenarioDraft(
                scenario_id=scenario.id,
                revision=1,
                definition_document=deepcopy(definition_document),
                validation_status="UNVALIDATED",
                validation_errors=[],
                base_scenario_version_id=base_scenario_version_id,
            )
        )
        self.db.flush()
        return scenario

    def _validate_record(self, draft: ScenarioDraft) -> ScenarioValidationResult:
        result = self.validator.validate(draft.definition_document)
        draft.validation_status = "PASSED" if result.passed else "FAILED"
        draft.validation_errors = [
            _issue_payload(issue, draft.definition_document) for issue in result.issues
        ]
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

    def _scenario(self, scenario_id: UUID, *, lock: bool) -> Scenario:
        query = select(Scenario).where(Scenario.id == scenario_id)
        if lock:
            query = query.with_for_update()
        scenario = self.db.scalar(query)
        if scenario is None:
            raise ScenarioLifecycleError("SCENARIO_NOT_FOUND", "The Scenario does not exist")
        return scenario

    @staticmethod
    def _require_mutable(scenario: Scenario) -> None:
        if scenario.status == "ARCHIVED":
            raise ScenarioLifecycleError(
                "SCENARIO_ARCHIVED",
                "An archived Scenario cannot be modified",
            )


def _issue_payload(issue: ScenarioValidationIssue, document: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": issue.code,
        "path": issue.path,
        "message": issue.message,
        "severity": issue.severity,
    }
    locator = locator_for_path(document, issue.path)
    if locator is not None:
        payload["locator"] = {
            "object_kind": locator.object_kind,
            "object_key": locator.object_key,
            "field_path": locator.field_path,
        }
    return payload


def _authoring_error_details(error: DraftAuthoringError) -> dict[str, Any]:
    if not error.references:
        return {}
    return {
        "references": [
            {
                "source": {
                    "object_kind": edge.source.object_kind,
                    "object_key": edge.source.object_key,
                    "field_path": edge.source.field_path,
                },
                "target": {
                    "object_kind": edge.target.object_kind,
                    "object_key": edge.target.object_key,
                    "field_path": edge.target.field_path,
                },
            }
            for edge in error.references
        ]
    }


def _blank_document(*, key: str, name: str) -> dict[str, Any]:
    """Return an intentionally incomplete but Editor-shaped current Draft."""

    return {
        "schema_version": 3,
        "metadata": {"key": key, "name": name, "description": ""},
        "engine_contract": {"key": "declarative-rule-engine", "version": "1"},
        "initialization": {"start_node_key": "", "primary_actor_key": ""},
        "world": {
            "key": key,
            "name": name,
            "node_types": [],
            "nodes": [],
            "relations": [],
            "resources": [],
        },
        "actors": {"roles": [], "actor_profiles": []},
        "interactions": [],
        "actions": [],
        "rules": [],
        "goal_resolution": {"quick_inputs": []},
        "planning": {"instructions": []},
    }


def _require_scenario_identity(document: dict[str, Any], scenario_key: str) -> None:
    for section in ("metadata", "world"):
        value = document.get(section)
        if isinstance(value, dict) and "key" in value and value["key"] != scenario_key:
            raise ScenarioLifecycleError(
                "SCENARIO_KEY_IMMUTABLE",
                "The Draft Scenario key cannot differ from its stable Scenario identity",
            )


__all__ = ["ScenarioLifecycleError", "ScenarioPublishResult", "ScenarioService"]
