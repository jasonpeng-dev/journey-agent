"""Pure objective semantics for Starfire Command.

This catalog is intentionally not connected to the production task, planner, or
completion flow during Phase B0. It defines terminal states and public state
prerequisites only; it does not prescribe tools or plan steps.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType

from app.scenarios.contracts import (
    ObjectiveContractError,
    ObjectiveDefinition,
    ObjectiveKey,
    ObjectivePrerequisite,
    ObjectiveRequirementEvaluation,
    ObjectiveScope,
    ObjectiveVerificationRequirement,
    PerObjectiveEvaluation,
    ScenarioRuntimeState,
    ScopedObjectiveEvaluation,
)


class StarfireObjectiveKey(StrEnum):
    GATHER_VALLEY_INTELLIGENCE = "GATHER_VALLEY_INTELLIGENCE"
    SECURE_NORTHERN_VALLEY = "SECURE_NORTHERN_VALLEY"
    RESTORE_STARFIRE_OUTPOST = "RESTORE_STARFIRE_OUTPOST"
    OPEN_NORTHERN_TRADE_ROUTE = "OPEN_NORTHERN_TRADE_ROUTE"
    FULL_NORTHERN_RECOVERY = "FULL_NORTHERN_RECOVERY"


VALLEY_INTELLIGENCE_COMPLETE = ObjectiveVerificationRequirement(
    key="valley_intelligence_complete",
    node_key="northern_valley",
    fact_key="valley_intelligence",
    accepted_values=frozenset({"PARTIAL", "COMPLETE"}),
    description="The Northern Valley has actionable intelligence.",
)
VALLEY_SAFE = ObjectiveVerificationRequirement(
    key="northern_valley_safe",
    node_key="northern_valley",
    fact_key="valley_security",
    accepted_values=frozenset({"SAFE"}),
    description="The Northern Valley is safe.",
)
OUTPOST_OPERATIONAL = ObjectiveVerificationRequirement(
    key="starfire_outpost_operational",
    node_key="starfire_outpost",
    fact_key="outpost_status",
    accepted_values=frozenset({"OPERATIONAL", "RESTORED"}),
    description="Starfire Outpost is operational or fully restored.",
)
TRADE_ROUTE_OPEN = ObjectiveVerificationRequirement(
    key="northern_trade_route_open",
    node_key="northern_trade_route",
    fact_key="trade_route_status",
    accepted_values=frozenset({"OPEN"}),
    description="The Northern Trade Route is open.",
)
TRADE_SUPPORT_AVAILABLE = ObjectiveVerificationRequirement(
    key="northern_trade_support_available",
    node_key="north_village",
    fact_key="village_support",
    accepted_values=frozenset({"GUIDE", "SUPPLIES"}),
    description="The village provides guides or supplies for trade-route testing.",
)

VALLEY_SECURITY_PREREQUISITE = ObjectivePrerequisite(
    key="valley_security_required",
    description="Public world rules require a safe valley before outpost work can proceed.",
    requirements=(VALLEY_SAFE,),
)
OUTPOST_OPERATIONAL_PREREQUISITE = ObjectivePrerequisite(
    key="outpost_operation_required",
    description="Public world rules require an operational outpost before trade testing.",
    requirements=(OUTPOST_OPERATIONAL,),
)
TRADE_SUPPORT_PREREQUISITE = ObjectivePrerequisite(
    key="trade_support_required",
    description="Public world rules require guides or supplies before trade testing.",
    requirements=(TRADE_SUPPORT_AVAILABLE,),
)


_DEFINITIONS: Mapping[ObjectiveKey, ObjectiveDefinition] = MappingProxyType(
    {
        StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE.value: ObjectiveDefinition(
            key=StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE.value,
            name="Gather Northern Valley Intelligence",
            description="Obtain actionable intelligence about the Northern Valley.",
            completion_requirements=(VALLEY_INTELLIGENCE_COMPLETE,),
        ),
        StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value: ObjectiveDefinition(
            key=StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value,
            name="Secure Northern Valley",
            description="Reach a verified safe state in the Northern Valley.",
            completion_requirements=(VALLEY_SAFE,),
        ),
        StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value: ObjectiveDefinition(
            key=StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value,
            name="Restore Starfire Outpost",
            description="Make Starfire Outpost operational or fully restored.",
            completion_requirements=(OUTPOST_OPERATIONAL,),
            prerequisites=(VALLEY_SECURITY_PREREQUISITE,),
        ),
        StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value: ObjectiveDefinition(
            key=StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value,
            name="Open Northern Trade Route",
            description="Reach a verified open state for the Northern Trade Route.",
            completion_requirements=(TRADE_ROUTE_OPEN,),
            prerequisites=(
                VALLEY_SECURITY_PREREQUISITE,
                OUTPOST_OPERATIONAL_PREREQUISITE,
                TRADE_SUPPORT_PREREQUISITE,
            ),
        ),
        StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value: ObjectiveDefinition(
            key=StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value,
            name="Full Northern Recovery",
            description="Satisfy the legacy full Starfire recovery terminal state.",
            completion_requirements=(VALLEY_SAFE, OUTPOST_OPERATIONAL, TRADE_ROUTE_OPEN),
            prerequisites=(TRADE_SUPPORT_PREREQUISITE,),
            subsumes=frozenset(
                {
                    StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value,
                    StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value,
                    StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value,
                }
            ),
        ),
    }
)


class StarfireObjectiveCatalog:
    """Static Starfire objective catalog with no production orchestration behavior."""

    scenario_key = "starfire_command"
    catalog_version = "starfire-objectives-v1"
    definitions = _DEFINITIONS

    def scope(self, objective_keys: Iterable[ObjectiveKey]) -> ObjectiveScope:
        normalized = tuple(sorted(set(str(key) for key in objective_keys)))
        scope = ObjectiveScope(
            scenario_key=self.scenario_key,
            catalog_version=self.catalog_version,
            objective_keys=normalized,
        )
        self._validate_scope(scope)
        return scope

    def definition(self, objective_key: ObjectiveKey) -> ObjectiveDefinition:
        definition = self.definitions.get(str(objective_key))
        if definition is None:
            raise ObjectiveContractError(
                "OBJECTIVE_NOT_SUPPORTED",
                f"Objective {objective_key!s} is not supported by {self.scenario_key}",
            )
        return definition

    def evaluate(
        self,
        scope: ObjectiveScope,
        state: ScenarioRuntimeState,
    ) -> ScopedObjectiveEvaluation:
        self._validate_scope(scope)
        objectives = tuple(
            PerObjectiveEvaluation(
                objective_key=objective_key,
                requirements=tuple(
                    self._evaluate_requirement(requirement, state)
                    for requirement in self.definition(objective_key).completion_requirements
                ),
            )
            for objective_key in scope.objective_keys
        )
        return ScopedObjectiveEvaluation(scope=scope, objectives=objectives)

    def verification_requirements(
        self,
        scope: ObjectiveScope,
    ) -> tuple[ObjectiveVerificationRequirement, ...]:
        self._validate_scope(scope)
        requirements: dict[str, ObjectiveVerificationRequirement] = {}
        for objective_key in scope.objective_keys:
            for requirement in self.definition(objective_key).completion_requirements:
                requirements.setdefault(requirement.key, requirement)
        return tuple(requirements.values())

    def prerequisites(self, scope: ObjectiveScope) -> tuple[ObjectivePrerequisite, ...]:
        self._validate_scope(scope)
        prerequisites: dict[str, ObjectivePrerequisite] = {}
        for objective_key in scope.objective_keys:
            for prerequisite in self.definition(objective_key).prerequisites:
                prerequisites.setdefault(prerequisite.key, prerequisite)
        return tuple(prerequisites.values())

    def _validate_scope(self, scope: ObjectiveScope) -> None:
        if scope.scenario_key != self.scenario_key:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_SCENARIO_MISMATCH",
                "Objective scope belongs to a different scenario",
            )
        if scope.catalog_version != self.catalog_version:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_CATALOG_MISMATCH",
                "Objective scope belongs to a different catalog version",
            )
        for objective_key in scope.objective_keys:
            self.definition(objective_key)
        full_key = StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value
        if full_key not in scope.objective_keys:
            return
        redundant = self.definition(full_key).subsumes.intersection(scope.objective_keys)
        if redundant:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_REDUNDANT",
                "Full Northern Recovery cannot be combined with objectives it subsumes",
            )

    @staticmethod
    def _evaluate_requirement(
        requirement: ObjectiveVerificationRequirement,
        state: ScenarioRuntimeState,
    ) -> ObjectiveRequirementEvaluation:
        actual = (
            state.fact_value(requirement.node_key, requirement.fact_key)
            if state.fact_known(requirement.node_key, requirement.fact_key)
            else None
        )
        return ObjectiveRequirementEvaluation(
            requirement=requirement,
            actual_value=actual,
            satisfied=actual in requirement.accepted_values,
        )


STARFIRE_OBJECTIVE_CATALOG = StarfireObjectiveCatalog()
FULL_STARFIRE_SCOPE = STARFIRE_OBJECTIVE_CATALOG.scope(
    [StarfireObjectiveKey.FULL_NORTHERN_RECOVERY]
)
