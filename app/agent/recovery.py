"""Backend-owned recovery decisions and deterministic compatibility context.

The recovery layer deliberately knows nothing about Scenario authoring hints.
It consumes the canonical :class:`~app.domain.failures.FailureEvent` emitted by
the runtime boundary and projects the small amount of historical context that
the existing Agent/Play records still expose.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.domain.failures import (
    FailureDomain,
    FailureEvent,
    FailureKind,
    FailurePhase,
    normalize_legacy_failure,
)


class RecoveryDecision(StrEnum):
    """The only decisions a recovery consumer may act on."""

    REPLAN = "REPLAN"
    BLOCK = "BLOCK"
    REPAIR = "REPAIR"
    PLAYER_DECISION = "PLAYER_DECISION"
    RETRY_PROVIDER = "RETRY_PROVIDER"
    NOOP = "NOOP"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class RecoveryContext(BaseModel):
    """Typed decision plus the deterministic execution directives.

    ``decision`` is the decision the current compatibility path should apply.
    ``proposed_decision`` is the backend-derived decision in canonical mode.
    During the v2 compatibility track the old producer retryability may select
    the outward decision; ``legacy_parity`` makes any disagreement observable
    in tests and diagnostics instead of silently changing behavior.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: RecoveryDecision
    proposed_decision: RecoveryDecision
    legacy_decision: RecoveryDecision | None = None
    legacy_retryable: bool | None = None
    legacy_parity: bool | None = None
    failure_event: FailureEvent
    replan_reason: str
    relevant_blocker: dict[str, JsonValue] | None = None
    preserve_completed_steps: bool = True
    retire_failed_suffix: bool = True
    rebuild_knowledge: bool = True
    rebuild_closure: bool = True
    rebuild_planner_input: bool = True
    preserve_planning_continuity: bool = True
    process_knowledge_changes: bool = True
    directives: tuple[str, ...] = ()
    state_markers: dict[str, JsonValue] = Field(default_factory=dict)


_PROVIDER_RETRYABLE_CODES = frozenset(
    {
        "MODEL_PROVIDER_TIMEOUT",
        "MODEL_PROVIDER_TRANSPORT_ERROR",
        "PROVIDER_TIMEOUT",
        "PROVIDER_TRANSPORT_ERROR",
    }
)

