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
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.domain.action_invocation import (
    ActionInvocationBinding,
    canonical_action_invocation_contract,
    canonical_action_parameters,
)
from app.domain.scenario import ScenarioVersionSnapshot
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionParameterType,
    FactDefinitionV2,
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

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


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


class FormalGoalActionCompletedRequirementV1(FormalGoalModel):
    """Frozen semantics for one task-owned successful Action invocation.

    This is intentionally separate from ``ObjectiveRequirementV2``.  A state
    requirement describes the current World State; this requirement is proved
    only by a matching successful ``WorldOperation`` owned by the Task.
    ``None`` for actor, target, or parameters means the corresponding part is
    unconstrained, not a wildcard value inserted into the contract.
    """

    kind: Literal["ACTION_COMPLETED"]
    action_key: StrictStr = Field(min_length=1, max_length=100)
    actor_key: StrictStr | None = Field(default=None, max_length=100)
    target_key: StrictStr | None = Field(default=None, max_length=160)
    binding_constraints: tuple[ActionInvocationBinding, ...] = ()
    parameter_constraints: dict[str, JsonValue] | None = None
    match_mode: Literal["ONE_SUCCESSFUL_INVOCATION"] = "ONE_SUCCESSFUL_INVOCATION"
    boundary: Literal["TASK_OWNED_OPERATION"] = "TASK_OWNED_OPERATION"

    @model_validator(mode="after")
    def validate_bindings(self) -> FormalGoalActionCompletedRequirementV1:
        roles = tuple(item.role for item in self.binding_constraints)
        if len(set(roles)) != len(roles):
            raise ValueError("Action completion binding roles must be unique")
        ordered = tuple(sorted(self.binding_constraints, key=lambda item: item.role))
        if ordered != self.binding_constraints:
            object.__setattr__(self, "binding_constraints", ordered)
        return self


class AdHocActionCompletedRequirementCandidateV1(FormalGoalModel):
    """Provider-facing, lossless ACTION_COMPLETED candidate semantics."""

    kind: Literal["ACTION_COMPLETED"]
    action_key: StrictStr = Field(min_length=1, max_length=100)
    actor_key: StrictStr | None = Field(default=None, max_length=100)
    target_key: StrictStr | None = Field(default=None, max_length=160)
    binding_constraints: tuple[ActionInvocationBinding, ...] = ()
    parameter_constraints: dict[str, JsonValue] | None = None
    match_mode: Literal["ONE_SUCCESSFUL_INVOCATION"] = "ONE_SUCCESSFUL_INVOCATION"
    boundary: Literal["TASK_OWNED_OPERATION"] = "TASK_OWNED_OPERATION"

    @model_validator(mode="after")
    def validate_bindings(self) -> AdHocActionCompletedRequirementCandidateV1:
        roles = tuple(item.role for item in self.binding_constraints)
        if len(set(roles)) != len(roles):
            raise ValueError("Action completion binding roles must be unique")
        ordered = tuple(sorted(self.binding_constraints, key=lambda item: item.role))
        if ordered != self.binding_constraints:
            object.__setattr__(self, "binding_constraints", ordered)
        return self


class AdHocFactRequirementCandidateV1(FormalGoalModel):
    """Strict provider-facing FACT candidate semantics."""

    kind: Literal["FACT"]
    node_key: StrictStr = Field(min_length=1, max_length=160)
    fact_key: StrictStr = Field(min_length=1, max_length=160)
    accepted_values: tuple[FormalGoalScalar, ...] = Field(min_length=1)


class AdHocResourceAtLeastRequirementCandidateV1(FormalGoalModel):
    """Strict provider-facing RESOURCE_AT_LEAST candidate semantics."""

    kind: Literal["RESOURCE_AT_LEAST"]
    region_key: StrictStr = Field(min_length=1, max_length=160)
    resource_key: StrictStr = Field(min_length=1, max_length=160)
    minimum: StrictInt = Field(ge=0)


class AdHocDerivedStateRequirementCandidateV1(FormalGoalModel):
    """Strict provider-facing DERIVED_STATE candidate semantics."""

    kind: Literal["DERIVED_STATE"]
    derived_key: StrictStr = Field(min_length=1, max_length=160)
    accepted_values: tuple[FormalGoalScalar, ...] = Field(min_length=1)


type AdHocGoalRequirementCandidateV1 = Annotated[
    AdHocFactRequirementCandidateV1
    | AdHocResourceAtLeastRequirementCandidateV1
    | AdHocDerivedStateRequirementCandidateV1,
    Field(discriminator="kind"),
]


