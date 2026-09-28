from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agent.generic import GenericAgentService, GenericGoalResolution
from app.agent.planning_context import PlanningContextBuilder
from app.domain.formal_goal import AdHocFactRequirementCandidateV1, FormalGoalSourceKind
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import GameInstanceActionTargetKnowledge, Player
from app.scenarios.builtin import require_builtin_v2_version
from app.scenarios.target_applicability_migration import (
    migrate_target_applicability_document,
)
from app.services.game_instances import GameInstanceService
from app.services.generic_game import GenericGameService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.runtime_initialization import RuntimeInitializationService


def _linjiang_document() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "scenarios"
        / "data"
        / "linjiang_infrastructure_recovery_v2_0.yaml"
    )
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_linjiang_repair_profile_migration_is_deterministic_and_maps_central_target() -> None:
    report = migrate_target_applicability_document(_linjiang_document(), apply_to_document=True)

    assert not report.blocked
    assert report.applied
    assert report.facts_removed == 25
    assert report.selectors_migrated == 71
    assert report.semantic_parity
    assert report.document is not None
    assert all(
        fact.get("key") != "repair_profile"
        for node in report.document["world"]["nodes"]
        for fact in node["facts"]
    )
    repair_action = next(
        item for item in report.document["actions"] if item["key"] == "repair_facility"
    )
    central = next(
        item
        for item in repair_action["target_contracts"]
        if item["target_key"] == "central_telecom_hub"
    )
    assert central["target_key"] != "central_communication_core"
    assert central["reveal_on_inspect"] is True


def test_target_applicability_migration_is_idempotent() -> None:
    first = migrate_target_applicability_document(_linjiang_document(), apply_to_document=True)
    assert first.document is not None
    second = migrate_target_applicability_document(first.document, apply_to_document=True)

    assert not second.blocked
    assert second.before_hash == first.after_hash
    assert second.after_hash == first.after_hash
    assert second.facts_removed == 0
    assert second.selectors_migrated == 0
    assert second.semantic_parity


def test_unsafe_selector_shape_blocks_without_mutating_source() -> None:
    document = _linjiang_document()
    rule = next(item for item in document["rules"] if item.get("action_key") == "repair_facility")
    rule["condition"] = {"kind": "ANY", "conditions": [rule["condition"]]}
    input_snapshot = deepcopy(document)

    report = migrate_target_applicability_document(document, apply_to_document=True)

    assert report.blocked
    assert report.unsafe_conditions
    assert document == input_snapshot


def test_action_target_contract_and_rule_applicability_validate_against_target_domain() -> None:
    from tests.unit.test_scenario_definition_v2 import _contract_scenario_document

    document = _contract_scenario_document()
    document["actions"][0]["target_contracts"] = [
        {"target_key": "patient_one", "initial_visibility": "HIDDEN"}
    ]
    document["rules"][0]["applicable_target_keys"] = ["patient_one"]

    definition = ScenarioDefinitionV2.model_validate(document)

    assert definition.actions[0].target_contracts[0].initial_visibility.value == "HIDDEN"
    assert definition.rules[0].applicable_target_keys == ("patient_one",)

    orphan_rule = deepcopy(document)
    orphan_rule["rules"][0]["applicable_target_keys"] = ["triage_room"]
    with pytest.raises(ValueError, match="target contracts"):
        ScenarioDefinitionV2.model_validate(orphan_rule)


def test_duplicate_or_state_applicability_is_rejected() -> None:
    from tests.unit.test_scenario_definition_v2 import _contract_scenario_document

    duplicate = _contract_scenario_document()
    duplicate["actions"][0]["target_contracts"] = [
        {"target_key": "patient_one"},
        {"target_key": "patient_one"},
    ]
    with pytest.raises(ValueError):
        ScenarioDefinitionV2.model_validate(duplicate)

    state_rule = _contract_scenario_document()
    state_rule["rules"] = [
        {
            "key": "state_rule",
            "phase": "RESOLVE",
            "trigger": "STATE",
            "priority": 0,
            "applicable_target_keys": ["patient_one"],
            "effects": [
                {
                    "kind": "SET_NODE_ACCESS",
                    "node": {"kind": "EXPLICIT", "node_key": "patient_one"},
                    "access": "AVAILABLE",
                }
            ],
        }
    ]
    with pytest.raises(ValueError):
        ScenarioDefinitionV2.model_validate(state_rule)