# These are stable runtime terminal gates.  They are retained as a typed
# compatibility fallback until every old producer supplies richer evidence.
class GenericRecoveryPolicy:
    """Sole backend authority for the next recovery decision.

    The policy has two explicit tracks.  ``legacy_mode=True`` preserves the
    v2/PUBLISHED outward behavior selected by the old ``retryable`` flag while
    still computing and exposing the canonical proposal.  New callers omit
    that compatibility mode and derive the decision from kind, domain,
    evidence and current execution markers.
    """

    def __init__(self, *, max_replans: int = 5) -> None:
        self.max_replans = max(0, max_replans)

    def evaluate(
        self,
        failure_event: FailureEvent,
        *,
        execution_context: Mapping[str, object] | None = None,
        replan_count: int = 0,
        state_markers: Mapping[str, object] | None = None,
        legacy_mode: bool = False,
    ) -> RecoveryContext:
        allowed_markers = {
            "state_changed",
            "knowledge_changed",
            "replan_exhausted",
            "new_knowledge",
            "closure_rebuilt",
        }
        markers = {
            str(key): _json_value(value)
            for key, value in (state_markers or {}).items()
            if key in allowed_markers and _json_value(value) is not None
        }
        if execution_context:
            # Context is deliberately a read-only input.  Only a bounded,
            # JSON-shaped marker projection is retained in the typed result.
            for key in ("state_changed", "knowledge_changed", "replan_exhausted"):
                if key in execution_context and key not in markers:
                    markers[key] = _json_value(execution_context[key])
        proposed = self._derive(failure_event, replan_count=replan_count, markers=markers)
        legacy_retryable = failure_event.legacy_retryable
        legacy_decision = (
            self._legacy_decision(
                failure_event,
                legacy_retryable=legacy_retryable,
                replan_count=replan_count,
            )
            if legacy_retryable is not None
            else None
        )
        parity = (
            proposed == legacy_decision
            if legacy_decision is not None and failure_event.domain == FailureDomain.ACTION_RUNTIME
            else None
        )
        decision = legacy_decision if legacy_mode and legacy_decision is not None else proposed
        directives = self._directives(failure_event, decision, markers)
        return RecoveryContext(
            decision=decision,
            proposed_decision=proposed,
            legacy_decision=legacy_decision,
            legacy_retryable=legacy_retryable,
            legacy_parity=parity,
            failure_event=failure_event,
            replan_reason=failure_event.code,
            relevant_blocker=self._blocker(failure_event),
            directives=directives,
            state_markers=markers,
        )

    def decide(self, *args: Any, **kwargs: Any) -> RecoveryContext:
        """Discoverable alias used by recovery consumers and tests."""

        return self.evaluate(*args, **kwargs)

    def _derive(
        self,
        event: FailureEvent,
        *,
        replan_count: int,
        markers: Mapping[str, JsonValue],
    ) -> RecoveryDecision:
        if event.domain == FailureDomain.PLAN_VALIDATION:
            return RecoveryDecision.REPAIR
        if event.domain == FailureDomain.PROVIDER:
            if event.provider_retryable is True or event.code in _PROVIDER_RETRYABLE_CODES:
                return RecoveryDecision.RETRY_PROVIDER
            return RecoveryDecision.BLOCK
        if event.domain in {
            FailureDomain.PERSISTENCE,
            FailureDomain.INTERNAL,
            FailureDomain.COMPATIBILITY,
            FailureDomain.GOAL,
        }:
            return RecoveryDecision.SYSTEM_ERROR
        if event.domain != FailureDomain.ACTION_RUNTIME:
            return RecoveryDecision.SYSTEM_ERROR
        if replan_count >= self.max_replans or markers.get("replan_exhausted") is True:
            return RecoveryDecision.BLOCK
        if event.kind == FailureKind.ALREADY_COMPLETED:
            return RecoveryDecision.NOOP
        if event.kind == FailureKind.AUTHORITY_BLOCKED:
            requires_player = (
                event.code == "ACTION_APPROVAL_REQUIRED"
                or event.metadata.get("requires_player_decision") is True
            )
            return RecoveryDecision.PLAYER_DECISION if requires_player else RecoveryDecision.BLOCK
        if event.kind == FailureKind.INTERNAL_ERROR:
            return RecoveryDecision.SYSTEM_ERROR
        if event.kind in {
            FailureKind.RESOURCE_INSUFFICIENT,
            FailureKind.KNOWLEDGE_UNKNOWN,
            FailureKind.TRAVEL_BLOCKED,
            FailureKind.LOCALITY_INVALID,
            FailureKind.ACTOR_UNAVAILABLE,
            FailureKind.PRECONDITION_UNMET,
        }:
            return RecoveryDecision.REPLAN
        if event.kind in {
            FailureKind.TARGET_INVALID,
            FailureKind.PARAMETER_INVALID,
            FailureKind.VALIDATOR_REJECTED,
        }:
            return RecoveryDecision.BLOCK
        # Unknown action failures remain fail-closed in canonical mode.
        return RecoveryDecision.BLOCK

    def _legacy_decision(
        self,
        event: FailureEvent,
        *,
        legacy_retryable: bool | None,
        replan_count: int,
    ) -> RecoveryDecision:
        if event.domain == FailureDomain.PLAN_VALIDATION:
            return RecoveryDecision.REPAIR
        if event.domain == FailureDomain.PROVIDER:
            return (
                RecoveryDecision.RETRY_PROVIDER
                if event.provider_retryable is True
                else RecoveryDecision.BLOCK
            )
        if event.domain != FailureDomain.ACTION_RUNTIME:
            return RecoveryDecision.SYSTEM_ERROR
        if replan_count >= self.max_replans:
            return RecoveryDecision.BLOCK
        return RecoveryDecision.REPLAN if legacy_retryable is True else RecoveryDecision.BLOCK

    @staticmethod
    def _blocker(event: FailureEvent) -> dict[str, JsonValue] | None:
        evidence = event.evidence
        if evidence is not None:
            payload = evidence.model_dump(mode="json", exclude_none=True)
            family = str(payload.pop("family", "GENERIC"))
            return {"type": family, **payload}
        if event.kind == FailureKind.TRAVEL_BLOCKED:
            return {"type": "TRANSPORT_PASSABILITY", "current_value": "KNOWN_BLOCKED"}
        if event.kind == FailureKind.RESOURCE_INSUFFICIENT:
            return {"type": "RESOURCE_QUANTITY", "current_value": "KNOWN_INSUFFICIENT"}
        if event.kind == FailureKind.KNOWLEDGE_UNKNOWN:
            return {"type": "KNOWLEDGE", "current_value": "UNKNOWN"}
        if event.kind == FailureKind.LOCALITY_INVALID:
            return {"type": "LOCALITY", "current_value": "INVALID"}
        if event.kind == FailureKind.ACTOR_UNAVAILABLE:
            return {"type": "ACTOR", "current_value": "UNAVAILABLE"}
        return None

    @staticmethod
    def _directives(
        event: FailureEvent,
        decision: RecoveryDecision,
        markers: Mapping[str, JsonValue],
    ) -> tuple[str, ...]:
        if event.kind == FailureKind.ALREADY_COMPLETED:
            return ("do_not_repeat_unchanged_action",)
        if event.kind == FailureKind.RESOURCE_INSUFFICIENT:
            return ("use_latest_known_resource_evidence", "rebuild_dependency_source_context")
        if event.kind == FailureKind.TRAVEL_BLOCKED:
            return (
                "preserve_completed_steps",
                "incorporate_public_passability_reveal",
                "rebuild_route_dependencies",
            )
        if decision == RecoveryDecision.REPLAN:
            return ("rebuild_knowledge_closure_and_planner_input",)
        if decision == RecoveryDecision.REPAIR:
            return ("repair_rejected_plan_segment",)
        if decision == RecoveryDecision.RETRY_PROVIDER:
            return ("retry_provider_with_same_canonical_context",)
        return ()


