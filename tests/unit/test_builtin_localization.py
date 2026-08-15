from app.agent.generic import GenericGoalResolver
from app.scenarios.builtin import MEDICAL_EMERGENCY_V2, STARFIRE_V2


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
