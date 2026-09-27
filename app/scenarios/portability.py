"""Portable Scenario artifact envelope and canonical codec.

This module is deliberately independent from HTTP, CLI and browser concerns.
It is the single boundary for the first portable ``*.scenario.json`` format.
Only the current authored v3 contract is accepted as a portable definition;
the v2 parser remains a historical/runtime compatibility lane elsewhere.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.core.errors import AppError
from app.domain.scenario_v2 import StableKey
from app.domain.scenario_v3 import ScenarioDefinitionV3
from app.scenarios.documents import SCENARIO_DOCUMENT_SCHEMA_VERSION
from app.scenarios.serialization import canonical_document_payload, scenario_content_hash

PORTABLE_ARTIFACT_TYPE = "journey_scenario"
PORTABLE_ARTIFACT_VERSION = 1
PORTABLE_CONTENT_TYPES = frozenset({"draft", "release"})

# The final-local v8 authored snapshot is approximately 303 KiB pretty-printed,
# with depth 7 and 9,082 aggregate container entries.  These limits leave a
# substantial growth margin without accepting unbounded JSON from future Web/CLI
# adapters.  All adapters must reuse this contract.
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_DEPTH = 64
MAX_ARTIFACT_CONTAINER_ENTRIES = 100_000
MAX_ARTIFACT_STRING_LENGTH = 16_384


class ScenarioPortabilityError(AppError):
    """Typed, non-gameplay error raised at the portability boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            code,
            message,
            status_code=status_code,
            details=details,
            retryable=retryable,
        )


class ScenarioArtifactMetadata(BaseModel):
    """Portable Scenario metadata, separate from database lifecycle metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


class ScenarioArtifact(BaseModel):
    """The v1 portable artifact envelope.

    ``definition`` intentionally remains a JSON mapping here.  The codec then
    parses it as ``ScenarioDefinitionV3`` so envelope validation and authored
    definition validation have separate, typed error paths.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: Literal["journey_scenario"]
    artifact_version: Literal[1]
    content_type: Literal["draft", "release"]
    schema_version: Literal[3]
    scenario: ScenarioArtifactMetadata
    content_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    definition: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ScenarioArtifactCandidate:
    """Validated, canonical authored v3 document ready for a service operation."""

    artifact: ScenarioArtifact
    definition: ScenarioDefinitionV3
    canonical_document: dict[str, Any]
    content_hash: str

    @property
    def content_type(self) -> Literal["draft", "release"]:
        return self.artifact.content_type

    @property
    def scenario_key(self) -> str:
        return self.definition.metadata.key


