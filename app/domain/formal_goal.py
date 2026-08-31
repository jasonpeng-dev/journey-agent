"""Frozen Formal Goal V1 domain contracts.

This module deliberately stays below the Task, Planner, and PLAY services.
It defines the one authoritative value that those services will consume in
later phases: a frozen, exact-ScenarioVersion-bound collection of typed
conjunctive requirements.

The V1 vocabulary is intentionally closed.  A dynamic candidate can express
only the requirement kinds already implemented by the generic Objective
system.  The backend, rather than a provider, owns requirement identity,
canonical ordering, and the contract hash.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.domain.scenario import ScenarioVersionSnapshot
from app.domain.scenario_v2 import (
    ObjectiveDefinitionV2,
    ObjectivePrerequisiteV2,
    ObjectiveRequirementKind,
    ObjectiveRequirementKnowledgeGateV2,
    ObjectiveRequirementV2,
    ScenarioDefinitionV2,
)
from app.scenarios.serialization import scenario_content_hash

type FormalGoalScalar = StrictStr | StrictInt | StrictBool
type FormalGoalIdentity = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=200,
        pattern=r"^[a-z][a-z0-9_.:/-]{0,199}$",
    ),
]
type HashText = Annotated[
    StrictStr,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


class FormalGoalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FormalGoalSourceKind(StrEnum):
    """How the frozen contract was authored or interpreted."""

    PREDEFINED = "PREDEFINED"
    PARAMETERIZED = "PARAMETERIZED"
    AD_HOC_DYNAMIC = "AD_HOC_DYNAMIC"


class FormalGoalError(ValueError):
    """Fail-closed error raised while compiling or validating a Goal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FormalGoalScenarioProofV1(FormalGoalModel):
    """The immutable ScenarioVersion identity used by one Goal contract."""

    scenario_version_id: UUID
    scenario_content_hash: HashText
    scenario_schema_version: Literal[2] = 2


class FormalGoalObjectiveSourceV1(FormalGoalModel):
    """Authored Objective provenance retained by a PREDEFINED contract."""

    objective_key: StrictStr = Field(min_length=1, max_length=80)


class FormalGoalRequirementV1(FormalGoalModel):
    """One stable contract-level identity around the existing requirement type."""

    identity: FormalGoalIdentity
    requirement: ObjectiveRequirementV2
    source_objective_key: StrictStr | None = Field(default=None, max_length=80)
    source_requirement_key: StrictStr | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_provenance(self) -> FormalGoalRequirementV1:
        source_fields = (self.source_objective_key, self.source_requirement_key)
        if (source_fields[0] is None) != (source_fields[1] is None):
            raise ValueError(
                "Formal Goal requirement provenance needs both Objective and requirement keys"
            )
        if source_fields[0] is not None and source_fields[1] is not None:
            expected = f"{source_fields[0]}:{source_fields[1]}"
            if self.identity != expected:
                raise ValueError(
                    "PREDEFINED Formal Goal requirement identity must match its source keys"
                )
        return self


class FormalGoalPlanningPrerequisiteV1(FormalGoalModel):
    """Authored prerequisite compatibility, namespaced by Objective."""

    objective_key: StrictStr = Field(min_length=1, max_length=80)
    prerequisite: ObjectivePrerequisiteV2


class FormalGoalPlanningCompatibilityV1(FormalGoalModel):
    """Planning-only authored semantics preserved for PREDEFINED Goals."""

    prerequisites: tuple[FormalGoalPlanningPrerequisiteV1, ...] = ()


