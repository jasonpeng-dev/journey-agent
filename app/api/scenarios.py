"""HTTP adapter for the authored Scenario lifecycle."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.provider import GenericProviderError
from app.api.schemas.phase_d import (
    CompletenessItemResponse,
    DraftCompletenessRequest,
    DraftCompletenessResponse,
    DraftDeleteObjectRequest,
    DraftPublishRequest,
    DraftReferenceAnalysisRequest,
    DraftReferenceAnalysisResponse,
    DraftRenameKeyRequest,
    DraftReplaceRequest,
    DraftResponse,
    DraftRevisionRequest,
    DraftSandboxRequest,
    DraftSandboxResponse,
    DraftTransformRequest,
    DraftTransformResponse,
    DraftValidationResponse,
    InitializationPreviewRequest,
    PresentationProfileHistoryResponse,
    PresentationProfileReplaceRequest,
    PresentationProfileResponse,
    PresentationProfileRestoreRequest,
    PresentationProfileRevisionCheckResponse,
    ReadinessCheckResponse,
    ReadinessLevel,
    ReferenceEdgeResponse,
    ReferenceIndexResponse,
    RestorePreviewRequest,
    RestorePreviewResponse,
    ScenarioCreateMode,
    ScenarioCreateRequest,
    ScenarioDeletionImpactResponse,
    ScenarioDependentGameResponse,
    ScenarioDetailResponse,
    ScenarioExampleResponse,
    ScenarioPublishResponse,
    ScenarioStatus,
    ScenarioSummaryResponse,
    ScenarioVersionDetailResponse,
    ScenarioVersionSummaryResponse,
    SemanticDiffRequest,
    SemanticDiffResponse,
    ValidationIssueResponse,
    ValidationSeverity,
)
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.scenario_v2 import (
    normalize_resource_source_hint_document,
)
from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.infrastructure.db.session import get_db
from app.scenarios.authoring import ReferenceEdge, locator_for_path, reference_index
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
from app.scenarios.documents import parse_scenario_document
from app.scenarios.initialization import bootstrap_parity, initialization_projection
from app.scenarios.semantic_diff import semantic_diff
from app.scenarios.validation import ScenarioValidationIssue
from app.services.draft_sandbox import DraftSandboxService
from app.services.presentation_profiles import (
    PresentationProfileLifecycleError,
    PresentationProfileService,
)
from app.services.scenarios import ScenarioLifecycleError, ScenarioService

router = APIRouter(prefix="/api/v1", tags=["scenarios"])

_EXAMPLES = {
    "linjiang_infrastructure_recovery_v2_0": (
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        ReadinessLevel.PUBLISH_READY,
    ),
}


@router.get("/scenarios", response_model=list[ScenarioSummaryResponse])
def list_scenarios(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[ScenarioSummaryResponse]:
    service = ScenarioService(db)
    scenarios = service.list_scenarios(include_archived=include_archived)
    return [_scenario_summary(db, item) for item in scenarios]


@router.post(
    "/scenarios",
    response_model=ScenarioDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_scenario(
    request: ScenarioCreateRequest,
    db: Session = Depends(get_db),
) -> ScenarioDetailResponse:
    service = ScenarioService(db)
    try:
        if request.mode == ScenarioCreateMode.BLANK:
            scenario = service.create_blank(key=request.key, name=request.name)
        elif request.mode == ScenarioCreateMode.CLONE_VERSION:
            assert request.source_version_id is not None
            scenario = service.clone_version(
                key=request.key,
                name=request.name,
                version_id=request.source_version_id,
            )
        else:
            assert request.example_key is not None
            example = _EXAMPLES.get(request.example_key)
            if example is None:
                raise ScenarioLifecycleError(
                    "SCENARIO_EXAMPLE_NOT_FOUND",
                    "The requested Scenario example does not exist",
                )
            scenario = service.create_from_definition(
                key=request.key,
                name=request.name,
                definition=example[0],
            )
        db.commit()
        return _scenario_detail(db, scenario)
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDetailResponse)
def get_scenario(scenario_id: UUID, db: Session = Depends(get_db)) -> ScenarioDetailResponse:
    try:
        return _scenario_detail(db, ScenarioService(db).get_scenario(scenario_id))
    except ScenarioLifecycleError as exc:
        _raise_http(exc)


@router.post("/scenarios/{scenario_id}/archive", response_model=ScenarioDetailResponse)
def archive_scenario(scenario_id: UUID, db: Session = Depends(get_db)) -> ScenarioDetailResponse:
    try:
        scenario = ScenarioService(db).archive(scenario_id)
        db.commit()
        return _scenario_detail(db, scenario)
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc)


@router.get("/scenarios/{scenario_id}/draft", response_model=DraftResponse)
def get_draft(scenario_id: UUID, db: Session = Depends(get_db)) -> DraftResponse:
    try:
        return _draft_response(ScenarioService(db).get_draft(scenario_id))
    except ScenarioLifecycleError as exc:
        _raise_http(exc)


@router.put("/scenarios/{scenario_id}/draft", response_model=DraftResponse)
def replace_draft(
    scenario_id: UUID,
    request: DraftReplaceRequest,
    db: Session = Depends(get_db),
) -> DraftResponse:
    return _draft_write(
        db,
        lambda service: service.replace_draft(
            scenario_id,
            expected_revision=request.expected_revision,
            definition_document=request.definition_document,
        ),
    )


@router.post("/scenarios/{scenario_id}/draft/validate", response_model=DraftValidationResponse)
def validate_draft(
    scenario_id: UUID,
    request: DraftRevisionRequest,
    db: Session = Depends(get_db),
) -> DraftValidationResponse:
    service = ScenarioService(db)
    try:
        result = service.validate_draft(
            scenario_id,
            expected_revision=request.expected_revision,
        )
        draft = service.get_draft(scenario_id)
        db.commit()
        issues = [_validation_issue(item, draft.definition_document) for item in result.issues]
        structural = result.definition is not None
        runnable = structural
        playable = result.passed
        return DraftValidationResponse(
            scenario_id=scenario_id,
            revision=draft.revision,
            content_hash=draft.content_hash,
            issues=issues,
            readiness=[
                ReadinessCheckResponse(
                    level=ReadinessLevel.STRUCTURALLY_VALID,
                    passed=structural,
                    issue_codes=[item.code for item in result.issues if not structural],
                ),
                ReadinessCheckResponse(level=ReadinessLevel.MINIMUM_RUNNABLE, passed=runnable),
                ReadinessCheckResponse(
                    level=ReadinessLevel.MINIMUM_PLAYABLE,
                    passed=playable,
                    issue_codes=[item.code for item in result.issues if item.severity == "ERROR"],
                ),
                ReadinessCheckResponse(
                    level=ReadinessLevel.PUBLISH_READY,
                    passed=result.passed,
                    issue_codes=[item.code for item in result.issues if item.severity == "ERROR"],
                ),
            ],
            publish_ready=result.passed,
        )
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc)


@router.post(
    "/scenarios/{scenario_id}/draft/initialization-preview",
    response_model=dict[str, Any],
)
def preview_initialization(
    scenario_id: UUID,
    request: InitializationPreviewRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Project a working copy without saving, publishing, or creating a Game."""

    service = ScenarioService(db)
    try:
        draft = service.get_draft(scenario_id)
        if draft.revision != request.expected_revision:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_CONFLICT",
                "The Scenario Draft revision changed before initialization preview",
            )
        definition = parse_scenario_document(request.definition_document)
        scenario = service.get_scenario(scenario_id)
        published = None
        if scenario.current_published_version_id is not None:
            version = service.get_version(scenario_id, scenario.current_published_version_id)
            published = parse_scenario_document(version.snapshot_document)
        return {
            "revision": draft.revision,
            "projection": initialization_projection(definition, request.definition_document),
            "parity": bootstrap_parity(definition, published),
        }
    except ValidationError as exc:
        focus = request.focus
        validation_issues = exc.errors(include_url=False, include_context=False)
        partial_document = _document_without_unfocused_invalid_entities(
            request.definition_document,
            validation_issues,
            focus.object_kind if focus is not None else None,
            focus.object_key if focus is not None else None,
        )
        if partial_document is not None:
            try:
                partial_definition = parse_scenario_document(partial_document)
            except ValidationError:
                pass
            else:
                projection = initialization_projection(partial_definition, partial_document)
                return {
                    "revision": draft.revision,
                    "projection": _focus_initialization_projection(
                        projection, focus.object_kind, focus.object_key
                    )
                    if focus is not None
                    else projection,
                    "parity": {
                        "published": False,
                        "initialization_changes": [],
                        "design_changes": [],
                    },
                    "partial": True,
                    "omitted_issue_count": len(exc.errors()),
                    "issues": _initialization_preview_issues(
                        request.definition_document, validation_issues
                    ),
                }
        raise AppError(
            code="SCENARIO_INITIALIZATION_PREVIEW_INVALID",
            message="The working document cannot produce an initialization preview",
            status_code=422,
            details={
                "issues": _initialization_preview_issues(
                    request.definition_document, validation_issues
                )
            },
        ) from exc
    except ScenarioLifecycleError as exc:
        _raise_http(exc)