type AdHocGoalRequirementCandidateV2 = Annotated[
    AdHocFactRequirementCandidateV1
    | AdHocResourceAtLeastRequirementCandidateV1
    | AdHocDerivedStateRequirementCandidateV1
    | AdHocActionCompletedRequirementCandidateV1,
    Field(discriminator="kind"),
]


class AdHocGoalCandidateSetV1(FormalGoalModel):
    requirements: tuple[AdHocGoalRequirementCandidateV1, ...] = Field(min_length=1)


class AdHocGoalCandidateSetV2(FormalGoalModel):
    requirements: tuple[AdHocGoalRequirementCandidateV2, ...] = Field(min_length=1)


class FormalGoalRequirementV2(FormalGoalModel):
    """One V2 contract identity around a state or operation requirement."""

    identity: FormalGoalIdentity
    requirement: ObjectiveRequirementV2 | FormalGoalActionCompletedRequirementV1
    source_objective_key: StrictStr | None = Field(default=None, max_length=80)
    source_requirement_key: StrictStr | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_provenance(self) -> FormalGoalRequirementV2:
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


class FormalGoalContractV2(FormalGoalModel):
    """Additive Formal Goal contract supporting exact operation completion.

    V1 remains the persisted contract for existing STATE Goals.  V2 is used
    for the first operation-backed vocabulary while retaining the same
    Scenario proof, immutable hash, and task-owned completion boundary.
    """

    schema_version: Literal[2] = 2
    source_kind: FormalGoalSourceKind
    scenario: FormalGoalScenarioProofV1
    completion_requirements: tuple[FormalGoalRequirementV2, ...] = Field(min_length=1)
    predefined_objectives: tuple[FormalGoalObjectiveSourceV1, ...] = ()
    planning_compatibility: FormalGoalPlanningCompatibilityV1 = Field(
        default_factory=FormalGoalPlanningCompatibilityV1
    )
    compiler_version: StrictStr = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_contract(self) -> FormalGoalContractV2:
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
            operation_count = sum(
                isinstance(item.requirement, FormalGoalActionCompletedRequirementV1)
                for item in self.completion_requirements
            )
            if operation_count > 1:
                raise ValueError(
                    "A Phase-1 Formal Goal supports at most one ACTION_COMPLETED requirement"
                )
            if objective_keys:
                raise ValueError("AD_HOC_DYNAMIC cannot carry authored Objective provenance")
            if self.planning_compatibility.prerequisites:
                raise ValueError("AD_HOC_DYNAMIC cannot inject authored prerequisites")
            if any(
                isinstance(item.requirement, ObjectiveRequirementV2)
                and item.requirement.knowledge_gate is not None
                for item in self.completion_requirements
            ):
                raise ValueError("AD_HOC_DYNAMIC cannot declare a knowledge gate")
            for item in self.completion_requirements:
                expected_identity = (
                    canonical_requirement_identity(item.requirement)
                    if isinstance(item.requirement, ObjectiveRequirementV2)
                    else canonical_action_completed_identity(item.requirement)
                )
                if item.identity != expected_identity:
                    raise ValueError(
                        "AD_HOC_DYNAMIC requirement identity must be derived from its semantics"
                    )
        return self

    def canonical_semantics(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind.value,
            "scenario": self.scenario.model_dump(mode="json"),
            "predefined_objectives": [
                {"objective_key": item.objective_key}
                for item in sorted(self.predefined_objectives, key=lambda item: item.objective_key)
            ],
            "completion_requirements": [
                _formal_requirement_semantics_v2(item)
                for item in sorted(self.completion_requirements, key=lambda item: item.identity)
            ],
            "planning_compatibility": _planning_compatibility_semantics(
                self.planning_compatibility
            ),
        }

    def canonical_json(self) -> str:
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


type FormalGoalContract = FormalGoalContractV1 | FormalGoalContractV2


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
    typed_requirements = validate_ad_hoc_dynamic_candidates(
        snapshot.definition,
        candidate_set,
    )
    requirements = [
        FormalGoalRequirementV1(
            identity=canonical_requirement_identity(requirement),
            requirement=requirement,
        )
        for requirement in typed_requirements
    ]
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


