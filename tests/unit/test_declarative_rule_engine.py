from copy import deepcopy
from typing import Any

import pytest

from app.domain.enums import (
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.resources import RUNTIME_KNOWN_INFLOW_POOL_KEY
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import AccessState, Visibility
from app.engine.rules import (
    ActionRuleContext,
    DeclarativeRuleEngine,
    DeclarativeRuleState,
    RuleEngineError,
    RuleFactState,
    RuleNodeState,
    RuleRegionResourceKnowledgeState,
    RuleResourcePoolState,
)
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _document() -> dict[str, Any]:
    document = _contract_scenario_document()
    document["rules"] = [
        {
            "key": "insufficient_medicine",
            "phase": "PREFLIGHT",
            "action_key": "treat_patient",
            "priority": 100,
            "condition": {
                "kind": "RESOURCE_COMPARE",
                "resource_key": "medicine",
                "operator": "LT",
                "value": 1,
            },
            "effects": [
                {
                    "kind": "EMIT_FAILURE",
                    "failure_code": "INSUFFICIENT_MEDICINE",
                    "message": "Medicine is unavailable.",
                    "retryable": True,
                }
            ],
        },
        {
            "key": "high_dose_treatment",
            "phase": "RESOLVE",
            "action_key": "treat_patient",
            "priority": 20,
            "condition": {
                "kind": "PARAMETER_COMPARE",
                "parameter_key": "dosage",
                "operator": "GTE",
                "value": 3,
            },
            "effects": [
                {
                    "kind": "SET_FACT",
                    "node": {"kind": "CURRENT_TARGET"},
                    "fact_key": "stable",
                    "value": {"source": "LITERAL", "literal": True},
                },
                {
                    "kind": "ADJUST_RESOURCE",
                    "resource_key": "medicine",
                    "amount": {
                        "source": "PARAMETER",
                        "parameter_key": "dosage",
                        "multiplier": -1,
                    },
                },
                {"kind": "EMIT_OUTCOME", "outcome_code": "COMPLETED"},
            ],
        },
        {
            "key": "standard_treatment",
            "phase": "RESOLVE",
            "action_key": "treat_patient",
            "priority": 0,
            "effects": [{"kind": "EMIT_OUTCOME", "outcome_code": "COMPLETED"}],
        },
    ]
    return document


def _state(*, medicine: int = 10) -> DeclarativeRuleState:
    return DeclarativeRuleState(
        nodes={
            key: RuleNodeState(Visibility.KNOWN, AccessState.AVAILABLE)
            for key in ("patient_one", "triage_room")
        },
        facts={
            ("patient_one", "stable"): RuleFactState(False, Visibility.KNOWN),
        },
        resources={"medicine": medicine},
        resource_reservations={"medicine": 0},
    )


def _context(dosage: int = 3) -> ActionRuleContext:
    return ActionRuleContext("treat_patient", "patient_one", {"dosage": dosage}, "doctor_lee")


def test_rule_engine_is_deterministic_pure_and_returns_generic_mutations() -> None:
    engine = DeclarativeRuleEngine(ScenarioDefinitionV2.model_validate(_document()))
    state = _state()

    first = engine.evaluate(state, _context(4))
    second = engine.evaluate(state, _context(4))

    assert first == second
    assert first.selected_rule_key == "high_dose_treatment"
    assert first.outcome_code == "COMPLETED"
    assert first.fact_updates[0].value is True
    assert first.resource_mutations[0].amount == -4
    assert state.facts[("patient_one", "stable")].value is False
    assert state.resources["medicine"] == 10


def test_preflight_failure_short_circuits_resolution() -> None:
    outcome = DeclarativeRuleEngine(ScenarioDefinitionV2.model_validate(_document())).evaluate(
        _state(medicine=0), _context()
    )

    assert outcome.selected_rule_key == "insufficient_medicine"
    assert outcome.failure is not None
    assert outcome.failure.code == "INSUFFICIENT_MEDICINE"
    assert outcome.failure.retryable


def test_unsurveyed_authored_pool_is_not_a_known_resource_compare_source() -> None:
    knowledge = {
        "region_a": RuleRegionResourceKnowledgeState(
            resource_inventory_visibility=ResourceInventoryVisibility.HIDDEN,
            resource_survey_completed=False,
        )
    }
    authored_pool = RuleResourcePoolState(
        pool_key="authored",
        resource_key="medicine",
        region_key="region_a",
        facility_key=None,
        quantity=20,
        visibility=ResourcePoolVisibility.VISIBLE,
        availability=ResourcePoolAvailability.AVAILABLE,
        survey_discoverable=False,
    )
    inflow_pool = RuleResourcePoolState(
        pool_key=RUNTIME_KNOWN_INFLOW_POOL_KEY,
        resource_key="medicine",
        region_key="region_a",
        facility_key=None,
        quantity=3,
        visibility=ResourcePoolVisibility.VISIBLE,
        availability=ResourcePoolAvailability.AVAILABLE,
        survey_discoverable=False,
    )
    state = DeclarativeRuleState(
        nodes={},
        facts={},
        resources={},
        resource_reservations={},
        resource_pools={"authored": authored_pool, "inflow": inflow_pool},
        region_resource_knowledge=knowledge,
    )

    assert DeclarativeRuleEngine._resource_value(state, "medicine", "region_a") == 3

    state_without_inflow = DeclarativeRuleState(
        nodes=state.nodes,
        facts=state.facts,
        resources=state.resources,
        resource_reservations=state.resource_reservations,
        resource_pools={"authored": authored_pool},
        region_resource_knowledge=knowledge,
    )
    with pytest.raises(RuleEngineError) as caught:
        DeclarativeRuleEngine._resource_value(state_without_inflow, "medicine", "region_a")
    assert caught.value.code == "RULE_RESOURCE_MISSING"


def test_lower_priority_unconditional_rule_is_explicit_fallback() -> None:
    outcome = DeclarativeRuleEngine(ScenarioDefinitionV2.model_validate(_document())).evaluate(
        _state(), _context(1)
    )

    assert outcome.selected_rule_key == "standard_treatment"


def test_engine_fails_closed_for_ambiguous_highest_priority() -> None:
    document = _document()
    duplicate = deepcopy(document["rules"][1])
    duplicate["key"] = "also_high_dose"
    document["rules"].append(duplicate)
    engine = DeclarativeRuleEngine(ScenarioDefinitionV2.model_validate(document))

    with pytest.raises(RuleEngineError) as caught:
        engine.evaluate(_state(), _context())

    assert caught.value.code == "RULE_RESOLUTION_AMBIGUOUS"


def test_engine_fails_closed_when_no_resolution_matches() -> None:
    document = _document()
    document["rules"] = document["rules"][:2]
    engine = DeclarativeRuleEngine(ScenarioDefinitionV2.model_validate(document))

    with pytest.raises(RuleEngineError) as caught:
        engine.evaluate(_state(), _context(1))

    assert caught.value.code == "RULE_RESOLUTION_NOT_FOUND"


@pytest.mark.parametrize(
    ("dosage", "code"),
    [(6, "RULE_PARAMETER_RANGE_INVALID"), (True, "RULE_PARAMETER_TYPE_INVALID")],
)
def test_engine_validates_versioned_parameter_schema(dosage: Any, code: str) -> None:
    engine = DeclarativeRuleEngine(ScenarioDefinitionV2.model_validate(_document()))
    context = ActionRuleContext("treat_patient", "patient_one", {"dosage": dosage})

    with pytest.raises(RuleEngineError) as caught:
        engine.evaluate(_state(), context)

    assert caught.value.code == code
