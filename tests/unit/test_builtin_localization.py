from app.agent.generic import GenericGoalResolver
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
from tests.scenario_fixtures import LINJIANG_V2_TEST


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
        "Restore central communications",
        "restore_central_communication_capability",
    ):
        resolution = resolver.resolve(goal, LINJIANG_V2_TEST)

        assert resolution.status == "RESOLVED"
        assert resolution.objective_key == "restore_central_communication_capability"
        assert resolution.objective_keys == ("restore_central_communication_capability",)


def test_linjiang_unmatched_goal_does_not_use_authored_catalog_fallback() -> None:
    resolver = GenericGoalResolver()
    resolution = resolver.resolve(
        "Please restore the central communication network.",
        LINJIANG_V2_TEST,
    )

    assert LINJIANG_V2_TEST.goal_resolution.allow_llm_fallback is True
    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()


def test_linjiang_unrelated_goal_remains_unsupported() -> None:
    resolution = GenericGoalResolver().resolve(
        "恢复临江市机场运行",
        LINJIANG_V2_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()
