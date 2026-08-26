import pytest

from app.agent.generic import GenericGoalResolver
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
from tests.scenario_fixtures import LINJIANG_V1_TEST

pytestmark = pytest.mark.legacy_scenario


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
        "恢复中央医院应急供电",
        "恢复中央医院的应急供电",
        "恢复中央医院应急电力",
        "Restore emergency power to Central Hospital.",
    ):
        resolution = resolver.resolve(goal, LINJIANG_V1_TEST)

        assert resolution.status == "RESOLVED"
        assert resolution.objective_key == "restore_central_hospital_emergency_power"
        assert resolution.objective_keys == ("restore_central_hospital_emergency_power",)


def test_linjiang_unmatched_goal_can_use_generic_provider_fallback() -> None:
    resolver = GenericGoalResolver(
        selector=lambda _goal, _objectives: "restore_central_hospital_emergency_power"
    )

    resolution = resolver.resolve(
        "请把中央医院的应急电源恢复起来",
        LINJIANG_V1_TEST,
    )

    assert LINJIANG_V1_TEST.goal_resolution.allow_llm_fallback is True
    assert resolution.status == "RESOLVED"
    assert resolution.source == "MODEL_VALIDATED"
    assert resolution.objective_keys == ("restore_central_hospital_emergency_power",)


def test_linjiang_unrelated_goal_remains_unsupported() -> None:
    resolution = GenericGoalResolver().resolve(
        "恢复临江市机场运行",
        LINJIANG_V1_TEST,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()
