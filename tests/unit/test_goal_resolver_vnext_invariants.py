from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.agent.generic import (
    GenericGoalResolver,
    _dynamic_goal_action_contract,
    _FrozenDynamicGoalEvidence,
    _vnext_allowed_clarification_fields,
    _vnext_normalize_frozen_intent,
    _vnext_operation_slot_matches_frozen,
    _vnext_sanitize_clarification_prompt,
    _vnext_semantic_family_evidence,
)
from app.agent.planner_contract import action_goal_terminal_effects
from app.agent.provider import (
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalIntentDraft,
    DynamicGoalMentionSlot,
    DynamicGoalOperationGrounding,
    DynamicGoalScalarMentionSlot,
    OperationContractSlot,
)
from tests.scenario_fixtures import LINJIANG_V2_TEST
from tests.unit.test_goal_resolver_vnext import (
    _operation,
    _role_grounding,
    _slot,
    _VNextProvider,
)


def _slot_surface(
    status: str,
    ref_type: str,
    key: str,
    surface: str | None,
) -> DynamicGoalMentionSlot:
    return DynamicGoalMentionSlot(
        status=status,
        ref_type=ref_type,  # type: ignore[arg-type]
        key=key if status == "GROUNDED" else None,
        surface=surface,
    )


def _transport_grounding(
    *,
    actor: DynamicGoalMentionSlot | None = None,
    target: DynamicGoalMentionSlot | None = None,
    source: DynamicGoalMentionSlot | None = None,
    resource: DynamicGoalMentionSlot | None = None,
    amount: DynamicGoalScalarMentionSlot | None = None,
    refs: tuple[DynamicGoalCandidateReference, ...] = (
        DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),
    ),
) -> DynamicGoalEntityGrounding:
    return _role_grounding(
        refs,
        actor=actor,
        target=target,
        source=source,
        resource=resource,
        amount=amount,
    )


def _reference_slot(
    *,
    expected_type: str,
    ref_type: str,
    key: str,
) -> OperationContractSlot:
    return OperationContractSlot(
        slot_key="target",
        expected_type=expected_type,  # type: ignore[arg-type]
        status="GROUNDED",
        ref_type=ref_type,  # type: ignore[arg-type]
        key=key,
    )


@pytest.mark.parametrize(
    ("expected", "actual", "matches"),
    [
        (
            _reference_slot(
                expected_type="NODE",
                ref_type="REGION",
                key="east_residential_district",
            ),
            _reference_slot(
                expected_type="NODE",
                ref_type="NODE",
                key="east_residential_district",
            ),
            True,
        ),
        (
            _reference_slot(
                expected_type="REGION",
                ref_type="REGION",
                key="east_residential_district",
            ),
            _reference_slot(
                expected_type="REGION",
                ref_type="NODE",
                key="east_residential_district",
            ),
            False,
        ),
        (
            _reference_slot(expected_type="RESOURCE", ref_type="RESOURCE", key="foo"),
            _reference_slot(expected_type="RESOURCE", ref_type="NODE", key="foo"),
            False,
        ),
        (
            _reference_slot(expected_type="NODE", ref_type="NODE", key="foo"),
            _reference_slot(expected_type="NODE", ref_type="NODE", key="bar"),
            False,
        ),
        (
            _reference_slot(expected_type="NODE", ref_type="NODE", key="foo"),
            _reference_slot(expected_type="NODE", ref_type="NODE", key="foo"),
            True,
        ),
    ],
    ids=[
        "node-contract-region-node-same-key",
        "region-contract-region-node-same-key",
        "resource-contract-node-same-key",
        "node-contract-different-key",
        "exact-reference-match",
    ],
)
def test_frozen_reference_slot_compatibility_is_contract_driven(
    expected: OperationContractSlot,
    actual: OperationContractSlot,
    matches: bool,
) -> None:
    assert _vnext_operation_slot_matches_frozen(actual, expected) is matches


