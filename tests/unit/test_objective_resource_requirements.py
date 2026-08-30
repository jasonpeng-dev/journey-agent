from uuid import uuid4

from app.domain.enums import ResourcePoolAvailability, ResourcePoolVisibility
from app.domain.runtime_scope import GameInstanceId, PlayerId, RuntimeScope, ScenarioVersionId
from app.domain.scenario_v2 import ObjectiveRequirementV2
from app.domain.world import Visibility
from app.infrastructure.db.models import GameInstanceResourceState
from app.services.objective_requirements import (
    requirement_gate_is_public,
    truth_requirement_satisfied,
)


def _scope() -> RuntimeScope:
    return RuntimeScope(
        GameInstanceId(uuid4()),
        PlayerId(uuid4()),
        ScenarioVersionId(uuid4()),
    )


def _resource_requirement(*, gated: bool = False) -> ObjectiveRequirementV2:
    return ObjectiveRequirementV2.model_validate(
        {
            "key": "regional_stock",
            "kind": "RESOURCE_AT_LEAST",
            "region_key": "target_region",
            "resource_key": "supplies",
            "minimum": 10,
            "description": "Keep ten supplies at the target.",
            **(
                {
                    "knowledge_gate": {
                        "node_key": "discovery_marker",
                        "fact_key": "discovered",
                        "accepted_values": [True],
                    }
                }
                if gated
                else {}
            ),
        }
    )


def _pool(
    scope: RuntimeScope,
    *,
    pool_key: str,
    value: int,
    reserved: int = 0,
    availability: ResourcePoolAvailability = ResourcePoolAvailability.AVAILABLE,
) -> GameInstanceResourceState:
    return GameInstanceResourceState(
        game_instance_id=scope.game_instance_id,
        resource_identity=f"supplies@target_region@{pool_key}",
        resource_key="supplies",
        scope_node_key="target_region",
        pool_key=pool_key,
        value=value,
        reserved_value=reserved,
        visibility=ResourcePoolVisibility.HIDDEN,
        availability=availability,
    )


def test_legacy_fact_requirement_serialization_remains_compatible() -> None:
    requirement = ObjectiveRequirementV2.model_validate(
        {
            "key": "fact_goal",
            "node_key": "target_node",
            "fact_key": "ready",
            "accepted_values": [True],
            "description": "Target is ready.",
        }
    )

    payload = requirement.model_dump(mode="json")

    assert "kind" not in payload
    assert "region_key" not in payload
    assert payload["node_key"] == "target_node"


class _FakeSession:
    def __init__(self, rows: tuple[object, ...] = (), fact: object | None = None) -> None:
        self.rows = rows
        self.fact = fact

    def scalars(self, _statement):  # type: ignore[no-untyped-def]
        return self.rows

    def get(self, _model, _identity):  # type: ignore[no-untyped-def]
        return self.fact


def test_resource_requirement_uses_available_unreserved_truth_across_pools() -> None:
    scope = _scope()
    session = _FakeSession(
        rows=(
            _pool(scope, pool_key="one", value=7, reserved=2),
            _pool(scope, pool_key="two", value=5),
        ),
    )

    value, satisfied = truth_requirement_satisfied(  # type: ignore[arg-type]
        session, scope, _resource_requirement()
    )

    assert value == 10
    assert satisfied is True


def test_requirement_gate_requires_known_accepted_fact() -> None:
    scope = _scope()
    requirement = _resource_requirement(gated=True)
    fact = type("Fact", (), {"truth_value": True, "visibility": Visibility.HIDDEN})()
    session = _FakeSession(fact=fact)
    assert requirement_gate_is_public(session, scope, requirement) is False  # type: ignore[arg-type]

    fact.visibility = Visibility.KNOWN
    assert requirement_gate_is_public(session, scope, requirement) is True  # type: ignore[arg-type]
