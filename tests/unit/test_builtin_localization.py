from app.agent.generic import GenericGoalResolver
from app.scenarios.builtin import (
    LINJIANG_INFRASTRUCTURE_RECOVERY_V1,
    MEDICAL_EMERGENCY_V2,
    STARFIRE_V2,
)


def test_starfire_player_display_content_is_localized_without_key_changes() -> None:
    actions = {item.key: item.name for item in STARFIRE_V2.actions}
    resources = {item.key: item.name for item in STARFIRE_V2.world.resources}
    actors = {item.key: item.name for item in STARFIRE_V2.actors.actor_profiles}

    assert STARFIRE_V2.metadata.key == "starfire_command"
    assert actions["clear_valley"] == "确保北部山谷安全"
    assert actions["disrupt_supply"] == "破坏敌军补给"
    assert resources == {"soldiers": "士兵", "food": "粮食", "gold": "金币", "morale": "士气"}
    assert actors == {"shen_ce": "沈策", "han_lie": "韩烈", "lu_ning": "陆宁"}


def test_medical_player_display_content_is_localized_without_key_changes() -> None:
    actions = {item.key: item.name for item in MEDICAL_EMERGENCY_V2.actions}
    objectives = {item.key: item.name for item in MEDICAL_EMERGENCY_V2.objectives}

    assert MEDICAL_EMERGENCY_V2.metadata.key == "medical_emergency"
    assert actions == {"diagnose_patient": "诊断患者", "treat_patient": "治疗患者"}
    assert objectives == {"diagnose_patient": "诊断患者", "stabilize_patient": "稳定患者病情"}


def test_author_content_is_not_implicitly_translated() -> None:
    custom = STARFIRE_V2.model_copy(
        update={
            "metadata": STARFIRE_V2.metadata.model_copy(
                update={"key": "custom_story", "name": "My authored title"}
            )
        }
    )

    assert custom.metadata.key == "custom_story"
    assert custom.metadata.name == "My authored title"


def test_starfire_authored_alias_resolves_without_runtime_hardcode() -> None:
    resolution = GenericGoalResolver().resolve("打开北部贸易路线", STARFIRE_V2)

    assert resolution.status == "RESOLVED"
    assert resolution.objective_keys == ("open_northern_trade_route",)


def test_linjiang_goal_aliases_resolve_declaratively() -> None:
    resolver = GenericGoalResolver()

    for goal in (
        "恢复中央医院应急供电",
        "恢复中央医院的应急供电",
        "恢复中央医院应急电力",
        "Restore emergency power to Central Hospital.",
    ):
        resolution = resolver.resolve(goal, LINJIANG_INFRASTRUCTURE_RECOVERY_V1)

        assert resolution.status == "RESOLVED"
        assert resolution.objective_key == "restore_central_hospital_emergency_power"
        assert resolution.objective_keys == ("restore_central_hospital_emergency_power",)


def test_linjiang_unmatched_goal_can_use_generic_provider_fallback() -> None:
    resolver = GenericGoalResolver(
        selector=lambda _goal, _objectives: "restore_central_hospital_emergency_power"
    )

    resolution = resolver.resolve(
        "请把中央医院的应急电源恢复起来",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V1,
    )

    assert LINJIANG_INFRASTRUCTURE_RECOVERY_V1.goal_resolution.allow_llm_fallback is True
    assert resolution.status == "RESOLVED"
    assert resolution.source == "MODEL_VALIDATED"
    assert resolution.objective_keys == ("restore_central_hospital_emergency_power",)


def test_linjiang_unrelated_goal_remains_unsupported() -> None:
    resolution = GenericGoalResolver().resolve(
        "恢复临江市机场运行",
        LINJIANG_INFRASTRUCTURE_RECOVERY_V1,
    )

    assert resolution.status == "UNSUPPORTED"
    assert resolution.objective_keys == ()


def test_existing_scenario_goal_aliases_still_resolve() -> None:
    resolver = GenericGoalResolver()

    starfire = resolver.resolve("打开北部贸易路线", STARFIRE_V2)
    medical = resolver.resolve("稳定患者病情", MEDICAL_EMERGENCY_V2)

    assert starfire.status == "RESOLVED"
    assert starfire.objective_keys == ("open_northern_trade_route",)
    assert medical.status == "RESOLVED"
    assert medical.objective_keys == ("stabilize_patient",)