class ScenarioArtifactCodec:
    """Strict parser and deterministic UTF-8 serializer for portable artifacts."""

    @staticmethod
    def parse(raw: bytes | str | Mapping[str, Any]) -> ScenarioArtifactCandidate:
        document = _decode_json_object(raw)
        _validate_envelope_discriminators(document)
        try:
            artifact = ScenarioArtifact.model_validate(document)
        except ValidationError as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The Scenario artifact envelope is invalid",
                details={"errors": _validation_details(exc)},
            ) from exc

        try:
            authored = ScenarioDefinitionV3.model_validate(deepcopy(artifact.definition))
            canonical = canonical_document_payload(authored.model_dump(mode="json"))
            canonical_authored = ScenarioDefinitionV3.model_validate(canonical)
        except ValidationError as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The Scenario artifact definition is not a valid current v3 document",
                details={"errors": _validation_details(exc)},
            ) from exc
        except ValueError as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The Scenario artifact definition is not a valid current v3 document",
                details={"reason": str(exc)},
            ) from exc

        _validate_metadata_consistency(artifact.scenario, canonical_authored)
        computed_hash = scenario_content_hash(canonical)
        if artifact.content_hash is not None and artifact.content_hash != computed_hash:
            raise ScenarioPortabilityError(
                "ARTIFACT_INTEGRITY_MISMATCH",
                "The Scenario artifact content hash does not match its canonical definition",
                details={"expected": computed_hash, "provided": artifact.content_hash},
            )
        normalized = artifact.model_copy(
            update={"definition": deepcopy(canonical), "content_hash": computed_hash}
        )
        return ScenarioArtifactCandidate(
            artifact=normalized,
            definition=canonical_authored,
            canonical_document=deepcopy(canonical),
            content_hash=computed_hash,
        )

    @staticmethod
    def from_definition(
        definition: ScenarioDefinitionV3 | Mapping[str, Any],
        *,
        content_type: Literal["draft", "release"],
    ) -> ScenarioArtifact:
        try:
            authored = (
                definition
                if isinstance(definition, ScenarioDefinitionV3)
                else ScenarioDefinitionV3.model_validate(deepcopy(dict(definition)))
            )
            canonical = canonical_document_payload(authored.model_dump(mode="json"))
            authored = ScenarioDefinitionV3.model_validate(canonical)
        except (ValidationError, ValueError) as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The Scenario definition cannot be exported as a portable v3 artifact",
                details={
                    "errors": _validation_details(exc)
                    if isinstance(exc, ValidationError)
                    else {"reason": str(exc)}
                },
            ) from exc
        metadata = authored.metadata
        return ScenarioArtifact(
            artifact_type=PORTABLE_ARTIFACT_TYPE,
            artifact_version=PORTABLE_ARTIFACT_VERSION,
            content_type=content_type,
            schema_version=SCENARIO_DOCUMENT_SCHEMA_VERSION,
            scenario=ScenarioArtifactMetadata(
                key=metadata.key,
                name=metadata.name,
                description=metadata.description,
            ),
            content_hash=scenario_content_hash(canonical),
            definition=canonical,
        )

    @staticmethod
    def serialize(artifact: ScenarioArtifact | ScenarioArtifactCandidate) -> str:
        value = artifact.artifact if isinstance(artifact, ScenarioArtifactCandidate) else artifact
        # model_dump(mode="json") is detached and JSON-compatible.  Sorting object
        # keys never reorders semantic arrays, so authored order remains intact.
        payload = value.model_dump(mode="json", exclude_none=True)
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                separators=(",", ": "),
            )
            + "\n"
        )


def parse_scenario_artifact(raw: bytes | str | Mapping[str, Any]) -> ScenarioArtifactCandidate:
    """Parse one strict portable artifact and return its canonical candidate."""

    return ScenarioArtifactCodec.parse(raw)


def serialize_scenario_artifact(artifact: ScenarioArtifact | ScenarioArtifactCandidate) -> str:
    """Serialize a portable artifact as deterministic, human-readable UTF-8 JSON."""

    return ScenarioArtifactCodec.serialize(artifact)


def rebind_root_identity(
    definition: ScenarioDefinitionV3 | Mapping[str, Any],
    *,
    target_scenario_key: str,
    target_scenario_name: str | None = None,
) -> dict[str, Any]:
    """Rebind only Scenario root identity; Scenario-local keys remain untouched."""

    try:
        key: str = TypeAdapter(StableKey).validate_python(target_scenario_key)
    except ValidationError as exc:
        raise ScenarioPortabilityError(
            "INVALID_TARGET_SCENARIO_KEY",
            "The target Scenario key is invalid",
            details={"field": "target_scenario_key", "errors": _validation_details(exc)},
        ) from exc
    if isinstance(definition, ScenarioDefinitionV3):
        document = definition.model_dump(mode="json")
    else:
        document = deepcopy(dict(definition))
    metadata = document.get("metadata")
    world = document.get("world")
    if not isinstance(metadata, dict) or not isinstance(world, dict):
        raise ScenarioPortabilityError(
            "INVALID_ARTIFACT",
            "The Scenario definition has no rebindable root identity",
        )
    metadata["key"] = key
    world["key"] = key
    if target_scenario_name is not None:
        if not isinstance(target_scenario_name, str) or not target_scenario_name.strip():
            raise ScenarioPortabilityError(
                "INVALID_TARGET_SCENARIO_KEY",
                "The target Scenario name is invalid",
                details={"field": "target_scenario_name"},
            )
        metadata["name"] = target_scenario_name
        world["name"] = target_scenario_name
    try:
        authored = ScenarioDefinitionV3.model_validate(document)
        return canonical_document_payload(authored.model_dump(mode="json"))
    except (ValidationError, ValueError) as exc:
        raise ScenarioPortabilityError(
            "INVALID_TARGET_SCENARIO_KEY",
            "The target Scenario root identity cannot be applied to this definition",
            details={
                "errors": _validation_details(exc)
                if isinstance(exc, ValidationError)
                else {"reason": str(exc)}
            },
        ) from exc