@router.post(
    "/scenarios/{scenario_id}/draft/semantic-diff",
    response_model=SemanticDiffResponse,
)
def compare_draft_semantics(
    scenario_id: UUID,
    request: SemanticDiffRequest,
    db: Session = Depends(get_db),
) -> SemanticDiffResponse:
    """Compare a read-only Working Copy with the explicit current Published version."""

    service = ScenarioService(db)
    draft = service.get_draft(scenario_id)
    if draft.revision != request.expected_revision:
        raise AppError(
            "SCENARIO_DRAFT_CONFLICT",
            "The Scenario Draft revision changed before semantic comparison",
            status_code=status.HTTP_409_CONFLICT,
        )
    scenario = service.get_scenario(scenario_id)
    if scenario.current_published_version_id is None:
        return SemanticDiffResponse(
            published=False,
            compared_version=None,
            published_version_id=None,
            published_version_number=None,
            published_schema_version=None,
            is_equal=None,
            comparable=False,
            total_changed_objects=0,
            total_changed_items=0,
            section_summaries=[],
            entries=[],
        )
    version = service.get_version(scenario_id, scenario.current_published_version_id)
    try:
        result = semantic_diff(
            request.definition_document,
            version.snapshot_document,
            published_version_id=str(version.id),
            published_version_number=version.version_number,
            published_schema_version=version.schema_version,
            include_entries=request.include_entries,
        )
    except ValidationError:
        # An incomplete Working Copy is a valid authoring state.  It is not a
        # semantic equality result and must not manufacture a baseline diff.
        result = {
            "published": True,
            "compared_version": {
                "id": str(version.id),
                "version_number": version.version_number,
                "schema_version": version.schema_version,
            },
            "published_version_id": str(version.id),
            "published_version_number": version.version_number,
            "published_schema_version": version.schema_version,
            "is_equal": None,
            "comparable": False,
            "total_changed_objects": 0,
            "total_changed_items": 0,
            "section_summaries": [],
            "entries": [],
            "error_message": "当前工作副本尚未形成可比较的完整语义。",
        }
    return SemanticDiffResponse.model_validate(result)


