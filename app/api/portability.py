"""HTTP adapters for the Scenario portability service.

The router deliberately contains no artifact parsing, validation, diff,
conflict, publication or transaction logic.  It translates request/response
shape and delegates those concerns to ``ScenarioPortabilityService``.
"""

from __future__ import annotations

from typing import Any, Never
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.schemas.portability import ScenarioArtifactRequest
from app.core.errors import AppError
from app.infrastructure.db.models import Scenario, ScenarioVersion
from app.infrastructure.db.session import get_db
from app.scenarios.portability import (
    ScenarioArtifactCandidate,
    ScenarioPortabilityError,
    safe_artifact_filename,
    serialize_scenario_artifact,
)
from app.services.scenario_portability import NewScenarioArtifactPreview, ScenarioPortabilityService
from app.services.scenarios import ScenarioLifecycleError

router = APIRouter(prefix="/api/v1/scenarios", tags=["scenario-portability"])


@router.post("/artifacts/preview", response_model=dict[str, Any])
def preview_new_artifact(
    request: ScenarioArtifactRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        preview = ScenarioPortabilityService(db).preview_new_import(
            request.artifact,
            target_scenario_key=request.target_scenario_key,
        )
        return _new_preview_response(preview)
    except ScenarioPortabilityError as exc:
        _raise_portability_http(exc)


@router.post(
    "/artifacts/import",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
def import_new_artifact(
    request: ScenarioArtifactRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = ScenarioPortabilityService(db).import_as_new(
            request.artifact,
            target_scenario_key=request.target_scenario_key,
        )
        return _import_response(result)
    except ScenarioPortabilityError as exc:
        _raise_portability_http(exc)


@router.get("/{scenario_id}/draft/artifact")
def export_draft_artifact(scenario_id: UUID, db: Session = Depends(get_db)) -> Response:
    try:
        artifact = ScenarioPortabilityService(db).export_draft(scenario_id)
        filename = safe_artifact_filename(artifact.scenario.key, content_type="draft")
        return _download_response(serialize_scenario_artifact(artifact), filename)
    except ScenarioPortabilityError as exc:
        _raise_portability_http(exc)
    except ScenarioLifecycleError as exc:
        _raise_lifecycle_http(exc)


@router.get("/{scenario_id}/latest/artifact")
def export_latest_release_artifact(scenario_id: UUID, db: Session = Depends(get_db)) -> Response:
    try:
        service = ScenarioPortabilityService(db)
        artifact = service.export_latest_release(scenario_id)
        scenario = db.get(Scenario, scenario_id)
        version_id = scenario.current_published_version_id if scenario is not None else None
        version = db.get(ScenarioVersion, version_id) if version_id is not None else None
        if version is None:
            raise ScenarioPortabilityError(
                "NO_PUBLISHED_VERSION",
                "The Scenario has no published version to export",
                status_code=status.HTTP_409_CONFLICT,
            )
        filename = safe_artifact_filename(
            artifact.scenario.key,
            content_type="release",
            version_number=version.version_number,
        )
        return _download_response(serialize_scenario_artifact(artifact), filename)
    except ScenarioPortabilityError as exc:
        _raise_portability_http(exc)
    except ScenarioLifecycleError as exc:
        _raise_lifecycle_http(exc)


@router.get("/{scenario_id}/versions/{version_id}/artifact")
def export_release_artifact(
    scenario_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    try:
        service = ScenarioPortabilityService(db)
        artifact = service.export_release(scenario_id, version_id)
        version = db.get(ScenarioVersion, version_id)
        if version is None or version.scenario_id != scenario_id:
            raise ScenarioPortabilityError(
                "SCENARIO_VERSION_NOT_FOUND",
                "The explicitly requested ScenarioVersion does not belong to this Scenario",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        filename = safe_artifact_filename(
            artifact.scenario.key,
            content_type="release",
            version_number=version.version_number,
        )
        return _download_response(serialize_scenario_artifact(artifact), filename)
    except ScenarioPortabilityError as exc:
        _raise_portability_http(exc)
    except ScenarioLifecycleError as exc:
        _raise_lifecycle_http(exc)


def _artifact_summary(candidate: ScenarioArtifactCandidate) -> dict[str, Any]:
    artifact = candidate.artifact
    return {
        "artifact_type": artifact.artifact_type,
        "artifact_version": artifact.artifact_version,
        "content_type": artifact.content_type,
        "schema_version": artifact.schema_version,
        "scenario": artifact.scenario.model_dump(mode="json"),
        "content_hash": candidate.content_hash,
    }


def _validation_summary(result: Any) -> dict[str, Any]:
    issues = [
        {
            "code": issue.code,
            "path": issue.path,
            "message": issue.message,
            "severity": issue.severity,
            "type": issue.type,
        }
        for issue in result.issues
    ]
    return {
        "structurally_valid": result.definition is not None,
        "publish_ready": result.passed,
        "issue_count": len(issues),
        "issues": issues,
    }


def _new_preview_response(preview: NewScenarioArtifactPreview) -> dict[str, Any]:
    return {
        "artifact": _artifact_summary(preview.candidate),
        "candidate_target_key": preview.target_scenario_key,
        "key_conflict": preview.key_conflict,
        "validation": _validation_summary(preview.validation),
        "content_hash": preview.content_hash,
        "what_import_will_create": {
            "records": list(preview.records_to_create),
            "published_version_number": 1 if preview.candidate.content_type == "release" else None,
            "game_created": False,
        },
    }


def _import_response(result: Any) -> dict[str, Any]:
    scenario = result.scenario
    draft = result.draft
    published = result.published_version
    published_payload = None
    if published is not None:
        published_payload = {
            "id": published.id,
            "version_number": published.version_number,
            "schema_version": published.schema_version,
            "content_hash": published.content_hash,
            "published_at": published.published_at,
        }
    return {
        "status": "IMPORTED",
        "artifact": {
            "artifact_type": result.artifact.artifact_type,
            "artifact_version": result.artifact.artifact_version,
            "content_type": result.artifact.content_type,
            "schema_version": result.artifact.schema_version,
            "content_hash": result.content_hash,
        },
        "scenario": {
            "id": scenario.id,
            "key": scenario.key,
            "name": scenario.name,
            "status": scenario.status,
        },
        "draft": {
            "revision": draft.revision,
            "validation_status": draft.validation_status,
        },
        "published_version": published_payload,
        "game_created": False,
    }


def _download_response(serialized: str, filename: str) -> Response:
    encoded_filename = quote(filename, safe="")
    return Response(
        content=serialized.encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_filename}"
            ),
            "Cache-Control": "no-store",
        },
    )


def _raise_portability_http(exc: ScenarioPortabilityError) -> Never:
    invalid_codes = {
        "INVALID_ARTIFACT",
        "UNSUPPORTED_ARTIFACT_TYPE",
        "UNSUPPORTED_ARTIFACT_VERSION",
        "UNSUPPORTED_SCENARIO_SCHEMA_VERSION",
        "INVALID_CONTENT_TYPE",
        "ARTIFACT_INTEGRITY_MISMATCH",
        "ARTIFACT_METADATA_MISMATCH",
        "ARTIFACT_INPUT_LIMIT",
        "INVALID_TARGET_SCENARIO_KEY",
        "RELEASE_NOT_PUBLISHABLE",
        "SCENARIO_VERSION_SCHEMA_UNSUPPORTED",
    }
    if exc.code in invalid_codes:
        code_status = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif exc.code in {
        "SCENARIO_KEY_CONFLICT",
        "STALE_TARGET_REVISION",
        "SCENARIO_ARCHIVED",
        "NO_PUBLISHED_VERSION",
    }:
        code_status = status.HTTP_409_CONFLICT
    elif exc.code.endswith("_NOT_FOUND"):
        code_status = status.HTTP_404_NOT_FOUND
    elif exc.code == "INTERNAL_IMPORT_FAILURE":
        code_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        code_status = exc.status_code
    raise AppError(
        exc.code,
        exc.message,
        status_code=code_status,
        details=exc.details,
        retryable=exc.retryable,
    ) from exc


def _raise_lifecycle_http(exc: ScenarioLifecycleError) -> Never:
    raise AppError(
        exc.code,
        exc.message,
        status_code=status.HTTP_404_NOT_FOUND
        if exc.code.endswith("_NOT_FOUND")
        else status.HTTP_409_CONFLICT,
        details=exc.details,
    ) from exc


__all__ = ["router"]
