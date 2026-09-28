"""Canonical, in-memory failure boundary.

Phase A deliberately keeps the existing failure producers and recovery policy
unchanged.  This module gives those producers a lossless, typed representation
at the boundary and a deterministic compatibility projection for consumers that
still read ``code``, ``message`` and ``retryable``.

The adapters are intentionally structural instead of importing runtime
producer classes.  That keeps this domain module independent from the engine,
agent and provider layers and avoids an import cycle while still accepting the
current legacy exception/dataclass/model instances.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr


class FailureDomain(StrEnum):
    """The subsystem that owns a failure.

    Only ``ACTION_RUNTIME`` is eligible for the existing generic gameplay
    recovery path.  The enum is descriptive in Phase A; no recovery consumer
    reads it yet.
    """

    ACTION_RUNTIME = "ACTION_RUNTIME"
    PLAN_VALIDATION = "PLAN_VALIDATION"
    PROVIDER = "PROVIDER"
    GOAL = "GOAL"
    PERSISTENCE = "PERSISTENCE"
    COMPATIBILITY = "COMPATIBILITY"
    INTERNAL = "INTERNAL"


class FailurePhase(StrEnum):
    """Execution phase in which a producer surfaced a failure."""

    PREFLIGHT = "PREFLIGHT"
    RESOLVE = "RESOLVE"
    STATE = "STATE"
    EXECUTION = "EXECUTION"
    VALIDATION = "VALIDATION"
    PLANNING = "PLANNING"
    PROVIDER_CALL = "PROVIDER_CALL"
    GOAL_RESOLUTION = "GOAL_RESOLUTION"
    PERSISTENCE = "PERSISTENCE"
    COMPATIBILITY = "COMPATIBILITY"
    UNKNOWN = "UNKNOWN"


class FailureKind(StrEnum):
    """Backend-owned, producer-neutral failure taxonomy."""

    PRECONDITION_UNMET = "PRECONDITION_UNMET"
    RESOURCE_INSUFFICIENT = "RESOURCE_INSUFFICIENT"
    KNOWLEDGE_UNKNOWN = "KNOWLEDGE_UNKNOWN"
    TRAVEL_BLOCKED = "TRAVEL_BLOCKED"
    LOCALITY_INVALID = "LOCALITY_INVALID"
    ACTOR_UNAVAILABLE = "ACTOR_UNAVAILABLE"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    TARGET_INVALID = "TARGET_INVALID"
    AUTHORITY_BLOCKED = "AUTHORITY_BLOCKED"
    PARAMETER_INVALID = "PARAMETER_INVALID"
    VALIDATOR_REJECTED = "VALIDATOR_REJECTED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    COMPATIBILITY_ERROR = "COMPATIBILITY_ERROR"
    UNKNOWN = "UNKNOWN"


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw: dict[str, JsonValue] = Field(default_factory=dict)


class ResourceEvidence(_EvidenceModel):
    """Evidence about a quantity or resource source."""

    family: Literal["RESOURCE"] = "RESOURCE"
    resource_key: StrictStr | None = None
    required: JsonValue | None = None
    available: JsonValue | None = None
    deficit: JsonValue | None = None
    knowledge_status: StrictStr | None = None
    scope_key: StrictStr | None = None
    source_key: StrictStr | None = None
    target_key: StrictStr | None = None


class FactEvidence(_EvidenceModel):
    """Evidence about one typed Fact predicate."""

    family: Literal["FACT"] = "FACT"
    node_key: StrictStr | None = None
    fact_key: StrictStr | None = None
    required: JsonValue | None = None
    accepted_values: tuple[JsonValue, ...] = ()
    actual: JsonValue | None = None
    knowledge_status: StrictStr | None = None


class TransportEvidence(_EvidenceModel):
    """Evidence about a transport connector and its passability."""

    family: Literal["TRANSPORT"] = "TRANSPORT"
    transport_key: StrictStr | None = None
    source_region: StrictStr | None = None
    target_region: StrictStr | None = None
    endpoints: tuple[StrictStr, ...] = ()
    passable: bool | None = None
    knowledge_status: StrictStr | None = None


class LocalityEvidence(_EvidenceModel):
    """Evidence about actor/target locality constraints."""

    family: Literal["LOCALITY"] = "LOCALITY"
    actor_region: StrictStr | None = None
    target_region: StrictStr | None = None
    required_locality: StrictStr | None = None
    source_region: StrictStr | None = None
    target_key: StrictStr | None = None
    endpoint_keys: tuple[StrictStr, ...] = ()


class ActorEvidence(_EvidenceModel):
    """Evidence about actor binding, availability or reachability."""

    family: Literal["ACTOR"] = "ACTOR"
    actor_key: StrictStr | None = None
    required: JsonValue | None = None
    actual: JsonValue | None = None
    capability: StrictStr | None = None
    role_key: StrictStr | None = None
    reachability: StrictStr | None = None


class TargetEvidence(_EvidenceModel):
    """Evidence about a target lookup or target contract."""

    family: Literal["TARGET"] = "TARGET"
    target_key: StrictStr | None = None
    required: JsonValue | None = None
    actual: JsonValue | None = None
    interaction_key: StrictStr | None = None


class ParameterEvidence(_EvidenceModel):
    """Evidence about Action parameter validation."""

    family: Literal["PARAMETER"] = "PARAMETER"
    parameter_key: StrictStr | None = None
    required: JsonValue | None = None
    actual: JsonValue | None = None
    validation_error: StrictStr | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class AuthorityEvidence(_EvidenceModel):
    """Evidence about an authorization or binding decision."""

    family: Literal["AUTHORITY"] = "AUTHORITY"
    actor_key: StrictStr | None = None
    action_key: StrictStr | None = None
    target_key: StrictStr | None = None
    required: JsonValue | None = None
    actual: JsonValue | None = None


class GenericEvidence(_EvidenceModel):
    """Typed fallback that still preserves the complete JSON evidence map."""

    family: Literal["GENERIC"] = "GENERIC"
    details: dict[str, JsonValue] = Field(default_factory=dict)


type FailureEvidence = Annotated[
    ResourceEvidence
    | FactEvidence
    | TransportEvidence
    | LocalityEvidence
    | ActorEvidence
    | TargetEvidence
    | ParameterEvidence
    | AuthorityEvidence
    | GenericEvidence,
    Field(discriminator="family"),
]


class LegacyFailureView(BaseModel):
    """The stable compatibility shape consumed by the pre-Phase-A code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: StrictStr
    message: StrictStr | None = None
    retryable: bool | None = None