_INITIALIZATION_PREVIEW_COLLECTIONS: dict[tuple[str, ...], str] = {
    ("world", "nodes"): "node",
    ("world", "node_types"): "node_type",
    ("world", "relations"): "relation",
    ("world", "relation_types"): "relation_type",
    ("world", "resources"): "resource",
    ("actors", "roles"): "role",
    ("actors", "actor_profiles"): "actor",
    ("interactions",): "interaction",
    ("actions",): "action",
    ("rules",): "rule",
    ("objectives",): "objective",
    ("derived_states",): "derived_state",
    ("public_references",): "public_reference",
    ("initialization", "resource_pools"): "resource_pool",
    ("initialization", "region_resource_knowledge"): "region_resource_knowledge",
    ("initialization", "resource_initial_states"): "legacy_resource",
}

_INITIALIZATION_REFERENCE_ISSUE_PATTERNS: tuple[
    tuple[re.Pattern[str], tuple[str, ...], str, str, str], ...
] = (
    (
        re.compile(r"^Node ([a-z][a-z0-9_]*) type references unknown key\b", re.I),
        ("world", "nodes"),
        "node",
        "node_type_key",
        "node-types",
    ),
    (
        re.compile(r"^Node ([a-z][a-z0-9_]*) Interaction references unknown key\b", re.I),
        ("world", "nodes"),
        "node",
        "interaction_keys",
        "interactions",
    ),
    (
        re.compile(r"^Actor ([a-z][a-z0-9_]*) Role references unknown key\b", re.I),
        ("actors", "actor_profiles"),
        "actor",
        "role_key",
        "roles",
    ),
    (
        re.compile(r"^Actor ([a-z][a-z0-9_]*) initial Node references unknown key\b", re.I),
        ("actors", "actor_profiles"),
        "actor",
        "initial_node_key",
        "world-entities",
    ),
    (
        re.compile(r"^Actor ([a-z][a-z0-9_]*) allowed Action references unknown key\b", re.I),
        ("actors", "actor_profiles"),
        "actor",
        "allowed_action_keys",
        "actions",
    ),
    (
        re.compile(r"^Action ([a-z][a-z0-9_]*) Interaction references unknown key\b", re.I),
        ("actions",),
        "action",
        "required_interaction_key",
        "interactions",
    ),
    (
        re.compile(r"^Action ([a-z][a-z0-9_]*) target Node type references unknown key\b", re.I),
        ("actions",),
        "action",
        "target_node_type_keys",
        "node-types",
    ),
    (
        re.compile(r"^Action ([a-z][a-z0-9_]*) required Actor Role references unknown key\b", re.I),
        ("actions",),
        "action",
        "required_actor_role_key",
        "roles",
    ),
    (
        re.compile(
            r"^Action ([a-z][a-z0-9_]*) source Relation Type references unknown key\b",
            re.I,
        ),
        ("actions",),
        "action",
        "source_relation_type_key",
        "relation-types",
    ),
)


def _initialization_reference_issue_target(
    document: dict[str, Any], issue: Mapping[str, Any]
) -> dict[str, Any] | None:
    message = issue.get("msg")
    if not isinstance(message, str):
        return None
    normalized_message = re.sub(r"^Value error,\s*", "", message, flags=re.I)
    for (
        pattern,
        collection_path,
        kind,
        field_path,
        reference_owner,
    ) in _INITIALIZATION_REFERENCE_ISSUE_PATTERNS:
        match = pattern.match(normalized_message)
        if not match:
            continue
        identity = match.group(1)
        collection: Any = document
        for component in collection_path:
            collection = collection.get(component) if isinstance(collection, dict) else None
        if not isinstance(collection, list):
            return None
        matches = [
            index
            for index, item in enumerate(collection)
            if isinstance(item, dict) and item.get("key") == identity
        ]
        if len(matches) != 1:
            return None
        return {
            "collection_path": collection_path,
            "index": matches[0],
            "kind": kind,
            "identity": identity,
            "field_path": field_path,
            "canonical_owner": "world-entities"
            if kind == "node"
            else ("actors" if kind == "actor" else "actions"),
            "reference_owner": reference_owner,
        }
    return None


def _document_without_unfocused_invalid_entities(
    document: dict[str, Any],
    issues: Sequence[Mapping[str, Any]],
    focus_kind: str | None,
    focus_key: str | None,
) -> dict[str, Any] | None:
    """Drop only malformed, unrelated entity rows for a current-object projection.

    This read-only projection aid never changes the submitted Working Copy and
    refuses to prune singleton configuration or the focused entity.
    """

    candidate = deepcopy(document)
    removals: dict[tuple[str, ...], set[int]] = {}
    for issue in issues:
        location = issue.get("loc")
        if not isinstance(location, (tuple, list)):
            return None
        parts = list(location)
        if parts and parts[0] == "body":
            parts.pop(0)
        collection_path = next(
            (
                path
                for path in _INITIALIZATION_PREVIEW_COLLECTIONS
                if tuple(parts[: len(path)]) == path
            ),
            None,
        )
        index_part = (
            parts[len(collection_path)]
            if collection_path is not None and len(parts) > len(collection_path)
            else None
        )
        reference_target: dict[str, Any] | None = None
        if (
            collection_path is None
            or not isinstance(index_part, int)
            or isinstance(index_part, bool)
        ):
            reference_target = _initialization_reference_issue_target(document, issue)
            if reference_target is None:
                return None
            collection_path = reference_target["collection_path"]
            index_part = reference_target["index"]
        collection: Any = candidate
        for key in collection_path:
            if not isinstance(collection, dict):
                return None
            collection = collection.get(key)
        if not isinstance(collection, list) or not (0 <= index_part < len(collection)):
            return None
        item = collection[index_part]
        if not isinstance(item, dict):
            return None
        kind = _INITIALIZATION_PREVIEW_COLLECTIONS[collection_path]
        key = item.get("key")
        if kind == "relation":
            key = item.get("key") or relation_identity_from_document(item)
        elif kind == "resource_pool":
            key = item.get("pool_key")
        elif kind == "region_resource_knowledge":
            key = item.get("region_key")
        elif kind == "legacy_resource":
            key = item.get("resource_key")
        if focus_kind is not None and kind == focus_kind and key == focus_key:
            return None
        removals.setdefault(collection_path, set()).add(index_part)

    if not removals:
        return None
    for collection_path, indexes in removals.items():
        collection = candidate
        for key in collection_path:
            collection = collection[key]
        for index in sorted(indexes, reverse=True):
            collection.pop(index)
    return candidate


