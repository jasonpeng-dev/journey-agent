from copy import deepcopy

import pytest
from sqlalchemy import select

from app.agent.authority import evaluate_authority
from app.agent.generic import _validate_explicit_actor_action_compatibility
from app.agent.provider import DynamicGoalGroundedOperation
from app.domain.action_invocation import canonical_action_invocation_contract
from app.domain.enums import AuthorityOutcome
from app.domain.formal_goal import FormalGoalError
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import AgentPlan, AgentStep, GameInstanceActor
from app.services.generic_actions import GenericActionError, GenericActionService
from tests.repair_parity import (
    LEGACY_REPAIR_RULE_COUNT,
    LEGACY_REPAIR_RULE_SEMANTIC_HASH,
    repair_rule_semantic_hash,
)
from tests.scenario_fixtures import LINJIANG_V2_TEST
from tests.unit.test_generic_agent import _agent
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _target_role_definition() -> ScenarioDefinitionV2:
    document = deepcopy(_contract_scenario_document())
    document["actions"][0]["parameters"][0]["required"] = False
    document["actions"][0]["parameters"][0]["default"] = 2
    document["actors"]["roles"].append(
        {
            "key": "specialist",
            "name": "Specialist",
            "capabilities": ["EXECUTE_ACTION"],
        }
    )
    document["actors"]["actor_profiles"].append(
        {
            "key": "specialist_one",
            "name": "Specialist One",
            "role_key": "specialist",
            "persona": "A target-specific treatment specialist.",
            "initial_node_key": "triage_room",
            "allowed_action_keys": ["treat_patient"],
        }
    )
    document["actions"][0]["target_node_type_keys"] = ["patient"]
    document["actions"][0]["target_actor_roles"] = [
        {
            "target_key": "patient_one",
            "required_actor_role_key": "specialist",
        }
    ]
    return ScenarioDefinitionV2.model_validate(document)


def test_repair_parity_inventory_freezes_all_legacy_rules() -> None:
    assert repair_rule_semantic_hash(LINJIANG_V2_TEST) == (
        LEGACY_REPAIR_RULE_COUNT,
        LEGACY_REPAIR_RULE_SEMANTIC_HASH,
    )


def test_target_actor_role_schema_roundtrip_and_action_fallback() -> None:
    definition = _target_role_definition()
    action = next(item for item in definition.actions if item.key == "treat_patient")

    assert action.required_actor_role_for_target("patient_one") == "specialist"
    assert action.required_actor_role_for_target("another_target") is None
    roundtrip = ScenarioDefinitionV2.model_validate(definition.model_dump(mode="json"))
    roundtrip_action = next(item for item in roundtrip.actions if item.key == "treat_patient")
    assert roundtrip_action.target_actor_roles == action.target_actor_roles
    contract = canonical_action_invocation_contract(action)
    assert contract["target_actor_roles"] == [
        {
            "target_key": "patient_one",
            "required_actor_role_key": "specialist",
        }
    ]


def test_target_actor_role_schema_rejects_invalid_references() -> None:
    document = deepcopy(_contract_scenario_document())
    document["actions"][0]["target_actor_roles"] = [
        {"target_key": "missing", "required_actor_role_key": "clinician"}
    ]
    with pytest.raises(ValueError):
        ScenarioDefinitionV2.model_validate(document)


def test_authority_prefers_target_specific_role_without_changing_action_role() -> None:
    definition = _target_role_definition()
    action = next(item for item in definition.actions if item.key == "treat_patient")
    doctor = GameInstanceActor(
        actor_key="doctor_lee",
        role_key="clinician",
        status="ACTIVE",
        allowed_action_keys=["treat_patient"],
        capabilities=["PLAN", "EXECUTE_ACTION", "INSPECT_STATE"],
        authority_policy={"autonomous_limits": [], "approval_required_values": []},
    )
    specialist = GameInstanceActor(
        actor_key="specialist_one",
        role_key="specialist",
        status="ACTIVE",
        allowed_action_keys=["treat_patient"],
        capabilities=["EXECUTE_ACTION"],
        authority_policy={"autonomous_limits": [], "approval_required_values": []},
    )

    denied = evaluate_authority(doctor, action, {"dosage": 2}, target_key="patient_one")
    allowed = evaluate_authority(
        specialist,
        action,
        {"dosage": 2},
        target_key="patient_one",
    )

    assert denied.outcome == AuthorityOutcome.DENY
    assert denied.reason_code == "ACTOR_ROLE_MISSING"
    assert allowed.outcome == AuthorityOutcome.ALLOW


def test_action_level_role_fallback_remains_unchanged() -> None:
    document = deepcopy(_contract_scenario_document())
    document["actions"][0]["required_actor_role_key"] = "clinician"
    definition = ScenarioDefinitionV2.model_validate(document)
    action = next(item for item in definition.actions if item.key == "treat_patient")
    doctor = GameInstanceActor(
        actor_key="doctor_lee",
        role_key="clinician",
        status="ACTIVE",
        allowed_action_keys=["treat_patient"],
        capabilities=["PLAN", "EXECUTE_ACTION", "INSPECT_STATE"],
        authority_policy={"autonomous_limits": [], "approval_required_values": []},
    )

    assert action.required_actor_role_for_target("patient_one") == "clinician"
    assert (
        evaluate_authority(doctor, action, {"dosage": 2}, target_key="patient_one").outcome
        == AuthorityOutcome.ALLOW
    )


def test_resolver_explicit_actor_compatibility_uses_target_role() -> None:
    definition = _target_role_definition()
    action = next(item for item in definition.actions if item.key == "treat_patient")

    _validate_explicit_actor_action_compatibility(
        definition,
        action,
        DynamicGoalGroundedOperation(
            action_key=action.key,
            actor_key=None,
            target_key="patient_one",
        ),
    )
    _validate_explicit_actor_action_compatibility(
        definition,
        action,
        DynamicGoalGroundedOperation(
            action_key=action.key,
            actor_key="specialist_one",
            target_key="patient_one",
        ),
    )
    with pytest.raises(FormalGoalError) as caught:
        _validate_explicit_actor_action_compatibility(
            definition,
            action,
            DynamicGoalGroundedOperation(
                action_key=action.key,
                actor_key="doctor_lee",
                target_key="patient_one",
            ),
        )
    assert caught.value.code == "EXPLICIT_ACTOR_ACTION_CONFLICT"


def test_planner_and_validator_select_only_target_role_actor(session) -> None:  # type: ignore[no-untyped-def]
    definition = _target_role_definition()
    agent, runtime = _agent(session, definition=definition)

    task = agent.create_task(runtime.session, "stabilize the patient")
    plan = session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id))
    assert plan is not None
    step = session.scalar(select(AgentStep).where(AgentStep.plan_id == plan.id))
    assert step is not None
    assert step.assigned_actor_key == "specialist_one"

    doctor = session.get(GameInstanceActor, (runtime.instance.id, "doctor_lee"))
    specialist = session.get(GameInstanceActor, (runtime.instance.id, "specialist_one"))
    assert doctor is not None and specialist is not None
    action = next(item for item in definition.actions if item.key == "treat_patient")
    assert not agent._validate_planning_action(definition, action, doctor, "patient_one")
    assert agent._validate_planning_action(definition, action, specialist, "patient_one")

    with pytest.raises(GenericActionError) as caught:
        GenericActionService(session, agent.scope).execute_action(
            actor_key="doctor_lee",
            action_key="treat_patient",
            target_key="patient_one",
            parameters={"dosage": 2},
            idempotency_key="wrong-target-role",
        )
    assert caught.value.code == "ACTOR_ROLE_MISSING"
