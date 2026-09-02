"""Test constructors for the strict Dynamic Goal requirement union."""

from typing import Any

from app.domain.formal_goal import (
    AdHocDerivedStateRequirementCandidateV1,
    AdHocFactRequirementCandidateV1,
    AdHocGoalRequirementCandidateV1,
    AdHocResourceAtLeastRequirementCandidateV1,
)
from app.domain.scenario_v2 import ObjectiveRequirementKind


def dynamic_candidate(**values: Any) -> AdHocGoalRequirementCandidateV1:
    """Construct one strict candidate from the legacy test call shape."""

    raw_kind = values.pop("kind")
    kind = raw_kind.value if isinstance(raw_kind, ObjectiveRequirementKind) else raw_kind
    if kind == "FACT":
        return AdHocFactRequirementCandidateV1(kind="FACT", **values)
    if kind == "RESOURCE_AT_LEAST":
        return AdHocResourceAtLeastRequirementCandidateV1(kind="RESOURCE_AT_LEAST", **values)
    if kind == "DERIVED_STATE":
        return AdHocDerivedStateRequirementCandidateV1(kind="DERIVED_STATE", **values)
    raise AssertionError(f"Unexpected Dynamic Goal kind: {kind!r}")
