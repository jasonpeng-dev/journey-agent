"""Stdlib CLI adapter for Scenario portability operations.

The CLI is intentionally thin: it reads/writes local files and delegates all
artifact semantics and lifecycle mutations to ``ScenarioPortabilityService``.
It never calls the HTTP application or creates a server-side export file on
behalf of an HTTP request.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.scenarios.portability import (
    MAX_ARTIFACT_BYTES,
    ScenarioArtifactCandidate,
    ScenarioPortabilityError,
    parse_scenario_artifact,
    safe_artifact_filename,
    serialize_scenario_artifact,
)
from app.scenarios.validation import ScenarioDefinitionValidator
from app.services.scenario_portability import ScenarioPortabilityService
from app.services.scenarios import ScenarioLifecycleError

EXIT_SUCCESS = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_ARTIFACT = 3
EXIT_CONFLICT = 4
EXIT_READINESS = 5


class CliError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="journey",
        description="Journey Agent Scenario portability tools",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    scenario = commands.add_parser("scenario", help="Scenario portability operations")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)

    import_parser = scenario_commands.add_parser("import", help="Import a Scenario artifact")
    import_parser.add_argument("file", type=Path)
    import_parser.add_argument("--target-key", default=None)

    validate_parser = scenario_commands.add_parser(
        "validate-file", help="Validate a Scenario artifact without database writes"
    )
    validate_parser.add_argument("file", type=Path)

    export_parser = scenario_commands.add_parser("export", help="Export a Draft or exact Release")
    export_parser.add_argument("scenario_key")
    export_kind = export_parser.add_mutually_exclusive_group(required=True)
    export_kind.add_argument("--draft", action="store_true", help="Export the persisted Draft")
    export_kind.add_argument(
        "--version",
        help="Export an exact Published Version number or UUID",
    )
    export_parser.add_argument("--output", type=Path, default=None)
    export_parser.add_argument("--force", action="store_true", help="Allow overwriting one file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command != "scenario":
            parser.error("a scenario command is required")
        if args.scenario_command == "validate-file":
            return _validate_file(args.file)
        if args.scenario_command == "import":
            return _import_file(args.file, target_key=args.target_key)
        if args.scenario_command == "export":
            return _export_scenario(args)
        parser.error("an unsupported scenario command was selected")
    except ScenarioPortabilityError as exc:
        return _print_error(
            exc.code,
            exc.message,
            details=exc.details,
            exit_code=_exit_code_for_portability(exc.code),
        )
    except ScenarioLifecycleError as exc:
        return _print_error(
            exc.code,
            exc.message,
            details=exc.details,
            exit_code=EXIT_CONFLICT if not exc.code.endswith("_NOT_FOUND") else EXIT_ARTIFACT,
        )
    except CliError as exc:
        return _print_error(exc.code, exc.message, details=exc.details, exit_code=EXIT_ARTIFACT)
    except OSError as exc:
        return _print_error(
            "CLI_IO_ERROR",
            "The requested artifact file could not be read or written",
            details={"reason": str(exc)},
            exit_code=EXIT_INTERNAL,
        )
    except Exception:
        return _print_error(
            "INTERNAL_ERROR",
            "The portability command could not complete",
            details={},
            exit_code=EXIT_INTERNAL,
        )


def _validate_file(path: Path) -> int:
    raw = _read_artifact_file(path)
    candidate = parse_scenario_artifact(raw)
    validation = ScenarioDefinitionValidator().validate(candidate.canonical_document)
    payload = {
        "status": "VALID",
        "artifact": _artifact_summary(candidate),
        "validation": _validation_payload(validation),
        "release_ready": candidate.content_type == "release" and validation.passed,
    }
    print(_json_text(payload))
    if candidate.content_type == "release" and not validation.passed:
        return EXIT_READINESS
    return EXIT_SUCCESS


def _import_file(path: Path, *, target_key: str | None) -> int:
    raw = _read_artifact_file(path)
    from app.infrastructure.db.session import SessionLocal

    with SessionLocal() as db:
        result = ScenarioPortabilityService(db).import_as_new(
            raw,
            target_scenario_key=target_key,
        )
        published = result.published_version
        payload = {
            "status": "IMPORTED",
            "artifact": {
                "artifact_type": result.artifact.artifact_type,
                "artifact_version": result.artifact.artifact_version,
                "content_type": result.artifact.content_type,
                "schema_version": result.artifact.schema_version,
                "content_hash": result.content_hash,
            },
            "scenario": {
                "id": str(result.scenario.id),
                "key": result.scenario.key,
                "name": result.scenario.name,
                "status": result.scenario.status,
            },
            "draft": {
                "revision": result.draft.revision,
                "validation_status": result.draft.validation_status,
            },
            "published_version": (
                {
                    "id": str(published.id),
                    "version_number": published.version_number,
                    "schema_version": published.schema_version,
                    "content_hash": published.content_hash,
                }
                if published is not None
                else None
            ),
            "game_created": False,
        }
    print(_json_text(payload))
    return EXIT_SUCCESS


def _export_scenario(args: argparse.Namespace) -> int:
    from app.infrastructure.db.models import Scenario
    from app.infrastructure.db.session import SessionLocal

    with SessionLocal() as db:
        scenario = db.scalar(select(Scenario).where(Scenario.key == args.scenario_key))
        if scenario is None:
            raise ScenarioPortabilityError(
                "SCENARIO_NOT_FOUND",
                "The requested Scenario does not exist",
                details={"key": args.scenario_key},
                status_code=404,
            )
        service = ScenarioPortabilityService(db)
        if args.draft:
            artifact = service.export_draft(scenario.id)
            filename = safe_artifact_filename(artifact.scenario.key, content_type="draft")
        else:
            version_id, version_number = _resolve_version(db, scenario.id, args.version)
            artifact = service.export_release(scenario.id, version_id)
            filename = safe_artifact_filename(
                artifact.scenario.key,
                content_type="release",
                version_number=version_number,
            )
        serialized = serialize_scenario_artifact(artifact)

    destination = resolve_export_path(args.output, filename)
    write_artifact_file(destination, serialized.encode("utf-8"), force=args.force)
    print(
        _json_text(
            {
                "status": "EXPORTED",
                "path": str(destination),
                "filename": filename,
                "artifact": _artifact_summary_from_artifact(artifact),
            }
        )
    )
    return EXIT_SUCCESS


def resolve_export_path(output: Path | None, filename: str) -> Path:
    """Resolve an explicit file or directory without using artifact paths."""

    if output is None:
        destination = Path("scenarios") / "exports" / filename
    else:
        expanded = output.expanduser()
        if (expanded.exists() and expanded.is_dir()) or str(output).endswith(("/", "\\")):
            destination = expanded / filename
        else:
            destination = expanded
    destination = destination.absolute()
    _reject_symlink_path(destination)
    return destination


def write_artifact_file(path: Path, payload: bytes, *, force: bool) -> None:
    """Write one deterministic artifact, refusing silent overwrite or symlinks."""

    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ScenarioPortabilityError(
            "ARTIFACT_INPUT_LIMIT",
            "The serialized artifact exceeds the portability byte limit",
        )
    _reject_symlink_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise CliError("CLI_OUTPUT_INVALID", "The export output path is a directory")
    mode = "wb" if force else "xb"
    try:
        with path.open(mode) as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise CliError(
            "CLI_OUTPUT_EXISTS",
            "The export output already exists; pass --force to overwrite it",
            details={"path": str(path)},
        ) from exc


def _read_artifact_file(path: Path) -> bytes:
    _reject_symlink_path(path)
    if path.is_symlink() or not path.is_file():
        raise CliError("CLI_INPUT_NOT_FOUND", "The artifact input file does not exist")
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ScenarioPortabilityError(
            "ARTIFACT_INPUT_LIMIT",
            "The Scenario artifact exceeds the portability input byte limit",
        )
    return path.read_bytes()


def _resolve_version(db: Any, scenario_id: UUID, value: str) -> tuple[UUID, int]:
    from app.infrastructure.db.models import ScenarioVersion

    try:
        version_id = UUID(value)
    except ValueError:
        try:
            version_number = int(value)
        except ValueError as exc:
            raise CliError(
                "CLI_VERSION_INVALID",
                "--version must be a positive version number or UUID",
            ) from exc
        if version_number < 1:
            raise CliError("CLI_VERSION_INVALID", "--version must be positive") from None
        version = db.scalar(
            select(ScenarioVersion).where(
                ScenarioVersion.scenario_id == scenario_id,
                ScenarioVersion.version_number == version_number,
            )
        )
    else:
        version = db.get(ScenarioVersion, version_id)
        if version is not None and version.scenario_id != scenario_id:
            version = None
    if version is None:
        raise ScenarioPortabilityError(
            "SCENARIO_VERSION_NOT_FOUND",
            "The requested Published Version does not exist for this Scenario",
            status_code=404,
        )
    return version.id, version.version_number


def _reject_symlink_path(path: Path) -> None:
    current = path.absolute()
    while True:
        if current.exists() and current.is_symlink():
            raise CliError(
                "CLI_SYMLINK_PATH_REJECTED",
                "Artifact input/output paths may not traverse symlinks",
                details={"path": str(path)},
            )
        if current.parent == current:
            return
        current = current.parent


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


def _artifact_summary_from_artifact(artifact: Any) -> dict[str, Any]:
    return {
        "artifact_type": artifact.artifact_type,
        "artifact_version": artifact.artifact_version,
        "content_type": artifact.content_type,
        "schema_version": artifact.schema_version,
        "scenario": artifact.scenario.model_dump(mode="json"),
        "content_hash": artifact.content_hash,
    }


def _validation_payload(result: Any) -> dict[str, Any]:
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


def _json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _print_error(code: str, message: str, *, details: dict[str, Any], exit_code: int) -> int:
    print(
        _json_text({"error": {"code": code, "message": message, "details": details}}),
        file=sys.stderr,
    )
    return exit_code


def _exit_code_for_portability(code: str) -> int:
    if code in {"SCENARIO_KEY_CONFLICT", "STALE_TARGET_REVISION", "SCENARIO_ARCHIVED"}:
        return EXIT_CONFLICT
    if code == "RELEASE_NOT_PUBLISHABLE":
        return EXIT_READINESS
    if code == "INTERNAL_IMPORT_FAILURE":
        return EXIT_INTERNAL
    return EXIT_ARTIFACT


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_ARTIFACT",
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_READINESS",
    "EXIT_SUCCESS",
    "EXIT_USAGE",
    "build_parser",
    "main",
    "resolve_export_path",
    "write_artifact_file",
]
