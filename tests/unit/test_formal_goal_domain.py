from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.formal_goal_projection import formal_goal_planning_objectives
from app.domain.formal_goal import (
    AdHocFactRequirementCandidateV1,
    AdHocGoalCandidateSetV1,
    AdHocResourceAtLeastRequirementCandidateV1,
    FormalGoalError,
    FormalGoalSourceKind,
    compile_ad_hoc_dynamic_goal,
    compile_predefined_formal_goal,
)
from app.domain.runtime_scope import GameInstanceId, PlayerId, RuntimeScope, ScenarioVersionId
from app.domain.scenario import ScenarioVersionSnapshot
from app.domain.scenario_v2 import ObjectiveRequirementKind, ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import GameInstanceFactState
from app.scenarios.serialization import scenario_content_hash
from app.services.formal_goal import FormalGoalCompletionEvaluator
from tests.dynamic_goal_helpers import dynamic_candidate as AdHocGoalRequirementCandidateV1
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _snapshot(document: dict[str, object] | None = None) -> ScenarioVersionSnapshot:
    definition = ScenarioDefinitionV2.model_validate(document or _contract_scenario_document())
    payload = definition.model_dump(mode="json")
    return ScenarioVersionSnapshot(
        id=uuid4(),
        scenario_id=uuid4(),
        version_number=1,
        schema_version=2,
        content_hash=scenario_content_hash(payload),
        published_at=datetime.now(UTC),
        definition=definition,
    )


def _resource_document() -> dict[str, object]:
    document = deepcopy(_contract_scenario_document())
    document["metadata"]["locality"] = {  # type: ignore[index]
        "enabled": True,
        "scoped_resources": True,
        "region_node_type_key": "room",
        "facility_node_type_key": "room",
        "transport_node_type_key": "room",
        "located_in_relation_type_key": "contains",
        "transport_endpoint_relation_type_key": "contains",
    }
    return document


def _integer_fact_document() -> dict[str, object]:
    document = deepcopy(_contract_scenario_document())
    patient = next(
        item
        for item in document["world"]["nodes"]
        if item["key"] == "patient_one"  # type: ignore[index]
    )
    patient["facts"].append(  # type: ignore[index]
        {
            "key": "treatment_count",
            "name": "Treatment Count",
            "value_type": "INTEGER",
            "initial_value": 0,
            "initial_visibility": "KNOWN",
        }
    )
    return document


def test_predefined_compiler_is_exact_and_multi_objective_identity_is_stable() -> None:
    snapshot = _snapshot()
    objective = snapshot.definition.objectives[0]
    contract = compile_predefined_formal_goal(snapshot, (objective,))

    assert contract.source_kind == FormalGoalSourceKind.PREDEFINED
    assert contract.predefined_objectives[0].objective_key == objective.key
    assert contract.completion_requirements[0].identity == (
        f"{objective.key}:{objective.completion_requirements[0].key}"
    )
    assert contract.content_hash == contract.content_hash
    assert "description" not in contract.canonical_json()


def test_predefined_hash_ignores_display_description_but_changes_authoritative_semantics() -> None:
    snapshot = _snapshot()
    objective = snapshot.definition.objectives[0]
    first = compile_predefined_formal_goal(snapshot, (objective,))

    changed_description = objective.completion_requirements[0].model_copy(
        update={"description": "A different player-facing sentence."}
    )
    changed_display = first.model_copy(
        update={
            "completion_requirements": (
                first.completion_requirements[0].model_copy(
                    update={"requirement": changed_description}
                ),
            )
        }
    )
    assert changed_display.content_hash == first.content_hash

    changed_requirement = objective.completion_requirements[0].model_copy(
        update={"accepted_values": (False,)}
    )
    changed_contract = first.model_copy(
        update={
            "completion_requirements": (
                first.completion_requirements[0].model_copy(
                    update={"requirement": changed_requirement}
                ),
            )
        }
    )
    assert changed_contract.content_hash != first.content_hash

    changed_fact = objective.completion_requirements[0].model_copy(
        update={"fact_key": "other_fact"}
    )
    with pytest.raises(FormalGoalError, match="not the immutable definition"):
        compile_predefined_formal_goal(
            snapshot,
            (objective.model_copy(update={"completion_requirements": (changed_fact,)}),),
        )


def test_dynamic_fact_identity_is_backend_owned_and_candidate_key_is_rejected() -> None:
    snapshot = _snapshot()
    candidates = AdHocGoalCandidateSetV1(
        requirements=(
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.FACT,
                node_key="patient_one",
                fact_key="stable",
                accepted_values=(True,),
            ),
        )
    )
    contract = compile_ad_hoc_dynamic_goal(snapshot, candidates)
    item = contract.completion_requirements[0]

    assert item.source_objective_key is None
    assert item.identity.startswith("fact/patient_one/stable/")
    assert item.requirement.key == "dynamic_requirement"

    with pytest.raises(ValidationError):
        AdHocFactRequirementCandidateV1.model_validate(
            {
                "kind": "FACT",
                "node_key": "patient_one",
                "fact_key": "stable",
                "accepted_values": [True],
                "key": "invented_key",
            }
        )


