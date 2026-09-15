from app.domain.scenario_v2 import (
    DerivedStateDefinitionV2,
    ObjectiveRequirementV2,
    ScenarioDefinitionV2,
)
from app.services.mission_roadmap import (
    MissionRoadmapProjector,
    MissionRoadmapStageStatus,
)
from tests.scenario_fixtures import GENERIC_TEST


def _initial_knowledge(
    scenario: ScenarioDefinitionV2,
) -> dict[tuple[str, str], str | int | bool]:
    return {
        (node.key, fact.key): fact.initial_value
        for node in scenario.world.nodes
        for fact in node.facts
        if fact.initial_visibility.value == "KNOWN"
    }


def test_generic_scenario_uses_the_same_generic_roadmap_projection() -> None:
    roadmap = MissionRoadmapProjector().project(
        GENERIC_TEST,
        ("stabilize_patient",),
        _initial_knowledge(GENERIC_TEST),
    )

    assert [stage.objective_key for stage in roadmap.stages] == [
        "diagnose_patient",
        "stabilize_patient",
    ]
    assert roadmap.stages[0].status == MissionRoadmapStageStatus.CURRENT
    assert roadmap.stages[1].status == MissionRoadmapStageStatus.PENDING


def test_resource_requirement_is_hidden_until_its_public_gate_opens() -> None:
    requirement = ObjectiveRequirementV2.model_validate(
        {
            "key": "reserve",
            "kind": "RESOURCE_AT_LEAST",
            "region_key": "triage_room",
            "resource_key": "medicine",
            "minimum": 10,
            "description": "Keep a medicine reserve.",
            "knowledge_gate": {
                "node_key": "patient_one",
                "fact_key": "stable",
                "accepted_values": [True],
            },
        }
    )
    objective = GENERIC_TEST.objectives[0].model_copy(
        update={"completion_requirements": (requirement,)}
    )
    scenario = GENERIC_TEST.model_copy(update={"objectives": (objective,)})
    hidden = MissionRoadmapProjector().project(scenario, (objective.key,), {})
    assert hidden.stages[0].requirements == ()

    revealed = MissionRoadmapProjector().project(
        scenario,
        (objective.key,),
        {("patient_one", "stable"): True},
        {"medicine": {"regions": {"triage_room": {"known_available": 7}}}},
    )
    assert revealed.stages[0].requirements[0]["current_known_available"] == 7
    assert revealed.stages[0].status == MissionRoadmapStageStatus.CURRENT


def test_derived_state_roadmap_reveals_only_gated_public_children() -> None:
    derived = DerivedStateDefinitionV2.model_validate(
        {
            "key": "public_capability",
            "name": "Public capability",
            "description": "A public world capability.",
            "value_type": "ENUM",
            "available_value": "AVAILABLE",
            "unavailable_value": "UNAVAILABLE",
            "allowed_values": ["AVAILABLE", "UNAVAILABLE"],
            "goal_addressable": True,
            "dependencies": [
                {
                    "kind": "FACT",
                    "node_key": "patient_one",
                    "fact_key": "stable",
                    "accepted_values": [True],
                    "knowledge_gate": {
                        "node_key": "patient_one",
                        "fact_key": "stable",
                        "accepted_values": [True],
                    },
                }
            ],
        }
    )
    objective = GENERIC_TEST.objectives[0].model_copy(
        update={
            "completion_requirements": (
                ObjectiveRequirementV2.model_validate(
                    {
                        "key": "capability_available",
                        "kind": "DERIVED_STATE",
                        "derived_key": "public_capability",
                        "accepted_values": ["AVAILABLE"],
                        "description": "The public capability is available.",
                    }
                ),
            )
        }
    )
    scenario = GENERIC_TEST.model_copy(
        update={"objectives": (objective,), "derived_states": (derived,)}
    )
    projector = MissionRoadmapProjector()

    hidden = projector.project(
        scenario,
        (objective.key,),
        {},
        known_derived={"public_capability": "UNAVAILABLE"},
    )
    assert [item.get("kind") for item in hidden.stages[0].requirements] == ["DERIVED_STATE"]
    assert all(item.get("node_key") != "patient_one" for item in hidden.stages[0].requirements)

    revealed = projector.project(
        scenario,
        (objective.key,),
        {("patient_one", "stable"): True},
        known_derived={"public_capability": "AVAILABLE"},
    )
    assert [item.get("kind") for item in revealed.stages[0].requirements] == [
        "DERIVED_STATE",
        "FACT",
    ]
    assert revealed.stages[0].requirements[1]["node_key"] == "patient_one"
    assert revealed.stages[0].status == MissionRoadmapStageStatus.COMPLETED
