from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.scenarios.builtin import MEDICAL_EMERGENCY_V2, STARFIRE_V2
from app.services.mission_roadmap import (
    MissionRoadmapProjector,
    MissionRoadmapStageStatus,
)


def _initial_knowledge(
    scenario: ScenarioDefinitionV2,
) -> dict[tuple[str, str], str | int | bool]:
    return {
        (node.key, fact.key): fact.initial_value
        for node in scenario.world.nodes
        for fact in node.facts
        if fact.initial_visibility.value == "KNOWN"
    }


def test_starfire_high_level_objective_has_future_non_executable_stages() -> None:
    scope = ("open_northern_trade_route",)
    roadmap = MissionRoadmapProjector().project(
        STARFIRE_V2,
        scope,
        _initial_knowledge(STARFIRE_V2),
    )

    assert scope == ("open_northern_trade_route",)
    assert [stage.objective_key for stage in roadmap.stages] == [
        "secure_northern_valley",
        "restore_starfire_outpost",
        None,
        "open_northern_trade_route",
    ]
    assert roadmap.stages[0].status == MissionRoadmapStageStatus.CURRENT
    assert all(stage.status != MissionRoadmapStageStatus.COMPLETED for stage in roadmap.stages)
    assert "ambush" not in " ".join(stage.name.lower() for stage in roadmap.stages)


def test_roadmap_updates_from_knowledge_without_changing_objective_scope() -> None:
    scope = ("open_northern_trade_route",)
    knowledge = _initial_knowledge(STARFIRE_V2)
    knowledge.update(
        {
            ("northern_valley", "valley_security"): "SAFE",
            ("starfire_outpost", "outpost_status"): "RESTORED",
            ("north_village", "village_support"): "GUIDE",
        }
    )

    roadmap = MissionRoadmapProjector().project(STARFIRE_V2, scope, knowledge)

    assert scope == ("open_northern_trade_route",)
    assert [stage.status for stage in roadmap.stages] == [
        MissionRoadmapStageStatus.COMPLETED,
        MissionRoadmapStageStatus.COMPLETED,
        MissionRoadmapStageStatus.COMPLETED,
        MissionRoadmapStageStatus.CURRENT,
    ]


def test_medical_uses_the_same_generic_roadmap_projection() -> None:
    roadmap = MissionRoadmapProjector().project(
        MEDICAL_EMERGENCY_V2,
        ("stabilize_patient",),
        _initial_knowledge(MEDICAL_EMERGENCY_V2),
    )

    assert [stage.objective_key for stage in roadmap.stages] == [
        "diagnose_patient",
        "stabilize_patient",
    ]
    assert roadmap.stages[0].status == MissionRoadmapStageStatus.CURRENT
    assert roadmap.stages[1].status == MissionRoadmapStageStatus.PENDING
