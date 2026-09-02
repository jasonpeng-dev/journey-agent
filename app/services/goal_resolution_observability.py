"""Durable, provider-safe diagnostics for Goal resolution."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.agent.generic import GenericGoalResolution
from app.agent.provider import GenericModelProvider, provider_call_metadata
from app.infrastructure.db.models import GoalResolutionAttempt

_SAFE_PROVIDER_METADATA_KEYS = frozenset(
    {
        "provider",
        "model",
        "call_type",
        "latency_ms",
        "wall_clock_latency_ms",
        "started_at",
        "finished_at",
        "request_started_at",
        "request_send_completed_at",
        "response_headers_received_at",
        "first_response_byte_at",
        "request_cancelled_at",
        "response_bytes_received",
        "timeout_subtype",
        "outcome",
        "error_category",
        "context_bytes",
        "request_size_bytes",
        "prompt_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "final_content_bytes",
        "finish_reason",
        "thinking_mode",
        "reasoning_effort",
        "configured_output_token_limit",
        "profile",
        "http_timeout_seconds",
        "total_deadline_seconds",
    }
)
_SAFE_ATTEMPT_KEYS = frozenset(
    {
        "stage",
        "attempt",
        "source",
        "status",
        "validation",
        "result",
        "rejection_code",
        "candidate_keys",
    }
)
_SAFE_VALUE_TYPES = frozenset({"STRING", "INTEGER", "BOOLEAN", "ENUM"})
_SAFE_JSON_TYPES = frozenset({"string", "integer", "number", "boolean", "null", "array", "object"})
_SAFE_SCHEMA_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "array", "object", "required", "literal"}
)


def persist_goal_resolution_attempt(
    session_factory: Callable[[], Session],
    *,
    game_instance_id: UUID,
    scenario_version_id: UUID,
    goal: str,
    resolution: GenericGoalResolution | None,
    resolution_duration_ms: int,
    provider: GenericModelProvider | None = None,
    error_code: str | None = None,
    provider_calls: Sequence[dict[str, object]] | None = None,
    validation_diagnostics: Sequence[dict[str, object]] | None = None,
) -> GoalResolutionAttempt:
    """Commit a redacted Goal resolution result in an independent transaction.

    The caller must invoke this after the resolver's read-only phase and
    before any Task transaction.  The session factory must be bound to the
    same database Engine as the PLAY session, not to the PLAY session itself.
    Its explicit commit is the durability boundary that makes unresolved
    attempts survive the API rollback without committing Task work.
    """

    observation = (
        resolution.provider_observation
        if resolution is not None and isinstance(resolution.provider_observation, dict)
        else {}
    )
    provider_metadata = _safe_provider_metadata(observation, provider)
    provider_metadata_snapshot = provider_call_metadata(provider) if provider is not None else {}
    safe_validation_diagnostics = _safe_provider_validation_diagnostics(
        validation_diagnostics
        if validation_diagnostics is not None
        else observation.get("validation_diagnostics")
        if observation.get("validation_diagnostics") is not None
        else provider_metadata_snapshot.get("validation_diagnostics")
    )
    if safe_validation_diagnostics:
        provider_metadata["validation_diagnostics"] = safe_validation_diagnostics
    safe_provider_calls = _safe_provider_calls(
        provider_calls if provider_calls is not None else observation.get("provider_calls")
    )
    if safe_provider_calls:
        provider_metadata["provider_calls"] = safe_provider_calls
    grounding = observation.get("grounding")
    grounding_map = grounding if isinstance(grounding, dict) else {}
    attempts = _safe_attempts(observation.get("attempts"))
    observed_attempt_count = _safe_int(observation.get("attempt_count"))
    attempt_count = observed_attempt_count
    if attempt_count is None and attempts:
        attempt_count = len(attempts)
    recovery_used = bool(
        (attempt_count is not None and attempt_count > 1)
        or any(_is_recovery_attempt(item) for item in attempts)
    )
    raw_provider_purpose = _safe_text(
        observation.get("call_type") or provider_metadata.get("call_type")
    )
    provider_purpose = (
        _provider_purpose(raw_provider_purpose) if raw_provider_purpose is not None else None
    )
    provider_model = _safe_text(
        observation.get("model")
        or provider_metadata.get("model")
        or (getattr(provider, "model_name", None) if provider is not None else None)
    )
    status = resolution.status if resolution is not None else "ERROR"
    resolver_source = resolution.source if resolution is not None else "PROVIDER_ERROR"
    rejection_code = error_code or _safe_text(observation.get("rejection_code"))
    value_type_diagnostics = _safe_value_type_diagnostics(observation.get("value_type_diagnostics"))
    with session_factory() as audit_db:
        row = GoalResolutionAttempt(
            game_instance_id=game_instance_id,
            scenario_version_id=scenario_version_id,
            original_goal_text=goal,
            normalized_goal_text=_normalize_goal(goal),
            goal_hash=_goal_hash(goal),
            resolution_status=status,
            resolver_source=resolver_source,
            grounding_source=_safe_text(
                grounding_map.get("source")
                or (resolution.source if resolution is not None else None)
            ),
            grounded_public_entity_keys=_safe_string_list(grounding_map.get("entity_keys")),
            resolution_candidate_keys=(
                list(resolution.candidate_keys) if resolution is not None else []
            ),
            public_catalog_hash=_safe_hash(observation.get("catalog_hash")),
            focused_ontology_hash=_safe_hash(observation.get("ontology_hash")),
            interpretation_status=(
                _safe_text(observation.get("status"))
                if observation.get("stage") == "DYNAMIC_GOAL_INTERPRETATION"
                else None
            ),
            attempt_count=attempt_count,
            interpretation_attempts=attempts,
            recovery_used=recovery_used,
            backend_validation_result=_safe_text(observation.get("validation")),
            rejection_code=rejection_code,
            value_type_diagnostics=value_type_diagnostics,
            provider_purpose=provider_purpose,
            provider_model=provider_model,
            provider_metadata=provider_metadata,
            resolution_duration_ms=max(0, resolution_duration_ms),
        )
        audit_db.add(row)
        audit_db.flush()
        audit_db.commit()
    return row


def _goal_hash(goal: str) -> str:
    return hashlib.sha256(_normalize_goal(goal).encode("utf-8")).hexdigest()


def _normalize_goal(goal: str) -> str:
    return " ".join(goal.casefold().replace("_", " ").split())


def _safe_provider_metadata(
    observation: dict[str, object], provider: GenericModelProvider | None
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if provider is not None:
        metadata.update(provider_call_metadata(provider))
    metadata.update(
        {key: value for key, value in observation.items() if key in _SAFE_PROVIDER_METADATA_KEYS}
    )
    return {
        key: value
        for key, value in metadata.items()
        if key in _SAFE_PROVIDER_METADATA_KEYS and _is_safe_metadata_value(value)
    }


def _safe_provider_calls(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, object]] = []
    safe_keys = _SAFE_PROVIDER_METADATA_KEYS | frozenset({"call_sequence"})
    for raw in value:
        if not isinstance(raw, dict):
            continue
        item: dict[str, object] = {}
        for key in safe_keys:
            candidate = raw.get(key)
            if key == "call_sequence":
                integer = _safe_int(candidate)
                if integer is not None:
                    item[key] = integer
            elif key in _SAFE_PROVIDER_METADATA_KEYS and _is_safe_metadata_value(candidate):
                item[key] = candidate
        call_diagnostics = _safe_provider_validation_diagnostics(raw.get("validation_diagnostics"))
        if call_diagnostics:
            item["validation_diagnostics"] = call_diagnostics
        call_type = item.get("call_type")
        if isinstance(call_type, str):
            item["purpose"] = _provider_purpose(call_type)
        if not item:
            continue
        item["call_order"] = len(result) + 1
        result.append(item)
    return result


def _safe_provider_validation_diagnostics(value: object) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        diagnostic: dict[str, str] = {}
        error_type = raw.get("validation_error_type")
        if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error_type):
            diagnostic["validation_error_type"] = error_type
        field_path = raw.get("field_path")
        if isinstance(field_path, str) and re.fullmatch(r"[A-Za-z0-9_.<>\[\]-]{1,160}", field_path):
            diagnostic["field_path"] = field_path
        expected = raw.get("expected_type")
        if isinstance(expected, str) and expected in _SAFE_SCHEMA_TYPES:
            diagnostic["expected_type"] = expected
        actual = raw.get("actual_json_type")
        if isinstance(actual, str) and actual in _SAFE_JSON_TYPES:
            diagnostic["actual_json_type"] = actual
        if diagnostic:
            result.append(diagnostic)
        if len(result) >= 20:
            break
    return result


def _provider_purpose(call_type: str) -> str:
    if call_type == "DYNAMIC_GOAL":
        return "DYNAMIC_GOAL_INTERPRETATION"
    return call_type


def _safe_attempts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        item: dict[str, object] = {}
        for key in _SAFE_ATTEMPT_KEYS:
            candidate = raw.get(key)
            if key == "candidate_keys":
                strings = _safe_string_list(candidate)
                if strings:
                    item[key] = strings
            elif key == "attempt":
                integer = _safe_int(candidate)
                if integer is not None:
                    item[key] = integer
            elif isinstance(candidate, str):
                item[key] = candidate[:160]
        if item:
            result.append(item)
    return result


def _safe_value_type_diagnostics(value: object) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        expected = raw.get("expected_value_type")
        actual = raw.get("actual_candidate_json_type")
        if (
            isinstance(expected, str)
            and expected in _SAFE_VALUE_TYPES
            and isinstance(actual, str)
            and actual in _SAFE_JSON_TYPES
        ):
            result.append(
                {
                    "expected_value_type": expected,
                    "actual_candidate_json_type": actual,
                }
            )
    return result


def _safe_string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item[:160] for item in value if isinstance(item, str) and item.strip()]


def _safe_text(value: object) -> str | None:
    return value[:160] if isinstance(value, str) and value else None


def _safe_hash(value: object) -> str | None:
    if isinstance(value, str) and len(value) == 64:
        return value
    return None


def _safe_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_recovery_attempt(item: dict[str, object]) -> bool:
    attempt = _safe_int(item.get("attempt"))
    return attempt is not None and attempt > 1


def _is_safe_metadata_value(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


__all__ = ["persist_goal_resolution_attempt"]
