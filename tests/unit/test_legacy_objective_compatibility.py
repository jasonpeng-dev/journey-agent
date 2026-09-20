from datetime import UTC, datetime
from uuid import uuid4

from app.domain.formal_goal import FormalGoalSourceKind, compile_predefined_formal_goal
from app.domain.scenario import ScenarioVersionSnapshot
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.scenarios.serialization import scenario_content_hash
from app.services.mission_roadmap import MissionRoadmapProjector, MissionRoadmapStageStatus
from tests.scenario_fixtures import LINJIANG_CURRENT_TEST, LINJIANG_LEGACY_OBJECTIVES_TEST


def _legacy_snapshot() -> ScenarioVersionSnapshot:
    definition = LINJIANG_LEGACY_OBJECTIVES_TEST
    payload = definition.model_dump(mode="json")
    return ScenarioVersionSnapshot(
        id=uuid4(),
        scenario_id=uuid4(),
        version_number=13,
        schema_version=2,
        content_hash=scenario_content_hash(payload),
        published_at=datetime.now(UTC),
        definition=definition,
    )


def test_legacy_linjiang_objectives_deserialize_and_compile_predefined_contract() -> None:
    definition = LINJIANG_LEGACY_OBJECTIVES_TEST
    reloaded = ScenarioDefinitionV2.model_validate(definition.model_dump(mode="json"))
    objective = reloaded.objective_definitions["restore_central_communication_capability"]

    contract = compile_predefined_formal_goal(_legacy_snapshot(), (objective,))

    assert len(reloaded.objectives) == 6
    assert objective.planning_guidance == (
        "优先恢复中央通信核心；遵循已知的地点、通道和资源条件。"  # noqa: RUF001
    )
    assert contract.source_kind == FormalGoalSourceKind.PREDEFINED
    assert contract.predefined_objectives[0].objective_key == objective.key
    assert contract.completion_requirements[0].requirement.fact_ref == (
        "central_telecom_hub",
        "operational",
    )


def test_legacy_objective_roadmap_and_completion_remain_available() -> None:
    definition = LINJIANG_LEGACY_OBJECTIVES_TEST
    objective_key = "restore_central_communication_capability"

    roadmap = MissionRoadmapProjector().project(
        definition,
        (objective_key,),
        {("central_telecom_hub", "operational"): True},
    )

    stage = next(item for item in roadmap.stages if item.objective_key == objective_key)
    assert stage.status == MissionRoadmapStageStatus.COMPLETED
    assert stage.requirements[0]["fact_key"] == "operational"


def test_retired_objective_vocabulary_is_owned_by_current_goal_metadata() -> None:
    current_terms = {
        term.casefold()
        for node in LINJIANG_CURRENT_TEST.world.nodes
        for fact in node.facts
        if fact.goal_addressable
        for term in (fact.name, *fact.goal_aliases, *fact.goal_examples)
    } | {
        term.casefold()
        for state in LINJIANG_CURRENT_TEST.derived_states
        if state.goal_addressable
        for term in (state.name, *state.goal_aliases, *state.goal_examples)
    }
    legacy_terms = {
        term.casefold()
        for objective in LINJIANG_LEGACY_OBJECTIVES_TEST.objectives
        for term in (objective.name, *objective.goal_aliases, *objective.goal_examples)
    }

    assert legacy_terms <= current_terms