def test_dynamic_fact_values_are_canonicalized_against_exact_fact_type() -> None:
    boolean_contract = compile_ad_hoc_dynamic_goal(
        _snapshot(),
        (
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.FACT,
                node_key="patient_one",
                fact_key="stable",
                accepted_values=("true",),
            ),
        ),
    )
    assert boolean_contract.completion_requirements[0].requirement.accepted_values == (True,)

    integer_contract = compile_ad_hoc_dynamic_goal(
        _snapshot(_integer_fact_document()),
        (
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.FACT,
                node_key="patient_one",
                fact_key="treatment_count",
                accepted_values=("20",),
            ),
        ),
    )
    assert integer_contract.completion_requirements[0].requirement.accepted_values == (20,)


def test_dynamic_fact_value_type_error_exposes_only_safe_type_diagnostics() -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=("yes",),
    )

    with pytest.raises(FormalGoalError) as error:
        compile_ad_hoc_dynamic_goal(_snapshot(), (candidate,))

    assert error.value.code == "FORMAL_GOAL_VALUE_TYPE_INVALID"
    assert error.value.details == {
        "expected_value_type": "BOOLEAN",
        "actual_candidate_json_type": "string",
    }


def test_dynamic_resource_reuses_typed_requirement_and_rejects_gate() -> None:
    snapshot = _snapshot(_resource_document())
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
        region_key="triage_room",
        resource_key="medicine",
        minimum=10,
    )
    contract = compile_ad_hoc_dynamic_goal(snapshot, (candidate,))

    item = contract.completion_requirements[0]
    assert item.requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST
    assert item.requirement.region_key == "triage_room"
    assert item.requirement.minimum == 10

    with pytest.raises(ValidationError):
        AdHocResourceAtLeastRequirementCandidateV1.model_validate(
            {
                "kind": "RESOURCE_AT_LEAST",
                "region_key": "triage_room",
                "resource_key": "medicine",
                "minimum": 10,
                "knowledge_gate": {
                    "node_key": "patient_one",
                    "fact_key": "stable",
                    "accepted_values": [True],
                },
            }
        )


def test_dynamic_planning_projection_keeps_typed_requirements_without_authored_objective() -> None:
    snapshot = _snapshot(_resource_document())
    contract = compile_ad_hoc_dynamic_goal(
        snapshot,
        (
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.FACT,
                node_key="patient_one",
                fact_key="stable",
                accepted_values=(True,),
            ),
            AdHocGoalRequirementCandidateV1(
                kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
                region_key="triage_room",
                resource_key="medicine",
                minimum=10,
            ),
        ),
    )

    projected = formal_goal_planning_objectives(contract, snapshot.definition)

    assert len(projected) == 1
    assert projected[0].key == "dynamic_goal"
    assert {item.kind for item in projected[0].completion_requirements} == {
        ObjectiveRequirementKind.FACT,
        ObjectiveRequirementKind.RESOURCE_AT_LEAST,
    }
    assert len({item.key for item in projected[0].completion_requirements}) == 2
    assert projected[0].prerequisites == ()


def test_dynamic_rejects_unknown_ontology_and_duplicate_semantics() -> None:
    snapshot = _snapshot()
    unknown = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="missing",
        accepted_values=(True,),
    )
    with pytest.raises(FormalGoalError, match="unknown Fact"):
        compile_ad_hoc_dynamic_goal(snapshot, (unknown,))

    duplicate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=(True,),
    )
    with pytest.raises(FormalGoalError, match="unique canonical semantics"):
        compile_ad_hoc_dynamic_goal(snapshot, (duplicate, duplicate))


def test_contract_rejects_duplicate_identity_and_invalid_scenario_proof() -> None:
    snapshot = _snapshot()
    objective = snapshot.definition.objectives[0]
    contract = compile_predefined_formal_goal(snapshot, (objective,))

    payload = contract.model_dump(mode="json")
    payload["completion_requirements"].append(payload["completion_requirements"][0])
    with pytest.raises(ValidationError):
        contract.__class__.model_validate(payload)

    bad_snapshot = snapshot.__class__(
        id=snapshot.id,
        scenario_id=snapshot.scenario_id,
        version_number=snapshot.version_number,
        schema_version=snapshot.schema_version,
        content_hash="0" * 64,
        published_at=snapshot.published_at,
        definition=snapshot.definition,
    )
    with pytest.raises(FormalGoalError) as error:
        compile_predefined_formal_goal(bad_snapshot, (objective,))
    assert error.value.code == "FORMAL_GOAL_SCENARIO_HASH_MISMATCH"


def test_formal_completion_evaluator_reuses_authoritative_truth_semantics() -> None:
    snapshot = _snapshot()
    objective = snapshot.definition.objectives[0]
    contract = compile_predefined_formal_goal(snapshot, (objective,))
    scope = RuntimeScope(GameInstanceId(uuid4()), PlayerId(uuid4()), ScenarioVersionId(snapshot.id))
    fact = GameInstanceFactState(
        game_instance_id=scope.game_instance_id,
        node_key="patient_one",
        fact_key="stable",
        truth_value=True,
        visibility=Visibility.HIDDEN,
    )

    class _FactSession:
        def get(self, _model, _identity):  # type: ignore[no-untyped-def]
            return fact

    evaluation = FormalGoalCompletionEvaluator(_FactSession(), scope).evaluate(  # type: ignore[arg-type]
        contract,
        definition=snapshot.definition,
    )

    assert evaluation.completed is True
    assert evaluation.player_visible_completed is False
    assert evaluation.requirements[0].value is True
    assert evaluation.requirements[0].satisfied is True
    assert evaluation.requirements[0].player_visible_satisfied is False