class FailureEvent(BaseModel):
    """Canonical in-memory failure event.

    ``legacy_retryable`` is compatibility data only.  It intentionally does
    not become a new recovery authority in Phase A; existing consumers still
    use the value projected from the old producer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: FailureDomain
    kind: FailureKind
    code: StrictStr = Field(min_length=1)
    phase: FailurePhase
    message: StrictStr | None = None
    evidence: FailureEvidence | None = None
    additional_evidence: tuple[FailureEvidence, ...] = ()
    action_key: StrictStr | None = None
    actor_key: StrictStr | None = None
    target_key: StrictStr | None = None
    knowledge_changes: tuple[dict[str, JsonValue], ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    producer: StrictStr = "unknown"
    legacy_retryable: bool | None = None
    provider_retryable: bool | None = None

    @property
    def retryable(self) -> bool | None:
        """Read-only compatibility alias; it does not change recovery policy."""

        return self.legacy_retryable

    @property
    def gameplay_recovery_eligible(self) -> bool:
        """Whether the existing generic Action recovery boundary may handle it."""

        return self.domain == FailureDomain.ACTION_RUNTIME

    @classmethod
    def from_legacy(cls, failure: object, **kwargs: Any) -> FailureEvent:
        return normalize_legacy_failure(failure, **kwargs)

    def to_legacy(self) -> LegacyFailureView:
        return project_legacy_failure(self)

    @property
    def legacy_view(self) -> LegacyFailureView:
        """Named property for callers migrating from the old failure object."""

        return self.to_legacy()


_KNOWN_KIND_BY_CODE: dict[str, FailureKind] = {
    "TRAVEL_BLOCKED": FailureKind.TRAVEL_BLOCKED,
    "KNOWN_TRANSPORT_BLOCKED": FailureKind.TRAVEL_BLOCKED,
    "TRANSPORT_BLOCKED": FailureKind.TRAVEL_BLOCKED,
    "TRANSPORT_NOT_CONFIRMED_BLOCKED": FailureKind.PRECONDITION_UNMET,
    "TRANSPORT_RESOURCE_INSUFFICIENT": FailureKind.RESOURCE_INSUFFICIENT,
    "KNOWN_RESOURCE_INSUFFICIENT": FailureKind.RESOURCE_INSUFFICIENT,
    "RESOURCE_INSUFFICIENT": FailureKind.RESOURCE_INSUFFICIENT,
    "RESOURCE_INVENTORY_UNKNOWN": FailureKind.KNOWLEDGE_UNKNOWN,
    "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN": FailureKind.KNOWLEDGE_UNKNOWN,
    "RESOURCE_SOURCE_UNKNOWN": FailureKind.KNOWLEDGE_UNKNOWN,
    "ACTION_PRECONDITION_UNKNOWN": FailureKind.KNOWLEDGE_UNKNOWN,
    "LOCALITY_INVALID": FailureKind.LOCALITY_INVALID,
    "LOCALITY_ROUTE_NOT_FOUND": FailureKind.LOCALITY_INVALID,
    "LOCALITY_NODE_NOT_FOUND": FailureKind.LOCALITY_INVALID,
    "LOCALITY_TARGET_NOT_FOUND": FailureKind.LOCALITY_INVALID,
    "LOCALITY_ACTOR_REGION_INVALID": FailureKind.LOCALITY_INVALID,
    "LOCALITY_TRANSPORT_ENDPOINT_INVALID": FailureKind.LOCALITY_INVALID,
    "LOCALITY_TRAVEL_SAME_REGION": FailureKind.LOCALITY_INVALID,
    "ACTOR_COMMAND_DISCONNECTED": FailureKind.ACTOR_UNAVAILABLE,
    "ACTOR_NOT_AVAILABLE": FailureKind.ACTOR_UNAVAILABLE,
    "RUNTIME_ACTOR_UNAVAILABLE": FailureKind.ACTOR_UNAVAILABLE,
    "RESOURCE_SURVEY_ALREADY_COMPLETED": FailureKind.ALREADY_COMPLETED,
    "SURVEY_ALREADY_COMPLETED": FailureKind.ALREADY_COMPLETED,
    "ACTION_TARGET_INVALID": FailureKind.TARGET_INVALID,
    "UNKNOWN_TARGET": FailureKind.TARGET_INVALID,
    "TARGET_INTERACTION_INVALID": FailureKind.TARGET_INVALID,
    "ACTION_NOT_AUTHORIZED": FailureKind.AUTHORITY_BLOCKED,
    "ACTOR_NOT_ALLOWED": FailureKind.AUTHORITY_BLOCKED,
    "ACTOR_CAPABILITY_MISSING": FailureKind.AUTHORITY_BLOCKED,
    "ACTOR_BINDING_INVALID": FailureKind.AUTHORITY_BLOCKED,
    "RUNTIME_ACTOR_BINDING_INVALID": FailureKind.AUTHORITY_BLOCKED,
    "ACTION_PARAMETERS_INVALID": FailureKind.PARAMETER_INVALID,
    "GENERIC_PLAN_PARAMETER_INVALID": FailureKind.PARAMETER_INVALID,
    "RULE_PARAMETER_INVALID": FailureKind.PARAMETER_INVALID,
    "RULE_PARAMETER_MISSING": FailureKind.PARAMETER_INVALID,
    "RULE_PARAMETER_TYPE_INVALID": FailureKind.PARAMETER_INVALID,
    "RULE_PARAMETER_RANGE_INVALID": FailureKind.PARAMETER_INVALID,
    "RULE_PARAMETER_UNKNOWN": FailureKind.PARAMETER_INVALID,
    "VALIDATOR_REJECTED": FailureKind.VALIDATOR_REJECTED,
    "PROPOSAL_INVALID": FailureKind.VALIDATOR_REJECTED,
    "MODEL_PLAN_REJECTED": FailureKind.VALIDATOR_REJECTED,
    "PROVIDER_ERROR": FailureKind.PROVIDER_ERROR,
    "MODEL_PROVIDER_TIMEOUT": FailureKind.PROVIDER_ERROR,
    "MODEL_PROVIDER_RESPONSE_INVALID": FailureKind.PROVIDER_ERROR,
    "MODEL_PROVIDER_TRANSPORT_ERROR": FailureKind.PROVIDER_ERROR,
    "COMPATIBILITY_ERROR": FailureKind.COMPATIBILITY_ERROR,
}

_KIND_BY_DOMAIN = {
    FailureDomain.PLAN_VALIDATION: FailureKind.VALIDATOR_REJECTED,
    FailureDomain.PROVIDER: FailureKind.PROVIDER_ERROR,
    FailureDomain.PERSISTENCE: FailureKind.INTERNAL_ERROR,
    FailureDomain.INTERNAL: FailureKind.INTERNAL_ERROR,
    FailureDomain.COMPATIBILITY: FailureKind.COMPATIBILITY_ERROR,
}


def normalize_legacy_failure(
    failure: object,
    *,
    domain: FailureDomain | str | None = None,
    phase: FailurePhase | str | None = None,
    producer: str | None = None,
    kind: FailureKind | str | None = None,
    evidence: FailureEvidence | Mapping[str, object] | None = None,
    additional_evidence: Sequence[FailureEvidence | Mapping[str, object]] = (),
    action_key: str | None = None,
    actor_key: str | None = None,
    target_key: str | None = None,
    knowledge_changes: Sequence[Mapping[str, object]] = (),
    metadata: Mapping[str, object] | None = None,
    provider_retryable: bool | None = None,
) -> FailureEvent:
    """Normalize one current producer without changing its behavior.

    The explicit keyword overrides are used when a producer's legacy object
    does not carry phase or typed context (for example an authored
    ``RuleFailure``).  Unknown codes remain ``UNKNOWN`` unless the caller has
    supplied an explicit ``kind``; the adapter never guesses from a free-form
    message.
    """

    payload = _failure_payload(failure)
    code = _required_text(payload.get("code") or payload.get("failure_code"), "code")
    message = _optional_text(payload.get("message"))
    if message is None:
        message = _optional_text(payload.get("error"))
    retryable = _optional_bool(payload.get("retryable", payload.get("legacy_retryable")))

    resolved_domain = _coerce_enum(
        FailureDomain,
        domain,
        default=_infer_domain(failure, payload),
    )
    raw_phase = cast(FailurePhase | str | None, phase or payload.get("phase"))
    resolved_phase = _coerce_enum(FailurePhase, raw_phase, default=_infer_phase(failure))
    raw_metadata = _json_mapping(metadata)
    if raw_phase is not None and resolved_phase == FailurePhase.UNKNOWN:
        raw_metadata.setdefault("legacy_phase", _json_value(raw_phase))
    details = _details_payload(failure, payload)
    if details:
        raw_metadata.setdefault("legacy_details", details)
    if "failure_code" in payload and payload.get("failure_code") != code:
        raw_metadata.setdefault("legacy_failure_code", _json_value(payload["failure_code"]))
    if "dimension" in payload:
        raw_metadata.setdefault("validation_dimension", _json_value(payload["dimension"]))

    resolved_evidence = _coerce_evidence(evidence)
    if resolved_evidence is None:
        resolved_evidence = _evidence_from_payload(code, payload, details)
    resolved_additional = tuple(
        item for raw in additional_evidence if (item := _coerce_evidence(raw)) is not None
    )
    if resolved_evidence is not None:
        raw_metadata.setdefault("evidence_family", resolved_evidence.family)
    resolved_kind = _coerce_enum(
        FailureKind,
        kind,
        default=_infer_kind(code, resolved_domain, failure, resolved_evidence),
    )

    return FailureEvent(
        domain=resolved_domain,
        kind=resolved_kind,
        code=code,
        phase=resolved_phase,
        message=message,
        evidence=resolved_evidence,
        additional_evidence=resolved_additional,
        action_key=action_key or _optional_text(payload.get("action_key")),
        actor_key=actor_key or _optional_text(payload.get("actor_key")),
        target_key=target_key or _optional_text(payload.get("target_key")),
        knowledge_changes=tuple(
            _json_mapping(item)
            for item in (knowledge_changes or _mapping_sequence(payload.get("knowledge_changes")))
        ),
        metadata=raw_metadata,
        producer=producer or _optional_text(payload.get("producer")) or type(failure).__name__,
        legacy_retryable=retryable,
        provider_retryable=(
            provider_retryable
            if provider_retryable is not None
            else _optional_bool(payload.get("provider_retryable"))
        ),
    )


def project_legacy_failure(event: FailureEvent) -> LegacyFailureView:
    """Project the old observable failure fields deterministically."""

    return LegacyFailureView(
        code=event.code,
        message=event.message,
        retryable=event.legacy_retryable,
    )


# Descriptive aliases make the boundary easy to discover from either direction.
failure_event_from_legacy = normalize_legacy_failure
legacy_failure_from_event = project_legacy_failure


def _failure_payload(failure: object) -> dict[str, object]:
    if isinstance(failure, Mapping):
        return {str(key): value for key, value in failure.items()}
    model_dump = getattr(failure, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", exclude_none=True)
        if isinstance(dumped, Mapping):
            return {str(key): value for key, value in dumped.items()}
    payload: dict[str, object] = {}
    for key in (
        "code",
        "failure_code",
        "message",
        "retryable",
        "legacy_retryable",
        "provider_retryable",
        "producer",
        "phase",
        "details",
        "action_key",
        "actor_key",
        "target_key",
        "knowledge_changes",
        "dimension",
        "required",
        "actual",
        "reason_code",
        "required_interaction_key",
        "actual_interactions",
        "transport_key",
        "source_region",
        "target_region",
        "resource_key",
        "scope_region",
        "required_amount",
        "projected_known_available_amount",
        "deficit",
        "parameter_key",
        "parameter_error",
        "validation_error",
        "actual_parameters",
        "blocking_condition",
        "known_predicate",
        "required_locality",
        "validation_diagnostics",
        "recovery_feedback",
        "grounding_recovery_feedback",
        "resolution_observation",
    ):
        value = getattr(failure, key, None)
        if value is not None:
            payload[key] = value
    if "message" not in payload:
        text = str(failure)
        if text:
            payload["message"] = text
    return payload


def _details_payload(failure: object, payload: Mapping[str, object]) -> dict[str, JsonValue]:
    details = payload.get("details")
    result = _json_mapping(details) if isinstance(details, Mapping) else {}
    for key, value in payload.items():
        if key in {
            "code",
            "failure_code",
            "message",
            "retryable",
            "legacy_retryable",
            "provider_retryable",
            "producer",
            "details",
        }:
            continue
        if value is not None:
            result.setdefault(key, _json_value(value))
    if not result and isinstance(failure, Mapping):
        result = _json_mapping(failure)
    return result


def _infer_domain(failure: object, payload: Mapping[str, object]) -> FailureDomain:
    name = type(failure).__name__
    code = str(payload.get("code", payload.get("failure_code", "")))
    if name == "PlanViolation" or payload.get("dimension") is not None:
        return FailureDomain.PLAN_VALIDATION
    if name in {"GenericProviderError", "ProviderTransportError"}:
        return FailureDomain.PROVIDER
    if name in {"GenericAgentError", "PlanViolation"}:
        if code.startswith("GOAL_"):
            return FailureDomain.GOAL
        if code.startswith("MODEL_") or code.startswith("PLAN_"):
            return FailureDomain.PLAN_VALIDATION
    if code in {
        "MODEL_PROVIDER_TIMEOUT",
        "MODEL_PROVIDER_RESPONSE_INVALID",
        "MODEL_PROVIDER_TRANSPORT_ERROR",
        "PROVIDER_ERROR",
    }:
        return FailureDomain.PROVIDER
    if name in {"PersistenceError", "IntegrityError"}:
        return FailureDomain.PERSISTENCE
    if name in {"CompatibilityError", "LegacyCompatibilityError"}:
        return FailureDomain.COMPATIBILITY
    if name == "GenericActionError" and _is_internal_action_code(code):
        return FailureDomain.INTERNAL
    if name == "GenericGameError" and _is_internal_game_code(code):
        return FailureDomain.INTERNAL
    if name in {"RuleFailure", "GenericGameError", "GenericActionError", "LocalityEngineError"}:
        return FailureDomain.ACTION_RUNTIME
    return FailureDomain.INTERNAL


def _is_internal_action_code(code: str) -> bool:
    """Identify Action boundary failures that are lifecycle/integrity errors.

    ``GenericActionError`` is intentionally a compatibility envelope for both
    gameplay gates and request/lifecycle failures.  The latter must not become
    eligible for GenericAgent gameplay recovery merely because they use the
    same exception class.
    """

    return code.startswith(
        (
            "ACTION_IDEMPOTENCY_",
            "ACTION_DECISION_",
            "WORLD_OPERATION_",
            "GENERIC_RUNTIME_",
            "RUNTIME_",
        )
    ) or code in {"ACTION_NOT_FOUND", "ACTION_LIFECYCLE_INVALID"}


def _is_internal_game_code(code: str) -> bool:
    """Identify GenericGame state/integrity failures outside gameplay policy."""

    return (
        code.startswith(
            (
                "RUNTIME_",
                "RULE_",
                "RESOURCE_REGION_KNOWLEDGE_",
                "RELATION_KNOWLEDGE_",
                "ACTION_TARGET_KNOWLEDGE_",
                "GENERIC_RUNTIME_",
                "GAME_",
            )
        )
        or code == "GAME_INSTANCE_NOT_FOUND"
    )


def _infer_phase(failure: object) -> FailurePhase:
    name = type(failure).__name__
    if name == "PlanViolation":
        return FailurePhase.PREFLIGHT
    if name == "GenericProviderError":
        return FailurePhase.PROVIDER_CALL
    if name in {"GenericAgentError"}:
        return FailurePhase.PLANNING
    if name in {"RuleFailure", "GenericGameError", "GenericActionError", "LocalityEngineError"}:
        return FailurePhase.EXECUTION
    return FailurePhase.UNKNOWN


def _infer_kind(
    code: str,
    domain: FailureDomain,
    failure: object,
    evidence: FailureEvidence | None = None,
) -> FailureKind:
    if domain == FailureDomain.PLAN_VALIDATION:
        return FailureKind.VALIDATOR_REJECTED
    if domain == FailureDomain.PROVIDER:
        return FailureKind.PROVIDER_ERROR
    if domain == FailureDomain.INTERNAL:
        producer_name = type(failure).__name__
        is_internal_producer = producer_name in {
            "PersistenceError",
            "IntegrityError",
            "RuleEngineError",
        }
        if is_internal_producer or code.startswith(
            (
                "INTERNAL_",
                "PERSISTENCE_",
                "RULE_",
                "RUNTIME_",
                "WORLD_OPERATION_",
                "ACTION_IDEMPOTENCY_",
                "ACTION_DECISION_",
                "GENERIC_RUNTIME_",
                "GAME_",
            )
        ):
            return FailureKind.INTERNAL_ERROR
        return FailureKind.UNKNOWN
    if evidence is not None:
        if evidence.family == "RESOURCE":
            if evidence.knowledge_status == "UNKNOWN":
                return FailureKind.KNOWLEDGE_UNKNOWN
            if evidence.deficit is not None:
                return FailureKind.RESOURCE_INSUFFICIENT
        elif evidence.family == "FACT":
            return FailureKind.PRECONDITION_UNMET
        elif evidence.family == "TRANSPORT":
            if evidence.passable is False:
                return FailureKind.TRAVEL_BLOCKED
        elif evidence.family == "LOCALITY":
            return FailureKind.LOCALITY_INVALID
        elif evidence.family == "ACTOR":
            return FailureKind.ACTOR_UNAVAILABLE
        elif evidence.family == "TARGET":
            return FailureKind.TARGET_INVALID
        elif evidence.family == "PARAMETER":
            return FailureKind.PARAMETER_INVALID
        elif evidence.family == "AUTHORITY":
            return FailureKind.AUTHORITY_BLOCKED
    known = _KNOWN_KIND_BY_CODE.get(code)
    if known is not None:
        return known
    if domain in _KIND_BY_DOMAIN:
        return _KIND_BY_DOMAIN[domain]
    # A Rule/engine producer with explicit precondition-shaped details is safe
    # to classify.  A free-form unknown code is deliberately left UNKNOWN.
    payload = _failure_payload(failure)
    dimension = str(payload.get("dimension", ""))
    if dimension in {"ACTION_PRECONDITION", "FACT_PRECONDITION", "RESOURCE_QUANTITY"}:
        return FailureKind.PRECONDITION_UNMET
    return FailureKind.UNKNOWN


def _evidence_from_payload(
    code: str,
    payload: Mapping[str, object],
    details: Mapping[str, JsonValue],
) -> FailureEvidence | None:
    source = dict(details)
    if any(
        key in source
        for key in ("resource_key", "required_amount", "projected_known_available_amount")
    ):
        return ResourceEvidence(
            resource_key=_optional_text(source.get("resource_key")),
            required=source.get("required_amount", source.get("required")),
            available=source.get("projected_known_available_amount", source.get("available")),
            deficit=source.get("deficit"),
            knowledge_status=_optional_text(source.get("knowledge_status")),
            scope_key=_optional_text(source.get("scope_region", source.get("scope_key"))),
            source_key=_optional_text(source.get("source_key")),
            target_key=_optional_text(source.get("target_key")),
            raw=source,
        )
    if any(key in source for key in ("fact_key", "accepted_values", "fact")):
        accepted = source.get("accepted_values", ())
        accepted_values = _json_sequence(accepted)
        return FactEvidence(
            node_key=_optional_text(source.get("node_key", source.get("target_key"))),
            fact_key=_optional_text(source.get("fact_key")),
            required=source.get("required"),
            accepted_values=accepted_values,
            actual=source.get("actual"),
            knowledge_status=_optional_text(source.get("knowledge_status")),
            raw=source,
        )
    transport_detail = any(
        key in source for key in ("transport_key", "source_region", "target_region")
    )
    if transport_detail or code in {
        "TRAVEL_BLOCKED",
        "KNOWN_TRANSPORT_BLOCKED",
        "TRANSPORT_BLOCKED",
    }:
        return TransportEvidence(
            transport_key=_optional_text(source.get("transport_key", source.get("target_key"))),
            source_region=_optional_text(source.get("source_region")),
            target_region=_optional_text(source.get("target_region")),
            endpoints=_json_str_sequence(
                source.get("transport_endpoints", source.get("endpoints"))
            ),
            passable=_optional_bool(source.get("passable")),
            knowledge_status=_optional_text(source.get("knowledge_status")),
            raw=source,
        )
    if code.startswith("LOCALITY_") or any(
        key in source for key in ("required_locality", "actor_region", "target_region")
    ):
        return LocalityEvidence(
            actor_region=_optional_text(source.get("actor_region", source.get("source_region"))),
            target_region=_optional_text(source.get("target_region")),
            required_locality=_optional_text(
                source.get("required_locality", source.get("required"))
            ),
            source_region=_optional_text(source.get("source_region")),
            target_key=_optional_text(source.get("target_key")),
            endpoint_keys=_json_str_sequence(source.get("transport_endpoints")),
            raw=source,
        )
    if any(key in source for key in ("parameter_key", "parameter_error", "actual_parameters")):
        actual_parameters = source.get("actual_parameters")
        return ParameterEvidence(
            parameter_key=_optional_text(source.get("parameter_key")),
            required=source.get("required"),
            actual=source.get("actual"),
            validation_error=_optional_text(
                source.get("validation_error", source.get("parameter_error"))
            ),
            parameters=_json_mapping(actual_parameters),
            raw=source,
        )
    if any(key in source for key in ("actor_key", "reachability", "required_value")) and (
        code.startswith("ACTOR_") or "reachability" in source
    ):
        return ActorEvidence(
            actor_key=_optional_text(source.get("actor_key")),
            required=source.get("required_value", source.get("required")),
            actual=source.get("actual"),
            capability=_optional_text(source.get("capability")),
            role_key=_optional_text(source.get("role_key")),
            reachability=_optional_text(source.get("reachability", source.get("actual"))),
            raw=source,
        )
    if any(
        key in source for key in ("target_key", "required_interaction_key", "actual_interactions")
    ):
        return TargetEvidence(
            target_key=_optional_text(source.get("target_key")),
            required=source.get("required_interaction_key", source.get("required")),
            actual=source.get("actual_interactions", source.get("actual")),
            interaction_key=_optional_text(source.get("required_interaction_key")),
            raw=source,
        )
    if any(key in source for key in ("action_key", "required", "actual")) and code.startswith(
        ("ACTION_", "RUNTIME_ACTOR_")
    ):
        return AuthorityEvidence(
            actor_key=_optional_text(source.get("actor_key")),
            action_key=_optional_text(source.get("action_key")),
            target_key=_optional_text(source.get("target_key")),
            required=source.get("required"),
            actual=source.get("actual"),
            raw=source,
        )
    if code in {
        "TRAVEL_BLOCKED",
        "KNOWN_TRANSPORT_BLOCKED",
        "TRANSPORT_BLOCKED",
        "TRANSPORT_NOT_CONFIRMED_BLOCKED",
    }:
        return TransportEvidence(
            transport_key=_optional_text(source.get("transport_key")),
            source_region=_optional_text(source.get("source_region")),
            target_region=_optional_text(source.get("target_region")),
            passable=False,
            knowledge_status="KNOWN",
            raw=source or {"code": code},
        )
    if code in {
        "TRANSPORT_RESOURCE_INSUFFICIENT",
        "KNOWN_RESOURCE_INSUFFICIENT",
        "RESOURCE_INSUFFICIENT",
    }:
        return ResourceEvidence(
            resource_key=_optional_text(source.get("resource_key")),
            required=source.get("required_amount", source.get("required")),
            available=source.get(
                "projected_known_available_amount",
                source.get("available"),
            ),
            deficit=source.get("deficit"),
            knowledge_status="KNOWN",
            scope_key=_optional_text(source.get("scope_region", source.get("scope_key"))),
            source_key=_optional_text(source.get("source_key")),
            target_key=_optional_text(source.get("target_key")),
            raw=source or {"code": code},
        )
    if code in {
        "RESOURCE_INVENTORY_UNKNOWN",
        "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN",
        "RESOURCE_SOURCE_UNKNOWN",
    }:
        return ResourceEvidence(
            resource_key=_optional_text(source.get("resource_key")),
            required=source.get("required_amount", source.get("required")),
            knowledge_status="UNKNOWN",
            scope_key=_optional_text(source.get("scope_region", source.get("scope_key"))),
            source_key=_optional_text(source.get("source_key")),
            target_key=_optional_text(source.get("target_key")),
            raw=source or {"code": code},
        )
    if code.startswith("ACTOR_") or code.startswith("RUNTIME_ACTOR_"):
        return ActorEvidence(
            actor_key=_optional_text(source.get("actor_key")),
            required=source.get("required", source.get("required_value")),
            actual=source.get("actual"),
            capability=_optional_text(source.get("capability")),
            role_key=_optional_text(source.get("role_key")),
            reachability=_optional_text(source.get("reachability", source.get("actual"))),
            raw=source or {"code": code},
        )
    if code.startswith("RELAY_TARGET_"):
        return ActorEvidence(
            actor_key=_optional_text(source.get("actor_key")),
            actual=source.get("actual"),
            reachability=_optional_text(source.get("reachability")),
            raw=source or {"code": code},
        )
    if code.startswith("ACTION_TARGET") or code in {"UNKNOWN_TARGET", "TARGET_INVALID"}:
        return TargetEvidence(
            target_key=_optional_text(source.get("target_key")),
            required=source.get("required_interaction_key", source.get("required")),
            actual=source.get("actual_interactions", source.get("actual")),
            interaction_key=_optional_text(source.get("required_interaction_key")),
            raw=source or {"code": code},
        )
    if code.endswith("PARAMETERS_INVALID") or code.startswith("RULE_PARAMETER_"):
        return ParameterEvidence(
            parameter_key=_optional_text(source.get("parameter_key")),
            required=source.get("required"),
            actual=source.get("actual"),
            validation_error=_optional_text(
                source.get("validation_error", source.get("parameter_error"))
            ),
            parameters=_json_mapping(source.get("actual_parameters")),
            raw=source or {"code": code},
        )
    if code == "TRANSPORT_RESOURCE_INVALID":
        return ParameterEvidence(
            parameter_key="resource_key",
            validation_error="unknown resource identity",
            raw=source or {"code": code},
        )
    if code in {
        "ACTION_NOT_AUTHORIZED",
        "ACTION_NOT_ALLOWED",
        "ACTION_APPROVAL_REQUIRED",
        "ACTION_APPROVAL_REJECTED",
        "ACTOR_NOT_ALLOWED",
        "ACTOR_CAPABILITY_MISSING",
        "ACTOR_BINDING_INVALID",
    }:
        return AuthorityEvidence(
            actor_key=_optional_text(source.get("actor_key")),
            action_key=_optional_text(source.get("action_key")),
            target_key=_optional_text(source.get("target_key")),
            required=source.get("required"),
            actual=source.get("actual"),
            raw=source or {"code": code},
        )
    return GenericEvidence(details=source, raw=source) if source else None


def _coerce_evidence(
    value: FailureEvidence | Mapping[str, object] | None,
) -> FailureEvidence | None:
    if value is None:
        return None
    if isinstance(value, _EvidenceModel):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("Failure evidence must be a typed evidence model or mapping")
    family = value.get("family")
    models: dict[str, type[_EvidenceModel]] = {
        "RESOURCE": ResourceEvidence,
        "FACT": FactEvidence,
        "TRANSPORT": TransportEvidence,
        "LOCALITY": LocalityEvidence,
        "ACTOR": ActorEvidence,
        "TARGET": TargetEvidence,
        "PARAMETER": ParameterEvidence,
        "AUTHORITY": AuthorityEvidence,
        "GENERIC": GenericEvidence,
    }
    model = models.get(str(family), GenericEvidence)
    if model is GenericEvidence and family != "GENERIC":
        return GenericEvidence(details=_json_mapping(value), raw=_json_mapping(value))
    return cast(FailureEvidence, model.model_validate(value))


def _coerce_enum[T: StrEnum](enum_type: type[T], value: T | str | None, *, default: T) -> T:
    if value is None:
        return default
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError:
        return default


def _required_text(value: object, label: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"Legacy failure {label} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return _json_value(enum_value)
    return str(value)


def _json_mapping(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _json_value(item) for key, item in value.items()}


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _json_sequence(value: object) -> tuple[JsonValue, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(_json_value(item) for item in value)


def _json_str_sequence(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _json_sequence(value))


__all__ = [
    "ActorEvidence",
    "AuthorityEvidence",
    "FactEvidence",
    "FailureDomain",
    "FailureEvent",
    "FailureEvidence",
    "FailureKind",
    "FailurePhase",
    "GenericEvidence",
    "LegacyFailureView",
    "LocalityEvidence",
    "ParameterEvidence",
    "ResourceEvidence",
    "TargetEvidence",
    "TransportEvidence",
    "failure_event_from_legacy",
    "legacy_failure_from_event",
    "normalize_legacy_failure",
    "project_legacy_failure",
]