def _decode_json_object(raw: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(raw, bytes):
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise _input_limit("serialized artifact exceeds the byte limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The Scenario artifact must be UTF-8 JSON",
            ) from exc
    elif isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The Scenario artifact must be UTF-8 JSON",
            ) from exc
        if len(encoded) > MAX_ARTIFACT_BYTES:
            raise _input_limit("serialized artifact exceeds the byte limit")
        text = raw
    elif isinstance(raw, Mapping):
        try:
            text = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "The Scenario artifact must contain JSON-compatible values",
            ) from exc
        if len(text.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise _input_limit("serialized artifact exceeds the byte limit")
    else:
        raise ScenarioPortabilityError(
            "INVALID_ARTIFACT",
            "The Scenario artifact input must be UTF-8 JSON or an object",
        )

    try:
        decoded = json.loads(text, parse_constant=_reject_non_finite_json)
    except (JSONDecodeError, ValueError) as exc:
        raise ScenarioPortabilityError(
            "INVALID_ARTIFACT",
            "The Scenario artifact is not valid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise ScenarioPortabilityError(
            "INVALID_ARTIFACT",
            "The Scenario artifact root must be a JSON object",
        )
    _enforce_structure_limits(decoded)
    return decoded


def _validate_envelope_discriminators(document: Mapping[str, Any]) -> None:
    if "artifact_type" not in document:
        raise ScenarioPortabilityError("INVALID_ARTIFACT", "The artifact_type field is required")
    if document.get("artifact_type") != PORTABLE_ARTIFACT_TYPE:
        raise ScenarioPortabilityError(
            "UNSUPPORTED_ARTIFACT_TYPE",
            "The Scenario artifact_type is not supported",
            details={"artifact_type": document.get("artifact_type")},
        )
    if "artifact_version" not in document:
        raise ScenarioPortabilityError("INVALID_ARTIFACT", "The artifact_version field is required")
    if type(document.get("artifact_version")) is not int or document.get("artifact_version") != 1:
        raise ScenarioPortabilityError(
            "UNSUPPORTED_ARTIFACT_VERSION",
            "Only artifact_version 1 is supported",
            details={"artifact_version": document.get("artifact_version")},
        )
    if "schema_version" not in document:
        raise ScenarioPortabilityError("INVALID_ARTIFACT", "The schema_version field is required")
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 3:
        raise ScenarioPortabilityError(
            "UNSUPPORTED_SCENARIO_SCHEMA_VERSION",
            "Only current Scenario schema_version 3 is portable",
            details={"schema_version": document.get("schema_version")},
        )
    if "content_type" not in document:
        raise ScenarioPortabilityError("INVALID_ARTIFACT", "The content_type field is required")
    if document.get("content_type") not in PORTABLE_CONTENT_TYPES:
        raise ScenarioPortabilityError(
            "INVALID_CONTENT_TYPE",
            "Scenario artifacts must be draft or release",
            details={"content_type": document.get("content_type")},
        )


def _validate_metadata_consistency(
    metadata: ScenarioArtifactMetadata,
    definition: ScenarioDefinitionV3,
) -> None:
    authored = definition.metadata
    fields = ("key", "name", "description")
    mismatches = {
        field: {"envelope": getattr(metadata, field), "definition": getattr(authored, field)}
        for field in fields
        if getattr(metadata, field) != getattr(authored, field)
    }
    if mismatches:
        raise ScenarioPortabilityError(
            "ARTIFACT_METADATA_MISMATCH",
            "Artifact metadata does not match the authored Scenario definition",
            details={"mismatches": mismatches},
        )


def _enforce_structure_limits(
    value: object, *, depth: int = 0, entries: list[int] | None = None
) -> None:
    counter = entries if entries is not None else [0]
    if depth > MAX_ARTIFACT_DEPTH:
        raise _input_limit("artifact JSON nesting exceeds the depth limit")
    if isinstance(value, str):
        if len(value) > MAX_ARTIFACT_STRING_LENGTH:
            raise _input_limit("artifact string exceeds the length limit")
        return
    if isinstance(value, Mapping):
        counter[0] += len(value)
        if counter[0] > MAX_ARTIFACT_CONTAINER_ENTRIES:
            raise _input_limit("artifact aggregate entry count exceeds the limit")
        for key, child in value.items():
            _enforce_structure_limits(key, depth=depth + 1, entries=counter)
            _enforce_structure_limits(child, depth=depth + 1, entries=counter)
        return
    if isinstance(value, list):
        counter[0] += len(value)
        if counter[0] > MAX_ARTIFACT_CONTAINER_ENTRIES:
            raise _input_limit("artifact aggregate entry count exceeds the limit")
        for child in value:
            _enforce_structure_limits(child, depth=depth + 1, entries=counter)


def _input_limit(reason: str) -> ScenarioPortabilityError:
    return ScenarioPortabilityError(
        "ARTIFACT_INPUT_LIMIT",
        "The Scenario artifact exceeds the portability input limits",
        details={"reason": reason},
    )


def _reject_non_finite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON number {value} is not supported")


def _validation_details(exc: ValidationError | ValueError) -> list[dict[str, Any]] | dict[str, str]:
    if isinstance(exc, ValidationError):
        return [
            {
                "path": ".".join(str(part) for part in error.get("loc", ())),
                "type": str(error.get("type", "validation_error")),
                "message": str(error.get("msg", "invalid value")),
            }
            for error in exc.errors()[:20]
        ]
    return {"reason": str(exc)}


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_ARTIFACT_CONTAINER_ENTRIES",
    "MAX_ARTIFACT_DEPTH",
    "MAX_ARTIFACT_STRING_LENGTH",
    "PORTABLE_ARTIFACT_TYPE",
    "PORTABLE_ARTIFACT_VERSION",
    "ScenarioArtifact",
    "ScenarioArtifactCandidate",
    "ScenarioArtifactCodec",
    "ScenarioArtifactMetadata",
    "ScenarioPortabilityError",
    "parse_scenario_artifact",
    "rebind_root_identity",
    "safe_artifact_filename",
    "serialize_scenario_artifact",
]


def safe_artifact_filename(
    scenario_key: str,
    *,
    content_type: Literal["draft", "release"],
    version_number: int | None = None,
) -> str:
    """Return the shared cross-platform download/export filename.

    The filename is derived only from the validated stable Scenario key and
    explicit artifact kind/version.  It never accepts a path or any metadata
    that could introduce path traversal.
    """

    try:
        key: str = TypeAdapter(StableKey).validate_python(scenario_key)
    except ValidationError as exc:
        raise ScenarioPortabilityError(
            "INVALID_TARGET_SCENARIO_KEY",
            "The Scenario key cannot be used as an artifact filename",
            details={"field": "scenario_key", "errors": _validation_details(exc)},
        ) from exc
    if content_type == "draft":
        if version_number is not None:
            raise ScenarioPortabilityError(
                "INVALID_ARTIFACT",
                "A Draft artifact filename cannot include a version number",
            )
        return f"{key}.draft.scenario.json"
    if version_number is None or type(version_number) is not int or version_number < 1:
        raise ScenarioPortabilityError(
            "INVALID_ARTIFACT",
            "A Release artifact filename requires a positive version number",
        )
    return f"{key}.v{version_number}.scenario.json"