def test_travel_accepts_same_canonical_target_with_node_region_representation() -> None:
    target_key = "east_residential_district"
    provider = _VNextProvider(
        grounding=[
            _role_grounding(
                (DynamicGoalCandidateReference(ref_type="REGION", key=target_key),),
                actor=_slot_surface("GROUNDED", "ACTOR", "electrical_repair_team_alpha", "actor"),
                target=_slot_surface("GROUNDED", "REGION", target_key, "east residential"),
            )
        ],
        family="OPERATION",
        action_key="travel",
        operation=[
            _operation(
                "travel",
                actor=_slot(
                    "actor",
                    "ACTOR",
                    "GROUNDED",
                    ref_type="ACTOR",
                    key="electrical_repair_team_alpha",
                ),
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key=target_key,
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "actor travel to east residential", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].target_key == target_key
    assert len(provider.operation_requests) == 1


def test_deterministic_exact_target_does_not_create_a_semantic_role() -> None:
    provider = _VNextProvider(
        grounding=[
            _transport_grounding(
                refs=(
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="east_residential_district"
                    ),
                )
            )
        ],
        family="OPERATION",
        action_key="travel",
        operation=[_operation("travel", target=_slot("target", "NODE", "NOT_SPECIFIED"))],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "east_residential_district", LINJIANG_V2_TEST
    )

    assert resolution.status == "NEEDS_CLARIFICATION"
    assert resolution.source == "GOAL_REQUIRED_SLOT_MISSING"
    assert resolution.dynamic_requirements == ()
    assert resolution.provider_observation is not None
    assert set(resolution.provider_observation["diagnostics"]["missing_slot_keys"]) == {
        "actor",
        "target",
    }


def test_semantic_frozen_source_survives_operation_grounding() -> None:
    source = _slot_surface("GROUNDED", "REGION", "north_industrial_district", "from north")
    target = _slot_surface("GROUNDED", "REGION", "east_residential_district", "to east")
    resource = _slot_surface("GROUNDED", "RESOURCE", "emergency_fuel", "fuel")
    provider = _VNextProvider(
        grounding=[
            _transport_grounding(
                source=source,
                target=target,
                resource=resource,
                amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30, surface="30"),
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
                    key="east_residential_district",
                ),
                bindings=[
                    _slot(
                        "source_region",
                        "REGION",
                        "GROUNDED",
                        ref_type="REGION",
                        key="north_industrial_district",
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
        "from north 30 emergency fuel to east", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].binding_constraints[0].value == (
        "north_industrial_district"
    )


@pytest.mark.parametrize(
    ("bad_target", "name"),
    [
        (_slot("target", "NODE", "UNRESOLVED"), "grounded_to_unresolved"),
        (_slot("target", "NODE", "NOT_SPECIFIED"), "grounded_to_not_specified"),
        (
            _slot("target", "NODE", "GROUNDED", ref_type="NODE", key="south_bridge"),
            "replaced_canonical_key",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "slot",
)
def test_operation_cannot_erase_or_replace_frozen_target(
    bad_target: dict[str, object], name: str
) -> None:
    target = _slot_surface("GROUNDED", "NODE", "central_hospital", "hospital")
    grounding = _transport_grounding(
        refs=(DynamicGoalCandidateReference(ref_type="NODE", key="south_bridge"),),
        target=target,
    )
    provider = _VNextProvider(
        grounding=[grounding],
        family="OPERATION",
        action_key="inspect",
        operation=[
            _operation("inspect", target=bad_target),
            _operation("inspect", target=bad_target),
        ],
    )

    if name == "replaced_canonical_key":
        with pytest.raises(Exception) as caught:
            GenericGoalResolver(provider=provider).resolve(
                "custom hospital goal", LINJIANG_V2_TEST
            )
        assert getattr(caught.value, "code", None) == "PROVIDER_SCHEMA_INVALID"
        assert len(provider.operation_requests) == 2
    else:
        resolution = GenericGoalResolver(provider=provider).resolve(
            "custom hospital goal", LINJIANG_V2_TEST
        )
        assert resolution.status == "RESOLVED"
        assert resolution.dynamic_requirements[0].target_key == "central_hospital"
        assert len(provider.operation_requests) == 1
    assert len(provider.family_requests) == 1
    assert len(provider.action_requests) == 1


def test_contextual_actor_candidate_does_not_override_not_specified_role() -> None:
    actor = DynamicGoalMentionSlot(status="NOT_SPECIFIED")
    target = _slot_surface("GROUNDED", "NODE", "central_hospital", "hospital")
    provider = _VNextProvider(
        grounding=[
            _transport_grounding(
                refs=(
                    DynamicGoalCandidateReference(
                        ref_type="ACTOR", key="logistics_team_alpha"
                    ),
                ),
                actor=actor,
                target=target,
            )
        ],
        family="OPERATION",
        action_key="inspect",
        operation=[
            _operation(
                "inspect",
                target=_slot(
                    "target", "NODE", "GROUNDED", ref_type="NODE", key="central_hospital"
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "travel to hospital", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].actor_key is None


def test_semantic_source_role_is_frozen_without_optional_surface() -> None:
    intent = _transport_grounding(
        refs=(DynamicGoalCandidateReference(ref_type="REGION", key="north_industrial_district"),),
        source=DynamicGoalMentionSlot(
            status="GROUNDED", ref_type="REGION", key="north_industrial_district"
        ),
    ).intent
    assert intent is not None
    normalized = _vnext_normalize_frozen_intent(
        "transport supplies to east",
        intent,
        (),
    )
    assert normalized is not None
    assert normalized.source.status == "GROUNDED"
    assert normalized.source.key == "north_industrial_district"


def test_grounded_semantic_role_without_surface_is_frozen() -> None:
    intent = _transport_grounding(
        refs=(DynamicGoalCandidateReference(ref_type="NODE", key="central_hospital"),),
        target=DynamicGoalMentionSlot(
            status="GROUNDED", ref_type="NODE", key="central_hospital"
        ),
    ).intent
    assert intent is not None
    normalized = _vnext_normalize_frozen_intent("inspect something", intent, ())
    assert normalized is not None
    assert normalized.target.status == "GROUNDED"
    assert normalized.target.key == "central_hospital"


@pytest.mark.parametrize(
    ("role", "ref_type", "key", "surface"),
    [
        ("actor", "ACTOR", "logistics_team_alpha", "logistics team"),
        ("source", "REGION", "north_industrial_district", "north depot"),
        ("target", "NODE", "central_hospital", "central hospital"),
        ("resource", "RESOURCE", "emergency_fuel", "emergency fuel"),
    ],
)
def test_validated_semantic_role_is_not_reinterpreted_from_surface(
    role: str,
    ref_type: str,
    key: str,
    surface: str,
) -> None:
    intent = _transport_grounding(
        **{
            role: DynamicGoalMentionSlot(
                status="GROUNDED",
                ref_type=ref_type,
                key=key,
                surface=surface,
            )
        }
    ).intent
    assert intent is not None

    normalized = _vnext_normalize_frozen_intent(
        "transport supplies to east",
        intent,
        (),
    )

    assert normalized is not None
    assert getattr(normalized, role).status == "GROUNDED"
    assert getattr(normalized, role).key == key


def test_real_semantic_surface_is_preserved_after_normalization() -> None:
    intent = _transport_grounding(
        target=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="REGION",
            key="east_residential_district",
            surface="east",
        )
    ).intent
    assert intent is not None

    normalized = _vnext_normalize_frozen_intent("move to east", intent, ())

    assert normalized is not None
    assert normalized.target.status == "GROUNDED"
    assert normalized.target.key == "east_residential_district"


def test_normalized_equivalent_semantic_surface_is_preserved() -> None:
    intent = _transport_grounding(
        source=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="REGION",
            key="north_industrial_district",
            surface="north_depot",
        )
    ).intent
    assert intent is not None

    normalized = _vnext_normalize_frozen_intent(
        "move from north depot",
        intent,
        (),
    )

    assert normalized is not None
    assert normalized.source.status == "GROUNDED"


def test_semantic_identity_does_not_need_deterministic_or_surface_authority() -> None:
    intent = _transport_grounding(
        target=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="REGION",
            key="east_residential_district",
        )
    ).intent
    assert intent is not None

    normalized = _vnext_normalize_frozen_intent(
        "go east",
        intent,
        (
            DynamicGoalCandidateReference(
                ref_type="REGION",
                key="east_residential_district",
                provenance="EXACT_USER_MENTION",
            ),
        ),
    )

    assert normalized is not None
    assert normalized.target.status == "GROUNDED"
    assert normalized.target.key == "east_residential_district"


def test_provider_role_provenance_without_goal_evidence_cannot_freeze_in_production_path() -> None:
    intent = _transport_grounding(
        target=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="NODE",
            key="central_hospital",
            provenance="SEMANTIC_ROLE_EVIDENCE",
        )
    ).intent
    assert intent is not None
    catalog = {
        "references": [{"ref_type": "NODE", "key": "central_hospital", "name": "Central Hospital"}]
    }

    normalized = _vnext_normalize_frozen_intent(
        "inspect the facility",
        intent,
        (),
        catalog,
    )

    assert normalized is not None
    assert normalized.target.status == "NOT_SPECIFIED"
    assert normalized.target.provenance == "NOT_SPECIFIED"


def test_explicit_semantic_surface_is_backend_marked_and_omitted_source_stays_unspecified() -> None:
    intent = _transport_grounding(
        source=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="NODE",
            key="east_distribution_station",
            surface="east station",
            match_semantics="SEMANTIC_EQUIVALENT",
            provenance="INFERRED",
        )
    ).intent
    assert intent is not None
    catalog = {
        "references": [
            {
                "ref_type": "NODE",
                "key": "east_distribution_station",
                "name": "East Distribution Station",
            }
        ]
    }
    normalized = _vnext_normalize_frozen_intent(
        "supply power from east station",
        intent,
        (),
        catalog,
    )
    assert normalized is not None
    assert normalized.source.status == "GROUNDED"
    assert normalized.source.key == "east_distribution_station"
    assert normalized.source.provenance == "SEMANTIC_ROLE_EVIDENCE"

    omitted = intent.model_copy(
        update={
            "source": DynamicGoalMentionSlot(
                status="GROUNDED",
                ref_type="NODE",
                key="east_distribution_station",
                provenance="SEMANTIC_ROLE_EVIDENCE",
            )
        }
    )
    normalized_omitted = _vnext_normalize_frozen_intent(
        "supply power to the hospital",
        omitted,
        (),
        catalog,
    )
    assert normalized_omitted is not None
    assert normalized_omitted.source.status == "NOT_SPECIFIED"


def test_operation_terminal_projection_is_generic_and_excludes_travel() -> None:
    supply = next(item for item in LINJIANG_V2_TEST.actions if item.key == "supply_power")
    repair = next(item for item in LINJIANG_V2_TEST.actions if item.key == "repair_facility")
    clear = next(item for item in LINJIANG_V2_TEST.actions if item.key == "clear_transport")
    travel = next(item for item in LINJIANG_V2_TEST.actions if item.key == "travel")

    supply_effects = action_goal_terminal_effects(LINJIANG_V2_TEST, supply, "central_hospital")
    repair_effects = action_goal_terminal_effects(LINJIANG_V2_TEST, repair, "central_hospital")
    clear_effects = action_goal_terminal_effects(LINJIANG_V2_TEST, clear, "central_hospital")
    assert ("central_hospital", "power_supply", "AVAILABLE") in supply_effects
    assert ("central_hospital", "operational", True) in repair_effects
    assert ("central_hospital", "passable", True) in clear_effects
    assert action_goal_terminal_effects(LINJIANG_V2_TEST, travel, "central_hospital") == ()


def test_family_advisory_evidence_distinguishes_operation_equivalence() -> None:
    supply = next(item for item in LINJIANG_V2_TEST.actions if item.key == "supply_power")
    intent = DynamicGoalIntentDraft(
        intent_kind="OPERATION",
        action=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="ACTION",
            key="supply_power",
            match_semantics="SEMANTIC_EQUIVALENT",
        ),
        target=DynamicGoalMentionSlot(
            status="GROUNDED",
            ref_type="NODE",
            key="central_hospital",
        ),
    )
    evidence = _FrozenDynamicGoalEvidence(frozen_intent=intent)
    advisory = _vnext_semantic_family_evidence(evidence, LINJIANG_V2_TEST)
    assert advisory.operation_expressed is True
    assert advisory.state_equivalent_available is True
    assert supply.key == "supply_power"


def test_semantic_role_binding_is_authoritative_even_with_deterministic_hint() -> None:
    target = _slot_surface(
        "GROUNDED",
        "REGION",
        "east_residential_district",
        "east",
    )
    provider = _VNextProvider(
        grounding=[
            _transport_grounding(
                refs=(
                    DynamicGoalCandidateReference(
                        ref_type="REGION", key="east_residential_district"
                    ),
                ),
                target=target,
            )
        ],
        family="OPERATION",
        action_key="inspect",
        operation=[
            _operation(
                "inspect",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="REGION",
                    key="east_residential_district",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "go east", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].target_key == "east_residential_district"


def test_explicit_ambiguous_role_remains_unresolved() -> None:
    intent = _transport_grounding(
        resource=DynamicGoalMentionSlot(
            status="UNRESOLVED", ref_type="RESOURCE", surface="parts"
        )
    ).intent
    assert intent is not None
    normalized = _vnext_normalize_frozen_intent("transport parts", intent, ())
    assert normalized is not None
    assert normalized.resource.status == "UNRESOLVED"


def test_survey_resources_generic_noun_does_not_require_resource_identity() -> None:
    target = _slot_surface("GROUNDED", "REGION", "central_district", "central district")
    provider = _VNextProvider(
        grounding=[
            _transport_grounding(
                refs=(DynamicGoalCandidateReference(ref_type="REGION", key="central_district"),),
                target=target,
                resource=DynamicGoalMentionSlot(
                    status="UNRESOLVED", ref_type="RESOURCE", surface="resources"
                ),
            ),
            _transport_grounding(
                refs=(DynamicGoalCandidateReference(ref_type="REGION", key="central_district"),),
                target=target,
                resource=DynamicGoalMentionSlot(
                    status="UNRESOLVED", ref_type="RESOURCE", surface="resources"
                ),
            ),
        ],
        family="OPERATION",
        action_key="survey_resources",
        operation=[
            DynamicGoalOperationGrounding(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="Please name a resource type",
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "survey resources at central district", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert resolution.dynamic_requirements[0].action_key == "survey_resources"
    assert resolution.dynamic_requirements[0].target_key == "central_district"
    assert resolution.dynamic_requirements[0].parameter_constraints is None


def _transport_evidence_with_unresolved_resource() -> _FrozenDynamicGoalEvidence:
    intent = _transport_grounding(
        target=_slot_surface("GROUNDED", "REGION", "south_waterfront_district", "south"),
        resource=DynamicGoalMentionSlot(
            status="UNRESOLVED", ref_type="RESOURCE", surface="parts"
        ),
        amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30, surface="30"),
    ).intent
    assert intent is not None
    return _FrozenDynamicGoalEvidence(frozen_intent=intent)


def test_clarification_allows_only_explicit_unresolved_contract_fields() -> None:
    action = next(item for item in LINJIANG_V2_TEST.actions if item.key == "transport_resource")
    contract = _dynamic_goal_action_contract(action)
    allowed = _vnext_allowed_clarification_fields(
        contract,
        _transport_evidence_with_unresolved_resource(),
    )
    assert allowed == ("resource_key", "source_region")


def test_bare_parts_clarification_asks_for_resource_only() -> None:
    evidence = _transport_evidence_with_unresolved_resource()
    action = next(item for item in LINJIANG_V2_TEST.actions if item.key == "transport_resource")
    contract = _dynamic_goal_action_contract(action)
    prompt = _vnext_sanitize_clarification_prompt(
        "Please specify action, target, amount, and resource.",
        ("resource_key",),
        contract,
    )
    assert "resource_key" in prompt
    assert "target" not in prompt.casefold()
    assert "amount" not in prompt.casefold()
    assert evidence.frozen_intent is not None


def test_known_amount_target_action_are_not_repeated_in_prompt() -> None:
    action = next(item for item in LINJIANG_V2_TEST.actions if item.key == "transport_resource")
    contract = _dynamic_goal_action_contract(action)
    prompt = _vnext_sanitize_clarification_prompt(
        "Which resource should be transported?",
        ("resource_key",),
        contract,
    )
    assert "resource" in prompt.casefold()
    assert "amount" not in prompt.casefold()
    assert "target" not in prompt.casefold()
    assert "action" not in prompt.casefold()


def test_runtime_required_does_not_become_goal_required() -> None:
    action = next(item for item in LINJIANG_V2_TEST.actions if item.key == "transport_resource")
    contract = copy.deepcopy(_dynamic_goal_action_contract(action))
    parameters = contract["parameters"]
    assert isinstance(parameters, list)
    for item in parameters:
        if item["slot_key"] == "amount":
            item["runtime_required"] = True
            item["goal_required"] = False
    allowed = _vnext_allowed_clarification_fields(
        contract,
        _FrozenDynamicGoalEvidence(),
    )
    assert "amount" not in allowed
    assert {"resource_key", "source_region", "target"}.issubset(allowed)


def test_no_allowed_clarification_fields_cannot_fall_back_to_provider_prompt() -> None:
    provider = _VNextProvider(
        grounding=[
            _transport_grounding(
                target=_slot_surface(
                    "GROUNDED",
                    "REGION",
                    "east_residential_district",
                    "east",
                ),
                resource=_slot_surface(
                    "GROUNDED",
                    "RESOURCE",
                    "emergency_fuel",
                    "fuel",
                ),
            )
        ],
        family="OPERATION",
        action_key="clear_transport",
        operation=[
            DynamicGoalOperationGrounding(
                status="NEEDS_CLARIFICATION",
                clarification_prompt="Please specify action, target, and resource.",
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "travel to east with fuel", LINJIANG_V2_TEST
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.source == "GOAL_UNREPRESENTABLE"
    assert resolution.clarification_prompt is None
    assert resolution.provider_observation["allowed_clarification_fields"] == []
    assert len(provider.operation_requests) == 1


def test_operation_recovery_is_local_and_action_stays_frozen() -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="OPERATION",
        action_key="inspect",
        operation=[
            {"status": "RESOLVED", "intent": {"action_key": "travel"}},
            _operation(
                "inspect",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            ),
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "inspect a public target", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.operation_requests) == 2
    assert {item.action_key for item in provider.operation_requests} == {"inspect"}
    assert len(provider.action_requests) == 1
    assert len(provider.family_requests) == 1


def test_action_response_cannot_change_frozen_family() -> None:
    provider = _VNextProvider(
        grounding=[DynamicGoalEntityGrounding(status="UNSUPPORTED")],
        family="OPERATION",
        action_key="inspect",
        action_results=[
            {"frozen_family": "STATE", "action_match": "MATCHED", "action_key": "inspect"},
            {"action_match": "MATCHED", "action_key": "inspect"},
        ],
        operation=[
            _operation(
                "inspect",
                target=_slot(
                    "target",
                    "NODE",
                    "GROUNDED",
                    ref_type="NODE",
                    key="central_hospital",
                ),
            )
        ],
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "inspect a public target", LINJIANG_V2_TEST
    )

    assert resolution.status == "RESOLVED"
    assert len(provider.family_requests) == 1
    assert len(provider.action_requests) == 2
    assert len(provider.operation_requests) == 1


def test_operation_contract_slot_dto_rejects_unknown_status_shape() -> None:
    with pytest.raises(ValidationError):
        OperationContractSlot(
            slot_key="target",
            expected_type="NODE",
            status="GROUNDED",
        )