def compile_ad_hoc_dynamic_goal_v2(
    snapshot: ScenarioVersionSnapshot,
    candidates: AdHocGoalCandidateSetV2 | tuple[AdHocGoalRequirementCandidateV2, ...],
    *,
    compiler_version: str = "formal-goal-interpreter@2",
) -> FormalGoalContractV2:
    """Compile a dynamic Goal containing state and/or operation requirements."""

    _validate_scenario_snapshot(snapshot)
    candidate_set = (
        candidates
        if isinstance(candidates, AdHocGoalCandidateSetV2)
        else AdHocGoalCandidateSetV2(requirements=candidates)
    )
    typed_requirements = validate_ad_hoc_dynamic_candidates_v2(
        snapshot.definition,
        candidate_set,
    )
    requirements = [
        FormalGoalRequirementV2(
            identity=(
                canonical_requirement_identity(requirement)
                if isinstance(requirement, ObjectiveRequirementV2)
                else canonical_action_completed_identity(requirement)
            ),
            requirement=requirement,
        )
        for requirement in typed_requirements
    ]
    return FormalGoalContractV2(
        source_kind=FormalGoalSourceKind.AD_HOC_DYNAMIC,
        scenario=FormalGoalScenarioProofV1(
            scenario_version_id=snapshot.id,
            scenario_content_hash=snapshot.content_hash,
            scenario_schema_version=snapshot.schema_version,
        ),
        completion_requirements=tuple(sorted(requirements, key=lambda item: item.identity)),
        compiler_version=compiler_version,
    )


def validate_ad_hoc_dynamic_candidates_v2(
    definition: ScenarioDefinitionV2,
    candidates: AdHocGoalCandidateSetV2 | tuple[AdHocGoalRequirementCandidateV2, ...],
) -> tuple[ObjectiveRequirementV2 | FormalGoalActionCompletedRequirementV1, ...]:
    """Validate state and exact operation candidates against one Version."""

    candidate_set = (
        candidates
        if isinstance(candidates, AdHocGoalCandidateSetV2)
        else AdHocGoalCandidateSetV2(requirements=candidates)
    )
    canonicalized = canonicalize_ad_hoc_dynamic_candidates_v2(definition, candidate_set)
    state_candidates = tuple(
        item
        for item in canonicalized.requirements
        if not isinstance(item, AdHocActionCompletedRequirementCandidateV1)
    )
    state_by_identity: dict[str, ObjectiveRequirementV2] = {}
    if state_candidates:
        state_requirements = validate_ad_hoc_dynamic_candidates(
            definition,
            AdHocGoalCandidateSetV1(requirements=state_candidates),
        )
        state_by_identity = {
            canonical_requirement_identity(item): item for item in state_requirements
        }

    requirements: list[ObjectiveRequirementV2 | FormalGoalActionCompletedRequirementV1] = []
    seen: set[str] = set()
    for candidate in canonicalized.requirements:
        requirement: ObjectiveRequirementV2 | FormalGoalActionCompletedRequirementV1
        if isinstance(candidate, AdHocActionCompletedRequirementCandidateV1):
            requirement = _action_completed_candidate_to_requirement(candidate, definition)
            identity = canonical_action_completed_identity(requirement)
        else:
            # The state subset was deterministically validated above, so this
            # lookup cannot be absent unless a future candidate kind is added
            # without updating this adapter.
            state_requirement = _candidate_state_requirement(candidate, definition)
            identity = canonical_requirement_identity(state_requirement)
            requirement = state_by_identity[identity]
        if identity in seen:
            raise FormalGoalError(
                "FORMAL_GOAL_REQUIREMENT_DUPLICATE",
                "Dynamic Goal requirements must have unique canonical semantics",
            )
        seen.add(identity)
        requirements.append(requirement)
    return tuple(requirements)


def canonicalize_ad_hoc_dynamic_candidates_v2(
    definition: ScenarioDefinitionV2,
    candidates: AdHocGoalCandidateSetV2 | tuple[AdHocGoalRequirementCandidateV2, ...],
) -> AdHocGoalCandidateSetV2:
    """Apply lossless state normalization and Action-schema normalization."""

    candidate_set = (
        candidates
        if isinstance(candidates, AdHocGoalCandidateSetV2)
        else AdHocGoalCandidateSetV2(requirements=candidates)
    )
    state_candidates = tuple(
        item
        for item in candidate_set.requirements
        if not isinstance(item, AdHocActionCompletedRequirementCandidateV1)
    )
    normalized_states: list[AdHocGoalRequirementCandidateV1] = []
    if state_candidates:
        normalized_state_set = canonicalize_ad_hoc_dynamic_candidates(
            definition,
            AdHocGoalCandidateSetV1(requirements=state_candidates),
        )
        normalized_states.extend(normalized_state_set.requirements)

    normalized: list[AdHocGoalRequirementCandidateV2] = []
    state_index = 0
    for candidate in candidate_set.requirements:
        if isinstance(candidate, AdHocActionCompletedRequirementCandidateV1):
            _validate_action_completed_candidate(candidate, definition)
            parameters = (
                None
                if candidate.parameter_constraints is None
                else _canonical_action_parameters_for_goal(
                    definition,
                    candidate.action_key,
                    candidate.parameter_constraints,
                )
            )
            normalized.append(candidate.model_copy(update={"parameter_constraints": parameters}))
            continue
        normalized.append(normalized_states[state_index])
        state_index += 1
    return AdHocGoalCandidateSetV2(requirements=tuple(normalized))


