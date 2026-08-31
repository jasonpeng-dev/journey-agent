from app.domain.scenario_v2 import ObjectiveRequirementV2, ScenarioDefinitionV2
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