def test_rule_engine_filters_applicability_before_condition_selection() -> None:
    from app.domain.scenario_v2 import ScenarioDefinitionV2
    from app.engine.rules import ActionRuleContext, DeclarativeRuleEngine
    from tests.unit.test_declarative_rule_engine import _document, _state

    document = _document()
    document["rules"].append(
        {
            "key": "patient_only",
            "phase": "RESOLVE",
            "action_key": "treat_patient",
            "priority": 1000,
            "applicable_target_keys": ["patient_one"],
            "effects": [{"kind": "EMIT_OUTCOME", "outcome_code": "COMPLETED"}],
        }
    )
    engine = DeclarativeRuleEngine(ScenarioDefinitionV2.model_validate(document))

    patient = engine.evaluate(
        _state(), ActionRuleContext("treat_patient", "patient_one", {"dosage": 1})
    )
    room = engine.evaluate(
        _state(), ActionRuleContext("treat_patient", "triage_room", {"dosage": 1})
    )

    assert patient.selected_rule_key == "patient_only"
    assert room.selected_rule_key == "standard_treatment"


def test_target_contract_knowledge_reveals_only_after_generic_inspect(session) -> None:
    migrated = migrate_target_applicability_document(_linjiang_document(), apply_to_document=True)
    assert migrated.document is not None
    migrated.document["metadata"]["key"] = "target_contract_knowledge_test"
    migrated.document["metadata"]["name"] = "Target contract Knowledge test"
    migrated.document["world"]["key"] = "target_contract_knowledge_test"
    migrated.document["world"]["name"] = "Target contract Knowledge test"
    definition = ScenarioDefinitionV2.model_validate(migrated.document)
    version = require_builtin_v2_version(session, definition)
    player = Player(name="target-contract-knowledge-test")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="target-contract-knowledge-test",
    )
    scope = GameInstanceService(session).load(runtime.instance.id)
    projection = SharedKnowledgeProjection(session, scope, definition)

    repair_action = next(action for action in definition.actions if action.key == "repair_facility")
    repair_contracts = {item.target_key: item for item in repair_action.target_contracts}
    known_targets = {
        item["target_key"]
        for item in projection.target_knowledge_contracts()
        if item["action_key"] == "repair_facility"
    }
    hidden_targets = set(repair_contracts).difference(known_targets)
    assert known_targets
    assert hidden_targets

    hidden_target = sorted(hidden_targets)[0]
    hidden_row = session.get(
        # The row is initialized from the contract, not from a Fact.
        GameInstanceActionTargetKnowledge,
        (runtime.instance.id, "repair_facility", hidden_target),
    )
    assert hidden_row is not None
    assert hidden_row.visibility == Visibility.HIDDEN

    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "Target contract Knowledge planner parity",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            source=FormalGoalSourceKind.AD_HOC_DYNAMIC.value,
            dynamic_requirements=(
                AdHocFactRequirementCandidateV1(
                    kind="FACT",
                    node_key="central_telecom_hub",
                    fact_key="operational",
                    accepted_values=(True,),
                ),
            ),
        ),
        initialize_plan=False,
    )
    objectives = agent._objectives(task, definition)
    planner = PlanningContextBuilder(session, scope).build_v2(
        definition,
        objectives,
        task=task,
        replan_reason=None,
    )
    assert all(
        not (item.action_key == "repair_facility" and item.target_key == hidden_target)
        for item in planner.target_bindings
    )
    closure = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        objectives,
        task=task,
        replan_reason=None,
    )
    assert all(
        not (item.action_key == "repair_facility" and item.target_key == hidden_target)
        for item in closure.planner_input.target_bindings
    )

    GenericGameService(session, scope).execute(
        actor_key=runtime.session.actor_key,
        action_key="inspect",
        target_node_key=hidden_target,
        parameters={},
    )
    session.flush()
    revealed = SharedKnowledgeProjection(session, scope, definition).target_knowledge_contracts()
    assert hidden_target in {
        item["target_key"] for item in revealed if item["action_key"] == "repair_facility"
    }
