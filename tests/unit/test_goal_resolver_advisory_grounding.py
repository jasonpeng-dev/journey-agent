from __future__ import annotations

import pytest

from app.agent.generic import GenericGoalResolver
from app.agent.provider import (
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalMentionSlot,
    DynamicGoalScalarMentionSlot,
    GenericProviderError,
)
from tests.scenario_fixtures import LINJIANG_V2_TEST
from tests.unit.test_contract_driven_goal_resolver import _operation, _slot
from tests.unit.test_goal_resolver_vnext import _role_grounding, _VNextProvider


@pytest.mark.parametrize(
    ("goal", "target_key", "surface", "expected_hint"),
    [
        ("修复中央通信塔", "central_telecom_hub", "中央通信塔", ("REGION", "central_district")),
        (
            "修复西部应急车辆基地",
            "vehicle_depot",
            "西部应急车辆基地",
            ("REGION", "west_logistics_district"),
        ),
        (
            "修复东部配电站",
            "east_distribution_station",
            "东部配电站",
            ("NODE", "east_distribution_station"),
        ),
        (
            "修复中央通信枢纽",
            "central_telecom_hub",
            "中央通信枢纽",
            ("NODE", "central_telecom_hub"),
        ),
    ],
)
def test_facility_repairs_use_semantic_target_not_lexical_region_hint(
    goal: str,
    target_key: str,
    surface: str,
    expected_hint: tuple[str, str],
) -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (DynamicGoalCandidateReference(ref_type="NODE", key=target_key),),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key=target_key,
                    surface=surface,
                ),
            )
        ],
        family="OPERATION",
        action_key="repair_facility",
        operation=[
            _operation(
                "repair_facility",
                target=_slot("target", "NODE", "GROUNDED", ref_type="NODE", key=target_key),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(goal, LINJIANG_V2_TEST)

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].action_key == "repair_facility"
    assert resolution.dynamic_requirements[0].target_key == target_key
    assert expected_hint in {
        (item.ref_type, item.key)
        for item in provider.grounding_requests[0].deterministic_candidate_refs
    }
    assert resolution.dynamic_requirements[0].target_key not in {
        item.key
        for item in provider.operation_requests[0].semantic_candidate_refs
        if item.ref_type == "REGION"
    }


@pytest.mark.parametrize(
    ("goal", "region_key", "surface"),
    [
        ("修复中央城区", "central_district", "中央城区"),
        ("修复东部", "east_residential_district", "东部"),
    ],
)
def test_region_repair_is_not_misresolved_as_facility_repair(
    goal: str,
    region_key: str,
    surface: str,
) -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (DynamicGoalCandidateReference(ref_type="REGION", key=region_key),),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="REGION",
                    key=region_key,
                    surface=surface,
                ),
            )
        ],
        family="OPERATION",
    )

    resolution = GenericGoalResolver(provider=provider).resolve(goal, LINJIANG_V2_TEST)

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "ACTION_NO_MATCH"
    assert provider.operation_requests == []


@pytest.mark.parametrize(
    ("goal", "actor_key", "actor_surface", "expected_status"),
    [
        (
            "让通信抢修一队修中央通信枢纽",
            "communications_repair_team_alpha",
            "通信抢修一队",
            "RESOLVED",
        ),
        (
            "让水务抢修一队修中央通信枢纽",
            "water_repair_team_alpha",
            "水务抢修一队",
            "UNSUPPORTED",
        ),
    ],
)
def test_explicit_actor_compatibility_is_validated(
    goal: str,
    actor_key: str,
    actor_surface: str,
    expected_status: str,
) -> None:
    target_key = "central_telecom_hub"
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="ACTOR", key=actor_key),
                    DynamicGoalCandidateReference(ref_type="NODE", key=target_key),
                ),
                actor=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="ACTOR",
                    key=actor_key,
                    surface=actor_surface,
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key=target_key,
                    surface="中央通信枢纽",
                ),
            )
        ],
        family="OPERATION",
        action_key="repair_facility",
        operation=[
            _operation(
                "repair_facility",
                target=_slot("target", "NODE", "GROUNDED", ref_type="NODE", key=target_key),
            )
        ],
    )
    provider.operation_results[0]["intent"]["actor"] = _slot(
        "actor", "ACTOR", "GROUNDED", ref_type="ACTOR", key=actor_key
    )

    resolution = GenericGoalResolver(provider=provider).resolve(goal, LINJIANG_V2_TEST)

    assert resolution.status == expected_status
    if expected_status == "RESOLVED":
        assert resolution.dynamic_requirements[0].actor_key == actor_key
    else:
        assert resolution.source == "EXPLICIT_ACTOR_ACTION_CONFLICT"