class FormalGoalContractV1(FormalGoalModel):
    """The immutable authoritative Formal Goal contract for V1.

    Completion requirements are an implicit conjunction.  Descriptions inside
    the reused ``ObjectiveRequirementV2`` remain available to projections, but
    are intentionally excluded from ``canonical_semantics`` and therefore do
    not affect the authoritative contract hash.
    """

    schema_version: Literal[1] = 1
    source_kind: FormalGoalSourceKind
    scenario: FormalGoalScenarioProofV1
    completion_requirements: tuple[FormalGoalRequirementV1, ...] = Field(min_length=1)
    predefined_objectives: tuple[FormalGoalObjectiveSourceV1, ...] = ()
    planning_compatibility: FormalGoalPlanningCompatibilityV1 = Field(
        default_factory=FormalGoalPlanningCompatibilityV1
    )
    compiler_version: StrictStr = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_contract(self) -> FormalGoalContractV1:
        identities = tuple(item.identity for item in self.completion_requirements)
        if len(set(identities)) != len(identities):
            raise ValueError("Formal Goal requirement identities must be unique")

        objective_keys = tuple(item.objective_key for item in self.predefined_objectives)
        if len(set(objective_keys)) != len(objective_keys):
            raise ValueError("Formal Goal Objective provenance must be unique")

        if self.source_kind == FormalGoalSourceKind.PREDEFINED:
            if not objective_keys:
                raise ValueError("PREDEFINED Formal Goal needs Objective provenance")
            expected = set(objective_keys)
            if any(
                item.source_objective_key not in expected or item.source_requirement_key is None
                for item in self.completion_requirements
            ):
                raise ValueError(
                    "PREDEFINED Formal Goal requirements need matching Objective provenance"
                )
        elif self.source_kind == FormalGoalSourceKind.AD_HOC_DYNAMIC:
            if objective_keys:
                raise ValueError("AD_HOC_DYNAMIC cannot carry authored Objective provenance")
            if self.planning_compatibility.prerequisites:
                raise ValueError("AD_HOC_DYNAMIC cannot inject authored prerequisites")
            if any(
                item.requirement.knowledge_gate is not None for item in self.completion_requirements
            ):
                raise ValueError("AD_HOC_DYNAMIC cannot declare a knowledge gate")
            if any(
                item.identity != canonical_requirement_identity(item.requirement)
                for item in self.completion_requirements
            ):
                raise ValueError(
                    "AD_HOC_DYNAMIC requirement identity must be derived from its semantics"
                )

        return self

    def canonical_semantics(self) -> dict[str, object]:
        """Return only authoritative, deterministic contract semantics."""

        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind.value,
            "scenario": self.scenario.model_dump(mode="json"),
            "predefined_objectives": [
                {"objective_key": item.objective_key}
                for item in sorted(self.predefined_objectives, key=lambda item: item.objective_key)
            ],
            "completion_requirements": [
                _formal_requirement_semantics(item)
                for item in sorted(self.completion_requirements, key=lambda item: item.identity)
            ],
            "planning_compatibility": _planning_compatibility_semantics(
                self.planning_compatibility
            ),
        }

    def canonical_json(self) -> str:
        """Serialize authoritative semantics with stable ordering."""

        return json.dumps(
            self.canonical_semantics(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def assert_bound_to(self, snapshot: ScenarioVersionSnapshot) -> None:
        """Fail closed if the contract is not bound to one exact snapshot."""

        _validate_scenario_snapshot(snapshot)
        if self.scenario.scenario_version_id != snapshot.id:
            raise FormalGoalError(
                "FORMAL_GOAL_SCENARIO_VERSION_MISMATCH",
                "Formal Goal is bound to a different ScenarioVersion",
            )
        if self.scenario.scenario_content_hash != snapshot.content_hash:
            raise FormalGoalError(
                "FORMAL_GOAL_SCENARIO_HASH_MISMATCH",
                "Formal Goal Scenario content hash does not match the exact version",
            )


class AdHocGoalRequirementCandidateV1(FormalGoalModel):
    """Provider-facing candidate semantics for an AD_HOC_DYNAMIC Goal.

    There is deliberately no ``key``, ``description``, ``knowledge_gate``, or
    prerequisite field.  Those are backend-owned or unsupported V1 concepts.
    """

    kind: ObjectiveRequirementKind
    node_key: StrictStr | None = None
    fact_key: StrictStr | None = None
    accepted_values: tuple[FormalGoalScalar, ...] = ()
    region_key: StrictStr | None = None
    resource_key: StrictStr | None = None
    minimum: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_candidate_shape(self) -> AdHocGoalRequirementCandidateV1:
        if self.kind == ObjectiveRequirementKind.FACT:
            if self.node_key is None or self.fact_key is None or not self.accepted_values:
                raise ValueError("Dynamic FACT candidate needs node/fact/accepted_values")
            if (
                self.region_key is not None
                or self.resource_key is not None
                or self.minimum is not None
            ):
                raise ValueError("Dynamic FACT candidate cannot declare resource fields")
        elif self.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
            if self.region_key is None or self.resource_key is None or self.minimum is None:
                raise ValueError(
                    "Dynamic RESOURCE_AT_LEAST candidate needs region/resource/minimum"
                )
            if self.node_key is not None or self.fact_key is not None or self.accepted_values:
                raise ValueError("Dynamic RESOURCE_AT_LEAST candidate cannot declare Fact fields")
        else:
            raise ValueError("Unsupported Dynamic Goal requirement kind")
        return self


class AdHocGoalCandidateSetV1(FormalGoalModel):
    requirements: tuple[AdHocGoalRequirementCandidateV1, ...] = Field(min_length=1)


def compile_predefined_formal_goal(
    snapshot: ScenarioVersionSnapshot,
    objectives: tuple[ObjectiveDefinitionV2, ...],
    *,
    compiler_version: str = "formal-goal-compiler@1",
) -> FormalGoalContractV1:
    """Compile an already normalized authored Objective set.

    The caller owns Objective resolver/subsumption normalization.  This
    compiler only canonicalizes ordering and proves every supplied definition
    is the exact immutable definition in the supplied ScenarioVersion.
    """

    _validate_scenario_snapshot(snapshot)
    if not objectives:
        raise FormalGoalError(
            "FORMAL_GOAL_OBJECTIVES_REQUIRED",
            "A PREDEFINED Formal Goal needs at least one Objective",
        )
    selected_keys = tuple(item.key for item in objectives)
    if len(set(selected_keys)) != len(selected_keys):
        raise FormalGoalError(
            "FORMAL_GOAL_OBJECTIVE_DUPLICATE",
            "PREDEFINED Formal Goal Objective keys must be unique",
        )
    catalog = snapshot.definition.objective_definitions
    for objective in objectives:
        exact = catalog.get(objective.key)
        if exact is None or exact != objective:
            raise FormalGoalError(
                "FORMAL_GOAL_OBJECTIVE_NOT_EXACT",
                "A PREDEFINED Objective is not the immutable definition in the exact Version",
            )

    requirements: list[FormalGoalRequirementV1] = []
    prerequisites: list[FormalGoalPlanningPrerequisiteV1] = []
    for objective in sorted(objectives, key=lambda item: item.key):
        for requirement in sorted(objective.completion_requirements, key=lambda item: item.key):
            requirements.append(
                FormalGoalRequirementV1(
                    identity=f"{objective.key}:{requirement.key}",
                    requirement=requirement,
                    source_objective_key=objective.key,
                    source_requirement_key=requirement.key,
                )
            )
        prerequisites.extend(
            FormalGoalPlanningPrerequisiteV1(objective_key=objective.key, prerequisite=item)
            for item in sorted(objective.prerequisites, key=lambda item: item.key)
        )

    return FormalGoalContractV1(
        source_kind=FormalGoalSourceKind.PREDEFINED,
        scenario=FormalGoalScenarioProofV1(
            scenario_version_id=snapshot.id,
            scenario_content_hash=snapshot.content_hash,
            scenario_schema_version=snapshot.schema_version,
        ),
        completion_requirements=tuple(requirements),
        predefined_objectives=tuple(
            FormalGoalObjectiveSourceV1(objective_key=key) for key in sorted(selected_keys)
        ),
        planning_compatibility=FormalGoalPlanningCompatibilityV1(
            prerequisites=tuple(
                sorted(
                    prerequisites,
                    key=lambda item: (item.objective_key, item.prerequisite.key),
                )
            )
        ),
        compiler_version=compiler_version,
    )


def compile_ad_hoc_dynamic_goal(
    snapshot: ScenarioVersionSnapshot,
    candidates: AdHocGoalCandidateSetV1 | tuple[AdHocGoalRequirementCandidateV1, ...],
    *,
    compiler_version: str = "formal-goal-interpreter@1",
) -> FormalGoalContractV1:
    """Validate public candidate semantics and build an AD_HOC contract."""

    _validate_scenario_snapshot(snapshot)
    candidate_set = (
        candidates
        if isinstance(candidates, AdHocGoalCandidateSetV1)
        else AdHocGoalCandidateSetV1(requirements=candidates)
    )
    requirements: list[FormalGoalRequirementV1] = []
    seen: set[str] = set()
    for candidate in candidate_set.requirements:
        requirement = _candidate_to_requirement(candidate, snapshot.definition)
        identity = canonical_requirement_identity(requirement)
        if identity in seen:
            raise FormalGoalError(
                "FORMAL_GOAL_REQUIREMENT_DUPLICATE",
                "Dynamic Goal requirements must have unique canonical semantics",
            )
        seen.add(identity)
        requirements.append(FormalGoalRequirementV1(identity=identity, requirement=requirement))
    return FormalGoalContractV1(
        source_kind=FormalGoalSourceKind.AD_HOC_DYNAMIC,
        scenario=FormalGoalScenarioProofV1(
            scenario_version_id=snapshot.id,
            scenario_content_hash=snapshot.content_hash,
            scenario_schema_version=snapshot.schema_version,
        ),
        completion_requirements=tuple(sorted(requirements, key=lambda item: item.identity)),
        compiler_version=compiler_version,
    )


def canonical_requirement_identity(requirement: ObjectiveRequirementV2) -> str:
    """Return a backend-owned identity derived from typed semantics."""

    semantic = _objective_requirement_semantics(requirement)
    digest = hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()[:16]
    if requirement.kind == ObjectiveRequirementKind.FACT:
        assert requirement.node_key is not None and requirement.fact_key is not None
        return f"fact/{requirement.node_key}/{requirement.fact_key}/{digest}"
    assert requirement.region_key is not None and requirement.resource_key is not None
    assert requirement.minimum is not None
    return (
        f"resource_at_least/{requirement.region_key}/{requirement.resource_key}/"
        f"{requirement.minimum}/{digest}"
    )


def _candidate_to_requirement(
    candidate: AdHocGoalRequirementCandidateV1,
    definition: ScenarioDefinitionV2,
) -> ObjectiveRequirementV2:
    if candidate.kind == ObjectiveRequirementKind.FACT:
        assert candidate.node_key is not None and candidate.fact_key is not None
        node = definition.world.node(candidate.node_key)
        if node is None:
            raise FormalGoalError(
                "FORMAL_GOAL_UNKNOWN_NODE",
                f"Dynamic Goal references unknown Node {candidate.node_key}",
            )
        fact = node.fact(candidate.fact_key)
        if fact is None:
            raise FormalGoalError(
                "FORMAL_GOAL_UNKNOWN_FACT",
                f"Dynamic Goal references unknown Fact {candidate.node_key}.{candidate.fact_key}",
            )
        accepted_values = _canonical_scalars(candidate.accepted_values)
        _validate_scalar_domain(fact, accepted_values)
        return ObjectiveRequirementV2(
            key="dynamic_requirement",
            node_key=candidate.node_key,
            fact_key=candidate.fact_key,
            accepted_values=accepted_values,
            description=f"{candidate.node_key}.{candidate.fact_key} has the requested value.",
        )

    assert candidate.region_key is not None
    assert candidate.resource_key is not None and candidate.minimum is not None
    locality = definition.metadata.locality
    region = definition.world.node(candidate.region_key)
    if region is None:
        raise FormalGoalError(
            "FORMAL_GOAL_UNKNOWN_REGION",
            f"Dynamic Goal references unknown Region {candidate.region_key}",
        )
    if not locality.enabled or region.node_type_key != locality.region_node_type_key:
        raise FormalGoalError(
            "FORMAL_GOAL_INVALID_REGION",
            f"Dynamic Goal resource scope is not a Scenario Region: {candidate.region_key}",
        )
    if not any(item.key == candidate.resource_key for item in definition.world.resources):
        raise FormalGoalError(
            "FORMAL_GOAL_UNKNOWN_RESOURCE",
            f"Dynamic Goal references unknown Resource {candidate.resource_key}",
        )
    return ObjectiveRequirementV2(
        key="dynamic_requirement",
        kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
        region_key=candidate.region_key,
        resource_key=candidate.resource_key,
        minimum=candidate.minimum,
        description=(
            f"{candidate.resource_key} in {candidate.region_key} reaches at least "
            f"{candidate.minimum}."
        ),
    )


def _validate_scenario_snapshot(snapshot: ScenarioVersionSnapshot) -> None:
    if snapshot.schema_version != 2 or snapshot.definition.schema_version != 2:
        raise FormalGoalError(
            "FORMAL_GOAL_SCENARIO_SCHEMA_UNSUPPORTED",
            "Formal Goal V1 requires an exact Scenario schema v2 snapshot",
        )
    try:
        expected_hash = scenario_content_hash(snapshot.definition.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise FormalGoalError(
            "FORMAL_GOAL_SCENARIO_INVALID",
            "The exact ScenarioVersion definition cannot be canonicalized",
        ) from exc
    if expected_hash != snapshot.content_hash:
        raise FormalGoalError(
            "FORMAL_GOAL_SCENARIO_HASH_MISMATCH",
            "The exact ScenarioVersion proof does not match its immutable definition",
        )


def _validate_scalar_domain(fact, values: tuple[FormalGoalScalar, ...]) -> None:  # type: ignore[no-untyped-def]
    for value in values:
        if fact.value_type.value == "STRING" and not isinstance(value, str):
            raise FormalGoalError(
                "FORMAL_GOAL_VALUE_TYPE_INVALID",
                "Dynamic Goal value does not match the STRING Fact domain",
            )
        if fact.value_type.value == "INTEGER" and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise FormalGoalError(
                "FORMAL_GOAL_VALUE_TYPE_INVALID",
                "Dynamic Goal value does not match the INTEGER Fact domain",
            )
        if fact.value_type.value == "BOOLEAN" and not isinstance(value, bool):
            raise FormalGoalError(
                "FORMAL_GOAL_VALUE_TYPE_INVALID",
                "Dynamic Goal value does not match the BOOLEAN Fact domain",
            )
        if fact.value_type.value == "ENUM" and value not in fact.allowed_values:
            raise FormalGoalError(
                "FORMAL_GOAL_VALUE_OUTSIDE_DOMAIN",
                "Dynamic Goal value is outside the ENUM Fact domain",
            )


def _formal_requirement_semantics(item: FormalGoalRequirementV1) -> dict[str, object]:
    return {
        "identity": item.identity,
        "requirement": _objective_requirement_semantics(item.requirement),
        **(
            {"source_objective_key": item.source_objective_key}
            if item.source_objective_key is not None
            else {}
        ),
        **(
            {"source_requirement_key": item.source_requirement_key}
            if item.source_requirement_key is not None
            else {}
        ),
    }


def _objective_requirement_semantics(requirement: ObjectiveRequirementV2) -> dict[str, object]:
    payload: dict[str, object] = {"kind": requirement.kind.value}
    if requirement.kind == ObjectiveRequirementKind.FACT:
        assert requirement.node_key is not None and requirement.fact_key is not None
        payload.update(
            {
                "node_key": requirement.node_key,
                "fact_key": requirement.fact_key,
                "accepted_values": list(_canonical_scalars(requirement.accepted_values)),
            }
        )
    else:
        assert requirement.region_key is not None
        assert requirement.resource_key is not None and requirement.minimum is not None
        payload.update(
            {
                "region_key": requirement.region_key,
                "resource_key": requirement.resource_key,
                "minimum": requirement.minimum,
            }
        )
    if requirement.knowledge_gate is not None:
        payload["knowledge_gate"] = _gate_semantics(requirement.knowledge_gate)
    return payload


def _planning_compatibility_semantics(
    compatibility: FormalGoalPlanningCompatibilityV1,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in sorted(
        compatibility.prerequisites,
        key=lambda entry: (entry.objective_key, entry.prerequisite.key),
    ):
        prerequisite = item.prerequisite
        result.append(
            {
                "objective_key": item.objective_key,
                "key": prerequisite.key,
                "requirements": [
                    _objective_requirement_semantics(requirement)
                    for requirement in sorted(
                        prerequisite.requirements, key=lambda value: value.key
                    )
                ],
            }
        )
    return result


def _gate_semantics(gate: ObjectiveRequirementKnowledgeGateV2) -> dict[str, object]:
    return {
        "node_key": gate.node_key,
        "fact_key": gate.fact_key,
        "accepted_values": list(_canonical_scalars(gate.accepted_values)),
    }


def _canonical_scalars(values: tuple[FormalGoalScalar, ...]) -> tuple[FormalGoalScalar, ...]:
    unique: dict[str, FormalGoalScalar] = {}
    for value in values:
        key = _canonical_json({"type": type(value).__name__, "value": value})
        unique[key] = value
    return tuple(
        unique[key]
        for key in sorted(
            unique,
            key=lambda item: (
                json.loads(item)["type"],
                str(json.loads(item)["value"]),
            ),
        )
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "AdHocGoalCandidateSetV1",
    "AdHocGoalRequirementCandidateV1",
    "FormalGoalContractV1",
    "FormalGoalError",
    "FormalGoalObjectiveSourceV1",
    "FormalGoalPlanningCompatibilityV1",
    "FormalGoalPlanningPrerequisiteV1",
    "FormalGoalRequirementV1",
    "FormalGoalScenarioProofV1",
    "FormalGoalSourceKind",
    "canonical_requirement_identity",
    "compile_ad_hoc_dynamic_goal",
    "compile_predefined_formal_goal",
]