def _initialization_preview_issues(
    document: dict[str, Any], issues: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Attach stable authoring identity and canonical locator to validation evidence."""

    owner_by_path = {
        ("world", "nodes"): "world-entities",
        ("world", "node_types"): "node-types",
        ("world", "relations"): "relations",
        ("world", "relation_types"): "relation-types",
        ("world", "resources"): "resources",
        ("actors", "roles"): "roles",
        ("actors", "actor_profiles"): "actors",
        ("interactions",): "interactions",
        ("actions",): "actions",
        ("rules",): "rules",
        ("objectives",): "objectives",
        ("derived_states",): "derived-states",
        ("public_references",): "public-references",
        ("initialization", "resource_pools"): "initialization",
        ("initialization", "region_resource_knowledge"): "initialization",
        ("initialization", "resource_initial_states"): "initialization",
    }
    kind_by_path = {
        ("world", "nodes"): "node",
        ("world", "node_types"): "node_type",
        ("world", "relations"): "relation",
        ("world", "relation_types"): "relation_type",
        ("world", "resources"): "resource",
        ("actors", "roles"): "role",
        ("actors", "actor_profiles"): "actor",
        ("interactions",): "interaction",
        ("actions",): "action",
        ("rules",): "rule",
        ("objectives",): "objective",
        ("derived_states",): "derived_state",
        ("public_references",): "public_reference",
        ("initialization", "resource_pools"): "resource_pool",
        ("initialization", "region_resource_knowledge"): "region_resource_knowledge",
        ("initialization", "resource_initial_states"): "legacy_resource",
    }

    enriched: list[dict[str, Any]] = []
    for issue in issues:
        result = dict(issue)
        raw_location = issue.get("loc")
        parts = list(raw_location) if isinstance(raw_location, (tuple, list)) else []
        if parts and parts[0] == "body":
            parts.pop(0)
        collection_path = next(
            (path for path in owner_by_path if tuple(parts[: len(path)]) == path), None
        )
        if collection_path is not None:
            index = parts[len(collection_path)] if len(parts) > len(collection_path) else None
            if isinstance(index, int) and not isinstance(index, bool):
                collection: Any = document
                for component in collection_path:
                    collection = collection.get(component) if isinstance(collection, dict) else None
                if isinstance(collection, list) and 0 <= index < len(collection):
                    item = collection[index]
                    if isinstance(item, dict):
                        kind = kind_by_path[collection_path]
                        key: Any = item.get("key")
                        if kind == "relation":
                            key = item.get("key") or relation_identity_from_document(item)
                        elif kind == "resource_pool":
                            key = item.get("pool_key")
                        elif kind == "region_resource_knowledge":
                            key = item.get("region_key")
                        elif kind == "legacy_resource":
                            key = item.get("resource_key")
                        rest = [str(part) for part in parts[len(collection_path) + 1 :]]
                        identity_kind = kind
                        if kind == "node" and len(rest) >= 2 and rest[0] == "facts":
                            facts = item.get("facts")
                            fact_index = rest[1]
                            if isinstance(facts, list) and fact_index.isdigit():
                                fact = (
                                    facts[int(fact_index)] if int(fact_index) < len(facts) else None
                                )
                                if isinstance(fact, dict) and isinstance(fact.get("key"), str):
                                    identity_kind = "fact"
                                    key = f"{key}:{fact['key']}"
                                    rest = ["facts", fact["key"], *rest[2:]]
                        field_path = ".".join(rest) or None
                        result.update(
                            {
                                "identity": f"{identity_kind}:{key}" if key else None,
                                "canonical_owner": owner_by_path[collection_path],
                                "field_path": field_path,
                                "locator": {
                                    "section": owner_by_path[collection_path],
                                    "object_kind": kind,
                                    "object_key": str(key) if key is not None else None,
                                    "field_path": field_path,
                                },
                            }
                        )
        if "locator" not in result:
            reference_target = _initialization_reference_issue_target(document, issue)
            if reference_target is not None:
                result.update(
                    {
                        "identity": f"{reference_target['kind']}:{reference_target['identity']}",
                        "canonical_owner": reference_target["canonical_owner"],
                        "field_path": reference_target["field_path"],
                        "reference_owner": reference_target["reference_owner"],
                        "locator": {
                            "section": reference_target["canonical_owner"],
                            "object_kind": reference_target["kind"],
                            "object_key": reference_target["identity"],
                            "field_path": reference_target["field_path"],
                        },
                    }
                )
                enriched.append(result)
                continue
            root = parts[0] if parts else ""
            root_owner = {
                "metadata": "overview",
                "initialization": "initialization",
                "planning": "planning-instructions",
                "goal_resolution": "goal-resolution",
            }.get(str(root), "configuration-check")
            field_path = ".".join(str(part) for part in parts[1:]) or None
            result.update(
                {
                    "identity": str(root) if root else None,
                    "canonical_owner": root_owner,
                    "field_path": field_path,
                    "locator": {
                        "section": root_owner,
                        "object_kind": str(root) if root else None,
                        "object_key": None,
                        "field_path": field_path,
                    },
                }
            )
        enriched.append(result)
    return enriched


def relation_identity_from_document(item: dict[str, Any]) -> str:
    return "__".join(
        str(item.get(field, ""))
        for field in ("source_node_key", "relation_type_key", "target_node_key")
    )


def _focus_initialization_projection(
    projection: dict[str, Any], focus_kind: str, focus_key: str
) -> dict[str, Any]:
    domains: list[dict[str, Any]] = []
    for domain in projection.get("domains", []):
        groups = []
        for group in domain.get("groups", []):
            items = []
            for item in group.get("items", []):
                locator = item.get("locator", {})
                context = item.get("context", {})
                matches = (
                    locator.get("object_kind") == focus_kind
                    and locator.get("object_key") == focus_key
                ) or (focus_kind == "resource" and context.get("resource_key") == focus_key)
                if matches:
                    items.append(item)
            if items:
                groups.append({**group, "items": items})
        if groups:
            domains.append({**domain, "groups": groups})

    def belongs_to_focus(finding: dict[str, Any]) -> bool:
        identity = finding.get("identity", "")
        if not isinstance(identity, str):
            return False
        if focus_kind == "node":
            return identity.startswith((f"node:{focus_key}:", f"fact:{focus_key}:"))
        if focus_kind == "actor":
            return identity.startswith(f"actor:{focus_key}:")
        if focus_kind == "relation":
            return identity.startswith(f"relation:{focus_key}:")
        if focus_kind == "resource":
            parts = identity.split(":")
            return len(parts) > 2 and parts[0] == "pool" and parts[2] == focus_key
        return False

    findings = [finding for finding in projection.get("findings", []) if belongs_to_focus(finding)]
    summary = {
        "nodes": sum(
            len(group["items"])
            for domain in domains
            if domain["id"] == "nodes"
            for group in domain["groups"]
        ),
        "actors": sum(
            len(group["items"])
            for domain in domains
            if domain["id"] == "actors"
            for group in domain["groups"]
        ),
        "resource_pools": sum(
            len(group["items"])
            for domain in domains
            if domain["id"] == "resources"
            for group in domain["groups"]
        ),
        "relations": sum(
            len(group["items"])
            for domain in domains
            if domain["id"] == "relations"
            for group in domain["groups"]
        ),
        "derived_states": 0,
        "warnings": sum(finding.get("severity") != "INFO" for finding in findings),
    }
    return {"domains": domains, "findings": findings, "summary": summary}


@router.post(
    "/scenarios/{scenario_id}/draft/sandbox",
    response_model=DraftSandboxResponse,
)
def test_draft_in_sandbox(
    scenario_id: UUID,
    request: DraftSandboxRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DraftSandboxResponse:
    """Strictly validate, then run in a disposable database isolated from formal Games."""

    try:
        result = DraftSandboxService(db, settings).test(
            scenario_id,
            expected_revision=request.expected_revision,
            goal=request.goal,
        )
    except ScenarioLifecycleError as exc:
        _raise_http(exc)
    except GenericProviderError as exc:
        status_code = 504 if exc.code == "MODEL_PROVIDER_TIMEOUT" else 502
        raise AppError(exc.code, exc.message, status_code=status_code) from exc
    issues = [_validation_issue(item) for item in result.issues]
    if not result.started:
        return DraftSandboxResponse(
            scenario_id=result.scenario_id,
            revision=result.revision,
            sandbox_started=False,
            issues=issues,
        )
    state = result.player_state
    assert state is not None
    return DraftSandboxResponse(
        scenario_id=result.scenario_id,
        revision=result.revision,
        sandbox_started=True,
        issues=issues,
        goal_status=result.goal_status,
        task=state.current_task,
        visible_nodes=state.visible_nodes,
        known_facts=state.known_facts,
        resources=state.resources,
        resource_intelligence=state.resource_intelligence,
    )


@router.post("/scenarios/{scenario_id}/draft/publish", response_model=ScenarioPublishResponse)
def publish_draft(
    scenario_id: UUID,
    request: DraftPublishRequest,
    db: Session = Depends(get_db),
) -> ScenarioPublishResponse:
    service = ScenarioService(db)
    try:
        result = service.publish_draft(
            scenario_id,
            expected_revision=request.expected_revision,
            expected_content_hash=request.expected_content_hash,
        )
        db.commit()
        scenario = service.get_scenario(scenario_id)
        return ScenarioPublishResponse(
            scenario=_scenario_summary(db, scenario),
            version=_version_summary(result.version),
        )
    except ScenarioLifecycleError as exc:
        details: dict[str, Any] = {}
        if exc.code == "SCENARIO_DRAFT_INVALID":
            draft = db.get(ScenarioDraft, scenario_id)
            if draft is not None:
                details["issues"] = [item for item in draft.validation_errors]
        db.rollback()
        _raise_http(exc, details=details)


@router.get(
    "/scenarios/{scenario_id}/draft/references",
    response_model=ReferenceIndexResponse,
)
def get_references(scenario_id: UUID, db: Session = Depends(get_db)) -> ReferenceIndexResponse:
    service = ScenarioService(db)
    try:
        draft = service.get_draft(scenario_id)
        return ReferenceIndexResponse(
            scenario_id=scenario_id,
            revision=draft.revision,
            references=[
                ReferenceEdgeResponse(
                    source={
                        "object_kind": edge.source.object_kind,
                        "object_key": edge.source.object_key,
                        "field_path": edge.source.field_path,
                    },
                    target={
                        "object_kind": edge.target.object_kind,
                        "object_key": edge.target.object_key,
                        "field_path": edge.target.field_path,
                    },
                )
                for edge in service.references(scenario_id)
            ],
        )
    except ScenarioLifecycleError as exc:
        _raise_http(exc)


@router.get(
    "/scenarios/{scenario_id}/presentation",
    response_model=PresentationProfileResponse,
)
def get_presentation_profile(
    scenario_id: UUID,
    db: Session = Depends(get_db),
) -> PresentationProfileResponse:
    try:
        profile = PresentationProfileService(db).get_current(scenario_id)
        db.commit()
        return _presentation_profile_response(profile)
    except PresentationProfileLifecycleError as exc:
        db.rollback()
        _raise_presentation_http(exc)


@router.get(
    "/scenarios/{scenario_id}/presentation/revision",
    response_model=PresentationProfileRevisionCheckResponse,
)
def get_presentation_profile_revision(
    scenario_id: UUID,
    db: Session = Depends(get_db),
) -> PresentationProfileRevisionCheckResponse:
    try:
        profile = PresentationProfileService(db).get_current(scenario_id)
        db.commit()
        return PresentationProfileRevisionCheckResponse(
            scenario_id=scenario_id,
            revision=profile.revision,
        )
    except PresentationProfileLifecycleError as exc:
        db.rollback()
        _raise_presentation_http(exc)


@router.put(
    "/scenarios/{scenario_id}/presentation",
    response_model=PresentationProfileResponse,
)
def replace_presentation_profile(
    scenario_id: UUID,
    request: PresentationProfileReplaceRequest,
    db: Session = Depends(get_db),
) -> PresentationProfileResponse:
    try:
        profile = PresentationProfileService(db).replace(
            scenario_id,
            expected_revision=request.expected_revision,
            profile_document=request.profile,
        )
        db.commit()
        return _presentation_profile_response(profile)
    except PresentationProfileLifecycleError as exc:
        db.rollback()
        _raise_presentation_http(exc)


@router.get(
    "/scenarios/{scenario_id}/presentation/revisions",
    response_model=PresentationProfileHistoryResponse,
)
def list_presentation_profile_revisions(
    scenario_id: UUID,
    db: Session = Depends(get_db),
) -> PresentationProfileHistoryResponse:
    try:
        revisions = PresentationProfileService(db).list_revisions(scenario_id)
        return PresentationProfileHistoryResponse(
            scenario_id=scenario_id,
            revisions=[
                {
                    "scenario_id": item.scenario_id,
                    "revision": item.revision,
                    "profile": item.profile_document,
                    "created_at": item.created_at,
                }
                for item in revisions
            ],
        )
    except PresentationProfileLifecycleError as exc:
        _raise_presentation_http(exc)


@router.post(
    "/scenarios/{scenario_id}/presentation/restore",
    response_model=PresentationProfileResponse,
)
def restore_presentation_profile(
    scenario_id: UUID,
    request: PresentationProfileRestoreRequest,
    db: Session = Depends(get_db),
) -> PresentationProfileResponse:
    try:
        profile = PresentationProfileService(db).restore(
            scenario_id,
            expected_revision=request.expected_revision,
            revision=request.revision,
        )
        db.commit()
        return _presentation_profile_response(profile)
    except PresentationProfileLifecycleError as exc:
        db.rollback()
        _raise_presentation_http(exc)


@router.post(
    "/scenarios/{scenario_id}/draft/reference-analysis",
    response_model=DraftReferenceAnalysisResponse,
)
def analyze_working_copy_references(
    scenario_id: UUID,
    request: DraftReferenceAnalysisRequest,
    db: Session = Depends(get_db),
) -> DraftReferenceAnalysisResponse:
    service = ScenarioService(db)
    try:
        references = service.analyze_working_copy_references(
            scenario_id,
            expected_revision=request.expected_revision,
            definition_document=request.definition_document,
        )
        draft = service.get_draft(scenario_id)
        return DraftReferenceAnalysisResponse(
            scenario_id=scenario_id,
            base_revision=draft.revision,
            source="WORKING_COPY",
            references=_reference_edges(references),
        )
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc, details=exc.details)


@router.post(
    "/scenarios/{scenario_id}/draft/completeness",
    response_model=DraftCompletenessResponse,
)
def preview_working_copy_completeness(
    scenario_id: UUID,
    request: DraftCompletenessRequest,
    db: Session = Depends(get_db),
) -> DraftCompletenessResponse:
    service = ScenarioService(db)
    try:
        result = service.completeness_working_copy(
            scenario_id,
            expected_revision=request.expected_revision,
            definition_document=request.definition_document,
        )
        draft = service.get_draft(scenario_id)
        return DraftCompletenessResponse(
            scenario_id=scenario_id,
            base_revision=draft.revision,
            items=[
                CompletenessItemResponse(
                    key=item.key,
                    title=item.title,
                    level=item.level,
                    dependency_kind=item.dependency_kind,
                    message=item.message,
                    path=item.path,
                    locator=item.locator,
                    action=item.action,
                    reference_locator=item.reference_locator,
                    reference_owner=item.reference_owner,
                )
                for item in result.items
            ],
            validation_issues=[
                _validation_issue(issue, request.definition_document)
                for issue in result.validation_issues
            ],
            required_missing=result.required_missing,
            recommended_missing=result.recommended_missing,
            validation_issue_count=result.validation_issue_count,
            reference_edge_count=result.reference_edge_count,
        )
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc, details=exc.details)


@router.post(
    "/scenarios/{scenario_id}/draft/transform",
    response_model=DraftTransformResponse,
)
def transform_working_copy(
    scenario_id: UUID,
    request: DraftTransformRequest,
    db: Session = Depends(get_db),
) -> DraftTransformResponse:
    service = ScenarioService(db)
    try:
        operation = request.operation
        changed = service.transform_working_copy(
            scenario_id,
            expected_revision=request.expected_revision,
            definition_document=request.definition_document,
            operation_kind=operation.kind,
            object_kind=operation.object_kind,
            old_key=operation.old_key,
            new_key=operation.new_key,
            object_key=operation.object_key,
            node_key=operation.node_key,
            fact_key=operation.fact_key,
            collection=operation.collection,
            identity=operation.identity,
            parent_kind=operation.parent_kind,
            parent_key=operation.parent_key,
            nested_key=operation.nested_key,
        )
        draft = service.get_draft(scenario_id)
        return DraftTransformResponse(
            scenario_id=scenario_id,
            base_revision=draft.revision,
            source="WORKING_COPY",
            definition_document=changed,
            references=_reference_edges(reference_index(changed)),
        )
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc, details=exc.details)


@router.post("/scenarios/{scenario_id}/draft/rename-key", response_model=DraftResponse)
def rename_draft_key(
    scenario_id: UUID,
    request: DraftRenameKeyRequest,
    db: Session = Depends(get_db),
) -> DraftResponse:
    return _draft_write(
        db,
        lambda service: service.rename_draft_key(
            scenario_id,
            expected_revision=request.expected_revision,
            object_kind=request.object_kind,
            old_key=request.old_key,
            new_key=request.new_key,
        ),
    )


@router.post("/scenarios/{scenario_id}/draft/delete-object", response_model=DraftResponse)
def delete_draft_object(
    scenario_id: UUID,
    request: DraftDeleteObjectRequest,
    db: Session = Depends(get_db),
) -> DraftResponse:
    return _draft_write(
        db,
        lambda service: service.delete_draft_object(
            scenario_id,
            expected_revision=request.expected_revision,
            object_kind=request.object_kind,
            object_key=request.object_key,
        ),
    )


@router.get(
    "/scenarios/{scenario_id}/versions",
    response_model=list[ScenarioVersionSummaryResponse],
)
def list_versions(
    scenario_id: UUID,
    db: Session = Depends(get_db),
) -> list[ScenarioVersionSummaryResponse]:
    try:
        return [_version_summary(item) for item in ScenarioService(db).list_versions(scenario_id)]
    except ScenarioLifecycleError as exc:
        _raise_http(exc)


@router.get(
    "/scenarios/{scenario_id}/versions/{version_id}",
    response_model=ScenarioVersionDetailResponse,
)
def get_version(
    scenario_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> ScenarioVersionDetailResponse:
    try:
        version = ScenarioService(db).get_version(scenario_id, version_id)
        return ScenarioVersionDetailResponse(
            **_version_summary(version).model_dump(),
            definition_document=version.snapshot_document,
        )
    except ScenarioLifecycleError as exc:
        _raise_http(exc)


@router.get(
    "/scenarios/{scenario_id}/deletion-impact",
    response_model=ScenarioDeletionImpactResponse,
)
def get_scenario_deletion_impact(
    scenario_id: UUID,
    db: Session = Depends(get_db),
) -> ScenarioDeletionImpactResponse:
    try:
        impact = ScenarioService(db).deletion_impact(scenario_id)
        return ScenarioDeletionImpactResponse(
            scenario_id=scenario_id,
            scenario_name=impact.scenario.name,
            scenario_key=impact.scenario.key,
            draft_revision=impact.draft_revision,
            published_version_count=impact.published_version_count,
            dependent_games=[
                ScenarioDependentGameResponse(
                    game_id=game.game_id,
                    identifier=game.identifier,
                    status=game.status,
                    scenario_version_id=game.scenario_version_id,
                    scenario_version_number=game.scenario_version_number,
                    created_at=game.created_at,
                    updated_at=game.updated_at,
                )
                for game in impact.dependent_games
            ],
            can_delete=impact.can_delete,
        )
    except ScenarioLifecycleError as exc:
        _raise_http(exc)


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(scenario_id: UUID, db: Session = Depends(get_db)) -> None:
    try:
        ScenarioService(db).delete_scenario(scenario_id)
        db.commit()
    except ScenarioLifecycleError as exc:
        db.rollback()
        if exc.code == "SCENARIO_HAS_GAME_DEPENDENCIES":
            raise AppError(
                exc.code,
                exc.message,
                status_code=status.HTTP_409_CONFLICT,
                details=exc.details,
            ) from exc
        _raise_http(exc, details=exc.details)


@router.post(
    "/scenarios/{scenario_id}/versions/{version_id}/restore-preview",
    response_model=RestorePreviewResponse,
)
def preview_restore_version(
    scenario_id: UUID,
    version_id: UUID,
    request: RestorePreviewRequest,
    db: Session = Depends(get_db),
) -> RestorePreviewResponse:
    try:
        preview = ScenarioService(db).preview_restore_version(
            scenario_id,
            version_id=version_id,
            expected_revision=request.expected_persisted_revision,
        )
        return RestorePreviewResponse(
            scenario_id=scenario_id,
            version=_version_summary(preview.version),
            current_draft_revision=preview.current_draft_revision,
            candidate_working_document=preview.candidate_working_document,
            semantic_diff=SemanticDiffResponse.model_validate(preview.semantic_diff),
            unchanged=preview.unchanged,
            restore_supported=True,
        )
    except ScenarioLifecycleError as exc:
        if exc.code.endswith("_NOT_FOUND"):
            code_status = status.HTTP_404_NOT_FOUND
        elif exc.code in {"SCENARIO_RESTORE_UNSUPPORTED"}:
            code_status = status.HTTP_422_UNPROCESSABLE_ENTITY
        else:
            code_status = status.HTTP_409_CONFLICT
        raise AppError(exc.code, exc.message, status_code=code_status, details=exc.details) from exc


@router.get("/scenario-examples", response_model=list[ScenarioExampleResponse])
def list_examples() -> list[ScenarioExampleResponse]:
    return [
        ScenarioExampleResponse(
            key=key,
            name=definition.metadata.name,
            description=definition.metadata.description,
            maturity=maturity,
        )
        for key, (definition, maturity) in _EXAMPLES.items()
    ]


@router.get("/scenario-definition-schema", response_model=dict[str, Any])
def get_scenario_definition_schema() -> dict[str, Any]:
    """Expose the closed current authoring vocabulary, never executable behavior."""

    from app.domain.scenario_v3 import ScenarioDefinitionV3

    return ScenarioDefinitionV3.model_json_schema(mode="validation")


def _draft_write(
    db: Session,
    operation: Callable[[ScenarioService], ScenarioDraft],
) -> DraftResponse:
    try:
        draft = operation(ScenarioService(db))
        db.commit()
        return _draft_response(draft)
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc, details=exc.details)


def _reference_edges(edges: tuple[ReferenceEdge, ...]) -> list[ReferenceEdgeResponse]:
    return [
        ReferenceEdgeResponse(
            source={
                "object_kind": edge.source.object_kind,
                "object_key": edge.source.object_key,
                "field_path": edge.source.field_path,
            },
            target={
                "object_kind": edge.target.object_kind,
                "object_key": edge.target.object_key,
                "field_path": edge.target.field_path,
            },
        )
        for edge in edges
    ]


def _scenario_summary(db: Session, scenario: Scenario) -> ScenarioSummaryResponse:
    draft = db.get(ScenarioDraft, scenario.id)
    assert draft is not None
    current = (
        db.get(ScenarioVersion, scenario.current_published_version_id)
        if scenario.current_published_version_id is not None
        else None
    )
    return ScenarioSummaryResponse(
        id=scenario.id,
        key=scenario.key,
        name=scenario.name,
        status=ScenarioStatus(scenario.status),
        draft_revision=draft.revision,
        current_published_version_id=scenario.current_published_version_id,
        current_published_version_number=current.version_number if current is not None else None,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


def _scenario_detail(db: Session, scenario: Scenario) -> ScenarioDetailResponse:
    count = db.scalar(
        select(func.count())
        .select_from(ScenarioVersion)
        .where(ScenarioVersion.scenario_id == scenario.id)
    )
    return ScenarioDetailResponse(
        **_scenario_summary(db, scenario).model_dump(),
        version_count=count or 0,
    )


def _draft_response(draft: ScenarioDraft) -> DraftResponse:
    try:
        definition_document = normalize_resource_source_hint_document(draft.definition_document)
    except ValueError as exc:
        raise ScenarioLifecycleError(
            "SCENARIO_RESOURCE_SOURCE_HINT_NORMALIZATION_FAILED",
            str(exc),
        ) from exc
    if not isinstance(definition_document, dict):
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_DOCUMENT_INVALID",
            "The Scenario Draft document must be an object",
        )
    return DraftResponse(
        scenario_id=draft.scenario_id,
        revision=draft.revision,
        definition_document=definition_document,
        validation_status=draft.validation_status,
        validation_issues=[
            ValidationIssueResponse(
                severity=ValidationSeverity(item.get("severity", "ERROR")),
                code=item["code"],
                path=item["path"],
                message=item["message"],
                locator=item.get("locator"),
            )
            for item in draft.validation_errors
        ],
        content_hash=draft.content_hash,
        base_scenario_version_id=draft.base_scenario_version_id,
        updated_at=draft.updated_at,
    )


def _version_summary(version: ScenarioVersion) -> ScenarioVersionSummaryResponse:
    return ScenarioVersionSummaryResponse(
        id=version.id,
        scenario_id=version.scenario_id,
        version_number=version.version_number,
        schema_version=version.schema_version,
        content_hash=version.content_hash,
        published_at=version.published_at,
    )


def _presentation_profile_response(profile: Any) -> PresentationProfileResponse:
    return PresentationProfileResponse(
        scenario_id=profile.scenario_id,
        revision=profile.revision,
        profile=profile.profile_document,
        updated_at=profile.updated_at,
    )


def _validation_issue(
    issue: ScenarioValidationIssue,
    document: dict[str, Any] | None = None,
) -> ValidationIssueResponse:
    resolved = locator_for_path(document, issue.path) if document is not None else None
    locator = (
        {
            "object_kind": resolved.object_kind,
            "object_key": resolved.object_key,
            "field_path": resolved.field_path,
        }
        if resolved is not None
        else None
    )
    return ValidationIssueResponse(
        severity=ValidationSeverity(issue.severity),
        code=issue.code,
        path=issue.path,
        message=issue.message,
        locator=locator,
        type=issue.type,
    )


def _raise_http(exc: ScenarioLifecycleError, *, details: dict[str, Any] | None = None) -> Never:
    not_found = exc.code.endswith("_NOT_FOUND")
    raise AppError(
        exc.code,
        exc.message,
        status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_409_CONFLICT,
        details=details,
    ) from exc


def _raise_presentation_http(exc: PresentationProfileLifecycleError) -> Never:
    if exc.code.endswith("_NOT_FOUND"):
        code_status = status.HTTP_404_NOT_FOUND
    elif exc.code.endswith("_INVALID"):
        code_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif exc.code.endswith("_ARCHIVED") or exc.code.endswith("_CONFLICT"):
        code_status = status.HTTP_409_CONFLICT
    else:
        code_status = status.HTTP_400_BAD_REQUEST
    raise AppError(
        exc.code,
        exc.message,
        status_code=code_status,
        details=exc.details,
    ) from exc


__all__ = ["router"]