def test_transport_preserves_all_explicit_semantic_constraints() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="southeast_heights_district"
                    ),
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="south_waterfront_district"
                    ),
                    DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
                ),
                source=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="REGION",
                    key="southeast_heights_district",
                    surface="东南片区",
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="REGION",
                    key="south_waterfront_district",
                    surface="南部",
                ),
                resource=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="RESOURCE",
                    key="emergency_fuel",
                    surface="应急燃料",
                ),
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30, surface="30个"),
            )
        ],
        family="OPERATION",
        action_key="transport_resource",
        operation=[
            _operation(
                "transport_resource",
                target=_slot(
                    "target",
                    "REGION",
                    "GROUNDED",
                    ref_type="REGION",
                    key="south_waterfront_district",
                ),
                bindings=[
                    _slot(
                        "source_region",
                        "REGION",
                        "GROUNDED",
                        ref_type="REGION",
                        key="southeast_heights_district",
                    )
                ],
                parameters=[
                    _slot("amount", "INTEGER", "GROUNDED", value=30),
                    _slot(
                        "resource_key",
                        "RESOURCE",
                        "GROUNDED",
                        ref_type="RESOURCE",
                        key="emergency_fuel",
                    ),
                ],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从东南片区运30个应急燃料到南部", LINJIANG_V2_TEST
    )

    requirement = resolution.dynamic_requirements[0]
    assert resolution.status == "RESOLVED"
    assert requirement.target_key == "south_waterfront_district"
    assert [(item.role, item.value) for item in requirement.binding_constraints] == [
        ("source_region", "southeast_heights_district")
    ]
    assert requirement.parameter_constraints == {
        "resources": [{"amount": 30, "resource_key": "emergency_fuel"}]
    }


def test_supply_power_keeps_unspecified_source_unconstrained() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),
                    DynamicGoalCandidateReference(
                        ref_type="NODE", key="southeast_emergency_power_station"
                    ),
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                    surface="中央医院",
                ),
            )
        ],
        family="OPERATION",
        action_key="supply_power",
        operation=[
            _operation(
                "supply_power",
                target=_slot("target", "NODE", "GROUNDED", ref_type="NODE", key="central_hospital"),
                parameters=[
                    _slot(
                        "source_key",
                        "NODE",
                        "GROUNDED",
                        ref_type="NODE",
                        key="southeast_emergency_power_station",
                    )
                ],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve("给中央医院供电", LINJIANG_V2_TEST)

    requirement = resolution.dynamic_requirements[0]
    assert resolution.status == "RESOLVED"
    assert requirement.action_key == "supply_power"
    assert requirement.target_key == "central_hospital"
    assert requirement.parameter_constraints is None
    assert provider.operation_requests[0].explicit_role_evidence["source"]["status"] == (
        "NOT_SPECIFIED"
    )


def test_supply_power_preserves_explicit_valid_source() -> None:
    source_key = "east_distribution_station"
    target_key = "east_community_hospital"
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(ref_type="NODE", key=source_key),
                    DynamicGoalCandidateReference(ref_type="NODE", key=target_key),
                ),
                source=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key=source_key,
                    surface="东部配电站",
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="NODE",
                    key=target_key,
                    surface="东区社区医院",
                ),
            )
        ],
        family="OPERATION",
        action_key="supply_power",
        operation=[
            _operation(
                "supply_power",
                target=_slot("target", "NODE", "GROUNDED", ref_type="NODE", key=target_key),
                parameters=[_slot("source_key", "NODE", "NOT_SPECIFIED")],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "从东部配电站向东区社区医院供电", LINJIANG_V2_TEST
    )

    requirement = resolution.dynamic_requirements[0]
    assert resolution.status == "RESOLVED"
    assert requirement.action_key == "supply_power"
    assert requirement.target_key == target_key
    assert requirement.parameter_constraints == {"source_key": source_key}
    assert provider.operation_requests[0].explicit_role_evidence["source"]["key"] == source_key


def test_transport_required_source_cannot_remain_unspecified() -> None:
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="southeast_heights_district"
                    ),
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="south_waterfront_district"
                    ),
                    DynamicGoalCandidateReference(ref_type="RESOURCE", key="emergency_fuel"),
                ),
                target=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="REGION",
                    key="south_waterfront_district",
                    surface="南部",
                ),
                resource=DynamicGoalMentionSlot(
                    status="GROUNDED",
                    ref_type="RESOURCE",
                    key="emergency_fuel",
                    surface="应急燃料",
                ),
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30, surface="30个"),
            )
        ],
        family="OPERATION",
        action_key="transport_resource",
        operation=[
            _operation(
                "transport_resource",
                target=_slot(
                    "target",
                    "REGION",
                    "GROUNDED",
                    ref_type="REGION",
                    key="south_waterfront_district",
                ),
                bindings=[
                    _slot(
                        "source_region",
                        "REGION",
                        "GROUNDED",
                        ref_type="REGION",
                        key="southeast_heights_district",
                    )
                ],
                parameters=[
                    _slot("amount", "INTEGER", "GROUNDED", value=30),
                    _slot(
                        "resource_key",
                        "RESOURCE",
                        "GROUNDED",
                        ref_type="RESOURCE",
                        key="emergency_fuel",
                    ),
                ],
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "把30个应急燃料运到南部", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()
    assert resolution.provider_observation is not None
    assert resolution.provider_observation["diagnostics"]["missing_slot_keys"] == [
        "source_region"
    ]


def test_semantic_grounding_cannot_invent_identity_outside_public_catalog() -> None:
    invalid = DynamicGoalEntityGrounding(
        candidate_refs=(DynamicGoalCandidateReference(ref_type="NODE", key="invented_facility"),)
    )
    provider = _VNextProvider(
        grounding=[invalid, invalid],
        family="OPERATION",
        action_key="repair_facility",
    )

    with pytest.raises(GenericProviderError) as exc_info:
        GenericGoalResolver(provider=provider).resolve("修复虚构设施", LINJIANG_V2_TEST)

    assert exc_info.value.code == "PROVIDER_SCHEMA_INVALID"
    assert exc_info.value.validation_diagnostics[0]["code"] == (
        "FORMAL_GOAL_DYNAMIC_ENTITY_NOT_PUBLIC"
    )