def canonical_action_completed_identity(
    requirement: FormalGoalActionCompletedRequirementV1,
) -> str:
    """Return a stable identity for one exact operation-match contract."""

    digest = hashlib.sha256(
        _canonical_json(_action_completed_semantics(requirement)).encode("utf-8")
    ).hexdigest()[:16]
    return f"action_completed/{requirement.action_key}/{digest}"


def _candidate_identity(
    candidate: AdHocGoalRequirementCandidateV1,
    definition: ScenarioDefinitionV2,
) -> str:
    return canonical_requirement_identity(_candidate_to_requirement(candidate, definition))


def _candidate_state_requirement(
    candidate: AdHocGoalRequirementCandidateV1,
    definition: ScenarioDefinitionV2,
) -> ObjectiveRequirementV2:
    return _candidate_to_requirement(candidate, definition)


def _action_completed_candidate_to_requirement(
    candidate: AdHocActionCompletedRequirementCandidateV1,
    definition: ScenarioDefinitionV2,
) -> FormalGoalActionCompletedRequirementV1:
    _validate_action_completed_candidate(candidate, definition)
    parameters = (
        None
        if candidate.parameter_constraints is None
        else _canonical_action_parameters_for_goal(
            definition,
            candidate.action_key,
            candidate.parameter_constraints,
        )
    )
    return FormalGoalActionCompletedRequirementV1(
        kind="ACTION_COMPLETED",
        action_key=candidate.action_key,
        actor_key=candidate.actor_key,
        target_key=candidate.target_key,
        binding_constraints=candidate.binding_constraints,
        parameter_constraints=parameters,
        match_mode=candidate.match_mode,
        boundary=candidate.boundary,
    )


def _validate_action_completed_candidate(
    candidate: AdHocActionCompletedRequirementCandidateV1,
    definition: ScenarioDefinitionV2,
) -> ActionDefinitionV2:
    action = next((item for item in definition.actions if item.key == candidate.action_key), None)
    if action is None:
        raise FormalGoalError(
            "FORMAL_GOAL_UNKNOWN_ACTION",
            f"Dynamic Goal references unknown Action {candidate.action_key}",
        )
    invocation_contract = canonical_action_invocation_contract(action)

    if candidate.actor_key is not None:
        actor = next(
            (item for item in definition.actors.actor_profiles if item.key == candidate.actor_key),
            None,
        )
        if actor is None:
            raise FormalGoalError(
                "FORMAL_GOAL_UNKNOWN_ACTOR",
                f"Dynamic Goal references unknown Actor {candidate.actor_key}",
            )

    if candidate.target_key is not None:
        if not candidate.target_key:
            raise FormalGoalError(
                "FORMAL_GOAL_TARGET_INVALID",
                "ACTION_COMPLETED target_key cannot be blank",
            )
        target_spec = invocation_contract.get("target")
        if not isinstance(target_spec, Mapping) or not _invocation_reference_is_valid(
            definition,
            str(target_spec.get("semantic_reference_type")),
            candidate.target_key,
            node_type_keys=target_spec.get("node_type_keys"),
        ):
            raise FormalGoalError(
                "FORMAL_GOAL_UNKNOWN_TARGET",
                f"Dynamic Goal references incompatible target {candidate.target_key}",
            )

    if candidate.binding_constraints:
        raw_bindings = invocation_contract.get("bindings")
        binding_specs = raw_bindings if isinstance(raw_bindings, (list, tuple)) else ()
        specs_by_role = {
            str(item["slot_key"]): item
            for item in binding_specs
            if isinstance(item, Mapping) and isinstance(item.get("slot_key"), str)
        }
        if not specs_by_role:
            raise FormalGoalError(
                "FORMAL_GOAL_ACTION_BINDING_UNSUPPORTED",
                "This Action does not declare public operation binding roles",
            )
        for binding in candidate.binding_constraints:
            spec = specs_by_role.get(binding.role)
            if spec is None:
                raise FormalGoalError(
                    "FORMAL_GOAL_ACTION_BINDING_INVALID",
                    f"Unsupported Action binding role {binding.role}",
                )
            if not isinstance(binding.value, str) or not binding.value:
                raise FormalGoalError(
                    "FORMAL_GOAL_ACTION_BINDING_INVALID",
                    "Reference-valued Action bindings must name a public identity",
                )
            semantic_type = spec.get("semantic_reference_type")
            if not _invocation_reference_is_valid(
                definition,
                str(semantic_type),
                binding.value,
            ):
                raise FormalGoalError(
                    "FORMAL_GOAL_ACTION_BINDING_INVALID",
                    "Action binding value does not match its declared semantic type",
                )

    if candidate.parameter_constraints is not None:
        _canonical_action_parameters_for_goal(
            definition,
            candidate.action_key,
            candidate.parameter_constraints,
        )
    return action