def failure_event_from_json(raw: object) -> FailureEvent | None:
    """Read a persisted/in-process event without allowing malformed metadata to escape."""

    if isinstance(raw, FailureEvent):
        return raw
    if not isinstance(raw, Mapping):
        return None
    try:
        return FailureEvent.model_validate(raw)
    except Exception:
        return None


def normalize_runtime_failure(
    failure: object,
    *,
    action_key: str | None = None,
    actor_key: str | None = None,
    target_key: str | None = None,
    knowledge_changes: tuple[Mapping[str, object], ...] = (),
) -> FailureEvent:
    """Normalize a producer at the recovery boundary."""

    existing = getattr(failure, "failure_event", None)
    if existing is None and isinstance(failure, Mapping):
        existing = failure_event_from_json(failure.get("failure_event"))
    if isinstance(existing, FailureEvent):
        return existing.model_copy(
            update={
                "action_key": existing.action_key or action_key,
                "actor_key": existing.actor_key or actor_key,
                "target_key": existing.target_key or target_key,
                "knowledge_changes": existing.knowledge_changes or tuple(
                    dict(item) for item in knowledge_changes
                ),
            }
        )
    return normalize_legacy_failure(
        failure,
        domain=FailureDomain.ACTION_RUNTIME,
        phase=FailurePhase.EXECUTION,
        action_key=action_key,
        actor_key=actor_key,
        target_key=target_key,
        knowledge_changes=knowledge_changes,
    )


def _json_value(value: object) -> JsonValue | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        items = [_json_value(item) for item in value]
        return [item for item in items if item is not None]
    if isinstance(value, dict):
        return {
            str(key): item
            for key, raw in value.items()
            if (item := _json_value(raw)) is not None
        }
    return str(value)
