from app.agent.generic import GenericGoalResolver
from app.domain.formal_goal import FormalGoalSourceKind
from app.domain.scenario_v2 import ObjectiveRequirementKind
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST


def test_current_builtin_preserves_stable_keys_and_player_names() -> None:
    actions = {item.key: item.name for item in LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.actions}
    resources = {
        item.key: item.name for item in LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.world.resources
    }

    assert LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.metadata.key == (
        "linjiang_infrastructure_recovery_v2_0"
    )
    assert actions
    assert all(actions)
    assert all(resources)
    assert all(name for name in actions.values())
    assert all(name for name in resources.values())


def test_author_content_is_not_implicitly_translated() -> None:
    custom = LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.model_copy(
        update={
            "metadata": LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.metadata.model_copy(
                update={"key": "custom_story", "name": "My authored title"}
            )
        }
    )

    assert custom.metadata.key == "custom_story"
    assert custom.metadata.name == "My authored title"


def test_linjiang_goal_aliases_resolve_declaratively() -> None:
    resolver = GenericGoalResolver()

    for goal in (
        "Restore east emergency power",
        "east_emergency_power_network",
    ):
        resolution = resolver.resolve(goal, LINJIANG_V2_TEST)

        assert resolution.status == "RESOLVED"
        assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
        assert resolution.objective_keys == ()
        assert len(resolution.dynamic_requirements) == 1
        assert resolution.dynamic_requirements[0].kind == ObjectiveRequirementKind.DERIVED_STATE
        assert resolution.dynamic_requirements[0].derived_key == ("east_emergency_power_network")


def test_linjiang_final_goal_vocabulary_has_five_derived_states_and_task1_fact() -> None:
    resolver = GenericGoalResolver()
    assert {item.key for item in LINJIANG_V2_TEST.derived_states} == {
        "east_emergency_power_network",
        "east_emergency_water_supply",
        "north_basic_engineering_support",
        "citywide_sustained_emergency_support",
        "southeast_sustained_emergency_generation",
    }

    task1 = next(
        item
        for item in LINJIANG_V2_TEST.objectives
        if item.key == "restore_central_communication_capability"
    )
    task1_requirement = task1.completion_requirements[0]
    assert task1_requirement.kind == ObjectiveRequirementKind.FACT
    assert task1_requirement.node_key == "central_telecom_hub"
    assert task1_requirement.fact_key == "operational"
    assert task1_requirement.accepted_values == (True,)
    assert LINJIANG_V2_TEST.goal_resolution.world_goal_state_catalog is True
    assert task1.key not in task1.goal_aliases
    for goal in (task1.name, *task1.goal_aliases):
        resolution = resolver.resolve(goal, LINJIANG_V2_TEST)
        assert resolution.status == "RESOLVED"
        assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
        assert resolution.objective_keys == ()
        assert len(resolution.dynamic_requirements) == 1
        assert resolution.dynamic_requirements[0].kind == ObjectiveRequirementKind.FACT
        assert resolution.dynamic_requirements[0].node_key == "central_telecom_hub"
        assert resolution.dynamic_requirements[0].fact_key == "operational"
        assert resolution.dynamic_requirements[0].accepted_values == (True,)
        assert resolution.provider_observation is not None
        assert resolution.provider_observation["stage"] == "WORLD_GOAL_STATE_CATALOG"

    assert resolver.resolve(task1.key, LINJIANG_V2_TEST).status == "UNSUPPORTED"

    derived_objectives = [
        item
        for item in LINJIANG_V2_TEST.objectives
        if item.completion_requirements[0].kind == ObjectiveRequirementKind.DERIVED_STATE
    ]
    assert len(derived_objectives) == 5

    for objective in derived_objectives:
        requirement = objective.completion_requirements[0]
        assert requirement.derived_key is not None
        state = LINJIANG_V2_TEST.derived_state_definitions[requirement.derived_key]
        for goal in (state.key, objective.name, *objective.goal_aliases):
            resolution = resolver.resolve(goal, LINJIANG_V2_TEST)

            assert resolution.status == "RESOLVED"
            assert resolution.source == FormalGoalSourceKind.AD_HOC_DYNAMIC.value
            assert resolution.objective_keys == ()
            assert resolution.dynamic_requirements[0].derived_key == state.key
            assert resolution.provider_observation is not None
            assert resolution.provider_observation["stage"] == "DERIVED_GOAL_CATALOG"


def test_linjiang_unmatched_goal_does_not_use_authored_catalog_fallback() -> None:
    resolver = GenericGoalResolver()
    resolution = resolver.resolve(
        "Please restore the central communication network.",
        LINJIANG_V2_TEST,
    )

    assert LINJIANG_V2_TEST.goal_resolution.allow_llm_fallback is True
    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()


def test_legacy_objective_catalog_still_routes_predefined() -> None:
    resolution = GenericGoalResolver().resolve("stabilize_patient", GENERIC_TEST)

    assert resolution.status == "RESOLVED"
    assert resolution.source == "DETERMINISTIC"
    assert resolution.objective_keys == ("stabilize_patient",)


def test_migrated_objective_key_does_not_reactivate_the_legacy_bridge() -> None:
    resolution = GenericGoalResolver().resolve(
        "establish_citywide_sustained_emergency_support",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()


def test_linjiang_unrelated_goal_remains_unsupported() -> None:
    resolution = GenericGoalResolver().resolve(
        "恢复临江市机场运行",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()
