"""HTTP adapter for the authored Scenario lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas.phase_d import (
    DraftDeleteObjectRequest,
    DraftPublishRequest,
    DraftRenameKeyRequest,
    DraftReplaceRequest,
    DraftResponse,
    DraftRestoreRequest,
    DraftRevisionRequest,
    DraftValidationResponse,
    ReadinessCheckResponse,
    ReadinessLevel,
    ReferenceEdgeResponse,
    ReferenceIndexResponse,
    ScenarioCreateMode,
    ScenarioCreateRequest,
    ScenarioDetailResponse,
    ScenarioExampleResponse,
    ScenarioPublishResponse,
    ScenarioStatus,
    ScenarioSummaryResponse,
    ScenarioVersionDetailResponse,
    ScenarioVersionSummaryResponse,
    ValidationIssueResponse,
    ValidationSeverity,
)
from app.core.errors import AppError
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.infrastructure.db.session import get_db
from app.scenarios.builtin import MEDICAL_EMERGENCY_V2, STARFIRE_V2
from app.scenarios.validation import ScenarioValidationIssue
from app.services.scenarios import ScenarioLifecycleError, ScenarioService

router = APIRouter(prefix="/api/v1", tags=["scenarios"])

_EXAMPLES = {
    "medical_emergency": (
        MEDICAL_EMERGENCY_V2,
        ReadinessLevel.MINIMUM_PLAYABLE,
    ),
    "starfire_command": (
        STARFIRE_V2,
        ReadinessLevel.MINIMUM_PLAYABLE,
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
        issues = [_validation_issue(item) for item in result.issues]
        passed = result.passed
        return DraftValidationResponse(
            scenario_id=scenario_id,
            revision=draft.revision,
            content_hash=draft.content_hash,
            issues=issues,
            readiness=[
                ReadinessCheckResponse(
                    level=ReadinessLevel.STRUCTURALLY_VALID,
                    passed=passed,
                    issue_codes=[item.code for item in result.issues],
                )
            ],
            publish_ready=passed,
        )
    except ScenarioLifecycleError as exc:
        db.rollback()
        _raise_http(exc)


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
                details["issues"] = [
                    {"severity": "ERROR", **item} for item in draft.validation_errors
                ]
        db.rollback()
        _raise_http(exc, details=details)


@router.post("/scenarios/{scenario_id}/draft/restore", response_model=DraftResponse)
def restore_draft(
    scenario_id: UUID,
    request: DraftRestoreRequest,
    db: Session = Depends(get_db),
) -> DraftResponse:
    return _draft_write(
        db,
        lambda service: service.restore_version(
            scenario_id,
            version_id=request.version_id,
            expected_revision=request.expected_revision,
        ),
    )


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
    """Expose the closed v2 authoring vocabulary, never executable behavior."""

    return ScenarioDefinitionV2.model_json_schema(mode="validation")


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
        _raise_http(exc)


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
    return DraftResponse(
        scenario_id=draft.scenario_id,
        revision=draft.revision,
        definition_document=draft.definition_document,
        validation_status=draft.validation_status,
        validation_issues=[
            ValidationIssueResponse(severity=ValidationSeverity.ERROR, **item)
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
        schema_version=2,
        content_hash=version.content_hash,
        published_at=version.published_at,
    )


def _validation_issue(issue: ScenarioValidationIssue) -> ValidationIssueResponse:
    return ValidationIssueResponse(
        severity=ValidationSeverity.ERROR,
        code=issue.code,
        path=issue.path,
        message=issue.message,
    )


def _raise_http(exc: ScenarioLifecycleError, *, details: dict[str, Any] | None = None) -> Never:
    not_found = exc.code.endswith("_NOT_FOUND")
    raise AppError(
        exc.code,
        exc.message,
        status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_409_CONFLICT,
        details=details,
    ) from exc


__all__ = ["router"]