def _invocation_reference_is_valid(
    definition: ScenarioDefinitionV2,
    semantic_type: str,
    value: object,
    *,
    node_type_keys: object = (),
) -> bool:
    """Validate one canonical identity from the compiled invocation contract."""

    if not isinstance(value, str) or not value:
        return False
    if semantic_type in {"NODE", "REGION", "FACILITY"}:
        node = definition.world.node(value)
        if node is None:
            return False
        if semantic_type == "REGION":
            return node.node_type_key == definition.metadata.locality.region_node_type_key
        if semantic_type == "FACILITY":
            return node.node_type_key == definition.metadata.locality.facility_node_type_key
        declared_types = (
            {str(item) for item in node_type_keys}
            if isinstance(node_type_keys, (list, tuple, set))
            else set()
        )
        return not declared_types or node.node_type_key in declared_types
    if semantic_type == "ACTOR":
        return any(item.key == value for item in definition.actors.actor_profiles)
    if semantic_type == "RESOURCE":
        return any(item.key == value for item in definition.world.resources)
    return False


def _canonical_action_parameters_for_goal(
    definition: ScenarioDefinitionV2,
    action_key: str,
    parameters: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    action = next((item for item in definition.actions if item.key == action_key), None)
    if action is None:
        raise FormalGoalError(
            "FORMAL_GOAL_UNKNOWN_ACTION",
            f"Dynamic Goal references unknown Action {action_key}",
        )
    try:
        definitions = {item.key: item for item in action.parameters}
        invocation_contract = canonical_action_invocation_contract(action)
        raw_parameter_specs = invocation_contract.get("parameters")
        parameter_specs = (
            {
                str(item["slot_key"]): item
                for item in raw_parameter_specs
                if isinstance(item, Mapping) and isinstance(item.get("slot_key"), str)
            }
            if isinstance(raw_parameter_specs, (list, tuple))
            else {}
        )
        if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
            normalized = cast(
                dict[str, JsonValue],
                canonical_action_parameters(action, parameters),
            )
            resources = normalized.get("resources")
            resource_spec = next(
                (
                    item
                    for item in parameter_specs.values()
                    if item.get("semantic_reference_type") == "RESOURCE"
                ),
                None,
            )
            if not isinstance(resources, list) or resource_spec is None:
                raise ValueError("Transport parameters lack canonical Resource entries")
            if any(
                not isinstance(item, dict)
                or not _invocation_reference_is_valid(
                    definition,
                    str(resource_spec["semantic_reference_type"]),
                    item.get("resource_key"),
                )
                for item in resources
            ):
                raise ValueError("Transport Resource identity is invalid")
            return normalized
        if set(parameters) - set(definitions):
            raise ValueError("Unknown Action parameter constraint")
        for key, value in parameters.items():
            parameter = definitions[key]
            valid = (
                (
                    parameter.value_type == ActionParameterType.INTEGER
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                )
                or (parameter.value_type == ActionParameterType.BOOLEAN and isinstance(value, bool))
                or (
                    parameter.value_type in {ActionParameterType.STRING, ActionParameterType.ENUM}
                    and isinstance(value, str)
                )
            )
            if not valid:
                raise ValueError("Action parameter constraint has an invalid type")
            spec = parameter_specs.get(key)
            semantic_type = spec.get("semantic_reference_type") if spec is not None else None
            if semantic_type is not None and not _invocation_reference_is_valid(
                definition,
                str(semantic_type),
                value,
            ):
                raise ValueError("Action parameter identity does not match its semantic type")
            if isinstance(value, int) and not isinstance(value, bool):
                if parameter.minimum is not None and value < parameter.minimum:
                    raise ValueError("Action parameter constraint is below minimum")
                if parameter.maximum is not None and value > parameter.maximum:
                    raise ValueError("Action parameter constraint is above maximum")
            if (
                parameter.value_type == ActionParameterType.ENUM
                and value not in parameter.allowed_values
            ):
                raise ValueError("Action parameter constraint is outside allowed values")
        normalized = dict(parameters)
    except (TypeError, ValueError) as exc:
        raise FormalGoalError(
            "FORMAL_GOAL_ACTION_PARAMETERS_INVALID",
            "ACTION_COMPLETED parameter constraints do not match the Action schema",
        ) from exc
    return cast(
        dict[str, JsonValue],
        {str(key): value for key, value in normalized.items()},
    )


def validate_ad_hoc_dynamic_candidates(
    definition: ScenarioDefinitionV2,
    candidates: AdHocGoalCandidateSetV1 | tuple[AdHocGoalRequirementCandidateV1, ...],
) -> tuple[ObjectiveRequirementV2, ...]:
    """Validate provider candidates against one exact Scenario definition.

    This is the reusable deterministic boundary used both while resolving a
    provider response and while freezing a Task.  It never accepts provider
    identities or authored planning semantics.
    """

    candidate_set = (
        candidates
        if isinstance(candidates, AdHocGoalCandidateSetV1)
        else AdHocGoalCandidateSetV1(requirements=candidates)
    )
    candidate_set = canonicalize_ad_hoc_dynamic_candidates(definition, candidate_set)
    requirements: list[ObjectiveRequirementV2] = []
    seen: set[str] = set()
    for candidate in candidate_set.requirements:
        requirement = _candidate_to_requirement(candidate, definition)
        identity = canonical_requirement_identity(requirement)
        if identity in seen:
            raise FormalGoalError(
                "FORMAL_GOAL_REQUIREMENT_DUPLICATE",
                "Dynamic Goal requirements must have unique canonical semantics",
            )
        seen.add(identity)
        requirements.append(requirement)
    return tuple(requirements)


def canonicalize_ad_hoc_dynamic_candidates(
    definition: ScenarioDefinitionV2,
    candidates: AdHocGoalCandidateSetV1 | tuple[AdHocGoalRequirementCandidateV1, ...],
) -> AdHocGoalCandidateSetV1:
    """Apply only lossless, fact-schema-driven normalization to provider values.

    The provider DTO intentionally accepts scalar JSON values without knowing
    the referenced Fact schema.  This boundary adds the exact Fact type before
    the existing deterministic Formal Goal validation runs.  It never chooses
    a value or infers Goal semantics; it only converts unambiguous serialized
    forms such as ``"true"`` for a BOOLEAN Fact or ``"20"`` for an INTEGER
    Fact.
    """

    candidate_set = (
        candidates
        if isinstance(candidates, AdHocGoalCandidateSetV1)
        else AdHocGoalCandidateSetV1(requirements=candidates)
    )
    normalized: list[AdHocGoalRequirementCandidateV1] = []
    for candidate in candidate_set.requirements:
        if not isinstance(candidate, AdHocFactRequirementCandidateV1):
            normalized.append(candidate)
            continue
        fact = _fact_for_dynamic_candidate(candidate, definition)
        values = tuple(_canonicalize_fact_value(fact, value) for value in candidate.accepted_values)
        normalized.append(candidate.model_copy(update={"accepted_values": values}))
    return AdHocGoalCandidateSetV1(requirements=tuple(normalized))


def canonical_requirement_identity(requirement: ObjectiveRequirementV2) -> str:
    """Return a backend-owned identity derived from typed semantics."""

    semantic = _objective_requirement_semantics(requirement)
    digest = hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()[:16]
    if requirement.kind == ObjectiveRequirementKind.FACT:
        assert requirement.node_key is not None and requirement.fact_key is not None
        return f"fact/{requirement.node_key}/{requirement.fact_key}/{digest}"
    if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
        assert requirement.derived_key is not None
        return f"derived/{requirement.derived_key}/{digest}"
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
    if isinstance(candidate, AdHocFactRequirementCandidateV1):
        assert candidate.node_key is not None and candidate.fact_key is not None
        fact = _fact_for_dynamic_candidate(candidate, definition)
        accepted_values = _canonical_scalars(candidate.accepted_values)
        _validate_scalar_domain(fact, accepted_values)
        return ObjectiveRequirementV2(
            key="dynamic_requirement",
            node_key=candidate.node_key,
            fact_key=candidate.fact_key,
            accepted_values=accepted_values,
            description=f"{candidate.node_key}.{candidate.fact_key} has the requested value.",
        )

    if isinstance(candidate, AdHocDerivedStateRequirementCandidateV1):
        assert candidate.derived_key is not None
        state = definition.derived_state_definitions.get(candidate.derived_key)
        if state is None:
            raise FormalGoalError(
                "FORMAL_GOAL_UNKNOWN_DERIVED_STATE",
                f"Dynamic Goal references unknown Derived State {candidate.derived_key}",
            )
        if not state.goal_addressable:
            raise FormalGoalError(
                "FORMAL_GOAL_DERIVED_STATE_NOT_PUBLIC",
                f"Dynamic Goal cannot address Derived State {candidate.derived_key}",
            )
        accepted_values = _canonical_scalars(candidate.accepted_values)
        _validate_typed_values(
            state.value_type.value,
            state.allowed_values,
            accepted_values,
            state.key,
        )
        return ObjectiveRequirementV2(
            key="dynamic_requirement",
            kind=ObjectiveRequirementKind.DERIVED_STATE,
            derived_key=state.key,
            accepted_values=accepted_values,
            description=f"{state.name} reaches the requested state.",
        )

    assert isinstance(candidate, AdHocResourceAtLeastRequirementCandidateV1)
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


def _fact_for_dynamic_candidate(
    candidate: AdHocFactRequirementCandidateV1,
    definition: ScenarioDefinitionV2,
) -> FactDefinitionV2:
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
    return fact


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
    verified_hashes = snapshot.verified_content_hashes or (expected_hash,)
    if expected_hash not in verified_hashes or snapshot.content_hash not in verified_hashes:
        raise FormalGoalError(
            "FORMAL_GOAL_SCENARIO_HASH_MISMATCH",
            "The exact ScenarioVersion proof does not match its immutable definition",
        )


_STRICT_INTEGER_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


def _canonicalize_fact_value(
    fact: FactDefinitionV2,
    value: FormalGoalScalar,
) -> FormalGoalScalar:
    """Normalize one provider scalar only when its target type is unambiguous."""

    if fact.value_type.value == "BOOLEAN":
        if type(value) is bool:
            return value
        if type(value) is str and value in {"true", "false"}:
            return value == "true"
        raise _value_type_error(fact, value, "BOOLEAN")

    if fact.value_type.value == "INTEGER":
        if type(value) is int:
            return value
        if type(value) is str and _STRICT_INTEGER_TEXT.fullmatch(value):
            try:
                return int(value, 10)
            except ValueError as exc:
                raise _value_type_error(fact, value, "INTEGER") from exc
        raise _value_type_error(fact, value, "INTEGER")

    if fact.value_type.value == "STRING":
        if type(value) is str:
            return value
        raise _value_type_error(fact, value, "STRING")

    # ENUM values remain closed-domain values.  Do not coerce them because the
    # allowed value itself is the semantic authority and may be any scalar.
    if any(type(value) is type(allowed) for allowed in fact.allowed_values):
        return value
    raise _value_type_error(fact, value, "ENUM")


def _value_type_error(
    fact: FactDefinitionV2,
    value: FormalGoalScalar,
    expected_value_type: str,
) -> FormalGoalError:
    return FormalGoalError(
        "FORMAL_GOAL_VALUE_TYPE_INVALID",
        f"Dynamic Goal value does not match the {expected_value_type} Fact domain",
        details={
            "expected_value_type": fact.value_type.value,
            "actual_candidate_json_type": _json_value_type(value),
        },
    )


def _json_value_type(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


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
        if fact.value_type.value == "ENUM":
            if not any(type(value) is type(allowed) for allowed in fact.allowed_values):
                raise _value_type_error(fact, value, "ENUM")
            if value not in fact.allowed_values:
                raise FormalGoalError(
                    "FORMAL_GOAL_VALUE_OUTSIDE_DOMAIN",
                    "Dynamic Goal value is outside the ENUM Fact domain",
                )


def _validate_typed_values(
    value_type: str,
    allowed_values: tuple[FormalGoalScalar, ...],
    values: tuple[FormalGoalScalar, ...],
    derived_key: str,
) -> None:
    """Validate a dynamic Derived target against authored public metadata."""

    for value in values:
        if value_type == "BOOLEAN" and type(value) is not bool:
            raise FormalGoalError(
                "FORMAL_GOAL_VALUE_TYPE_INVALID",
                "Dynamic Goal value does not match the Derived State BOOLEAN domain",
                details={
                    "derived_key": derived_key,
                    "expected_value_type": value_type,
                    "actual_candidate_json_type": _json_value_type(value),
                },
            )
        if value_type == "INTEGER" and type(value) is not int:
            raise FormalGoalError(
                "FORMAL_GOAL_VALUE_TYPE_INVALID",
                "Dynamic Goal value does not match the Derived State INTEGER domain",
                details={
                    "derived_key": derived_key,
                    "expected_value_type": value_type,
                    "actual_candidate_json_type": _json_value_type(value),
                },
            )
        if value_type == "STRING" and type(value) is not str:
            raise FormalGoalError(
                "FORMAL_GOAL_VALUE_TYPE_INVALID",
                "Dynamic Goal value does not match the Derived State STRING domain",
                details={
                    "derived_key": derived_key,
                    "expected_value_type": value_type,
                    "actual_candidate_json_type": _json_value_type(value),
                },
            )
        if value_type == "ENUM":
            if not any(type(value) is type(allowed) for allowed in allowed_values):
                raise FormalGoalError(
                    "FORMAL_GOAL_VALUE_TYPE_INVALID",
                    "Dynamic Goal value does not match the Derived State ENUM domain",
                    details={
                        "derived_key": derived_key,
                        "expected_value_type": value_type,
                        "actual_candidate_json_type": _json_value_type(value),
                    },
                )
            if value not in allowed_values:
                raise FormalGoalError(
                    "FORMAL_GOAL_VALUE_OUTSIDE_DOMAIN",
                    "Dynamic Goal value is outside the Derived State ENUM domain",
                    details={"derived_key": derived_key},
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


def _formal_requirement_semantics_v2(item: FormalGoalRequirementV2) -> dict[str, object]:
    requirement = item.requirement
    if isinstance(requirement, ObjectiveRequirementV2):
        semantics = _objective_requirement_semantics(requirement)
    else:
        semantics = _action_completed_semantics(requirement)
    return {
        "identity": item.identity,
        "requirement": semantics,
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


def _action_completed_semantics(
    requirement: FormalGoalActionCompletedRequirementV1,
) -> dict[str, object]:
    return {
        "kind": requirement.kind,
        "action_key": requirement.action_key,
        **({"actor_key": requirement.actor_key} if requirement.actor_key is not None else {}),
        **({"target_key": requirement.target_key} if requirement.target_key is not None else {}),
        "binding_constraints": [
            item.model_dump(mode="json")
            for item in sorted(requirement.binding_constraints, key=lambda item: item.role)
        ],
        **(
            {
                "parameter_constraints": json.loads(
                    _canonical_json(requirement.parameter_constraints)
                )
            }
            if requirement.parameter_constraints is not None
            else {}
        ),
        "match_mode": requirement.match_mode,
        "boundary": requirement.boundary,
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
    elif requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
        assert requirement.region_key is not None
        assert requirement.resource_key is not None and requirement.minimum is not None
        payload.update(
            {
                "region_key": requirement.region_key,
                "resource_key": requirement.resource_key,
                "minimum": requirement.minimum,
            }
        )
    else:
        assert requirement.derived_key is not None
        payload.update(
            {
                "derived_key": requirement.derived_key,
                "accepted_values": list(_canonical_scalars(requirement.accepted_values)),
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
    "AdHocActionCompletedRequirementCandidateV1",
    "AdHocDerivedStateRequirementCandidateV1",
    "AdHocFactRequirementCandidateV1",
    "AdHocGoalCandidateSetV1",
    "AdHocGoalCandidateSetV2",
    "AdHocGoalRequirementCandidateV1",
    "AdHocGoalRequirementCandidateV2",
    "AdHocResourceAtLeastRequirementCandidateV1",
    "FormalGoalActionCompletedRequirementV1",
    "FormalGoalContract",
    "FormalGoalContractV1",
    "FormalGoalContractV2",
    "FormalGoalError",
    "FormalGoalObjectiveSourceV1",
    "FormalGoalPlanningCompatibilityV1",
    "FormalGoalPlanningPrerequisiteV1",
    "FormalGoalRequirementV1",
    "FormalGoalRequirementV2",
    "FormalGoalScenarioProofV1",
    "FormalGoalSourceKind",
    "canonical_action_completed_identity",
    "canonical_requirement_identity",
    "canonicalize_ad_hoc_dynamic_candidates",
    "canonicalize_ad_hoc_dynamic_candidates_v2",
    "compile_ad_hoc_dynamic_goal",
    "compile_ad_hoc_dynamic_goal_v2",
    "compile_predefined_formal_goal",
    "validate_ad_hoc_dynamic_candidates",
    "validate_ad_hoc_dynamic_candidates_v2",
]
