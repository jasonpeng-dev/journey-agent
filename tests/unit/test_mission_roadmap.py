from app.domain.scenario_v2 import ScenarioDefinitionV2
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
