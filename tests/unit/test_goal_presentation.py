# ruff: noqa: RUF001
from app.agent.generic import GenericGoalResolution
from app.services.goal_presentation import SYSTEM_FAILURE_TEXT, present_failed_goal


def test_failure_presenter_uses_typed_family_and_not_provider_prompt() -> None:
    resolution = GenericGoalResolution(
        "NEEDS_CLARIFICATION",
        clarification_prompt="ambiguous source amount actor internal prompt",
        source="FAMILY_AMBIGUOUS",
        provider_observation={
            "frozen_family": "AMBIGUOUS",
            "rejection_code": "FAMILY_AMBIGUOUS",
        },
    )
    text = present_failed_goal(resolution, "repair the station")
    assert text == "无法确定你希望达成一个状态，还是执行一个具体操作，请更明确地描述目标。"
    assert "internal" not in text


def test_failure_presenter_quotes_only_trusted_raw_surface() -> None:
    observation: dict[str, object] = {
        "frozen_family": "OPERATION",
        "rejection_code": "EXPLICIT_CONSTRAINT_UNRESOLVED",
        "allowed_clarification_fields": ["resource"],
        "explicit_role_evidence": {
            "resource": {
                "status": "UNRESOLVED",
                "surface": "部件",
                "ref_type": "RESOURCE",
            }
        },
    }
    trusted = present_failed_goal(
        GenericGoalResolution("NEEDS_CLARIFICATION", provider_observation=observation),
        "运输部件",
    )
    untrusted = present_failed_goal(
        GenericGoalResolution("NEEDS_CLARIFICATION", provider_observation=observation),
        "transport supplies",
    )
    assert "「部件」" in trusted
    assert "「部件」" not in untrusted


def test_optional_not_specified_actor_is_never_requested() -> None:
    resolution = GenericGoalResolution(
        "NEEDS_CLARIFICATION",
        source="EXPLICIT_CONSTRAINT_UNRESOLVED",
        provider_observation={
            "frozen_family": "OPERATION",
            "rejection_code": "EXPLICIT_CONSTRAINT_UNRESOLVED",
            "allowed_clarification_fields": ["target"],
            "explicit_role_evidence": {
                "actor": {"status": "NOT_SPECIFIED", "surface": None},
                "target": {"status": "UNRESOLVED", "surface": "station"},
            },
        },
    )
    text = present_failed_goal(resolution, "repair station")
    assert "执行者" not in text
    assert "目标对象" in text


def test_system_failure_is_fixed_and_player_safe() -> None:
    assert SYSTEM_FAILURE_TEXT == "目标解析暂时失败，请重新解析。"
    assert "Provider" not in SYSTEM_FAILURE_TEXT
