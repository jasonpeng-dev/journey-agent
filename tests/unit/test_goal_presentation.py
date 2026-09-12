# ruff: noqa: RUF001
from app.agent.generic import GenericGoalResolution
from app.domain.formal_goal import (
    AdHocActionCompletedRequirementCandidateV1,
    AdHocDerivedStateRequirementCandidateV1,
    AdHocFactRequirementCandidateV1,
    AdHocResourceAtLeastRequirementCandidateV1,
    compile_ad_hoc_dynamic_goal,
    compile_ad_hoc_dynamic_goal_v2,
    compile_predefined_formal_goal,
)
from app.scenarios.builtin import require_builtin_v2_version
from app.scenarios.versions import ScenarioVersionRepository
from app.services.goal_presentation import (
    SYSTEM_FAILURE_TEXT,
    present_failed_goal,
    present_resolved_goal,
)
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST


def test_success_presenter_is_deterministic_and_uses_public_names(session) -> None:  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, GENERIC_TEST)
    snapshot = ScenarioVersionRepository(session).load(version.id)
    definition = snapshot.definition
    objective = definition.objective_definitions["stabilize_patient"]
    contracts = (
        compile_predefined_formal_goal(snapshot, (objective,)),
        compile_ad_hoc_dynamic_goal(
            snapshot,
            (
                AdHocFactRequirementCandidateV1(
                    kind="FACT",
                    node_key="patient_one",
                    fact_key="stable",
                    accepted_values=(True,),
                ),
            ),
        ),
        compile_ad_hoc_dynamic_goal_v2(
            snapshot,
            (
                AdHocActionCompletedRequirementCandidateV1(
                    kind="ACTION_COMPLETED",
                    action_key="diagnose_patient",
                    target_key="patient_one",
                ),
            ),
        ),
    )

    texts = tuple(present_resolved_goal(contract, definition) for contract in contracts)

    assert texts == tuple(present_resolved_goal(contract, definition) for contract in contracts)
    assert objective.name in texts[0]
    assert definition.world.node("patient_one").name in texts[1]
    assert definition.world.node("patient_one").fact("stable").name in texts[1]
    assert (
        next(item.name for item in definition.actions if item.key == "diagnose_patient")
        in texts[2]
    )
    assert all("patient_one" not in text for text in texts)


def test_success_presenter_covers_resource_and_derived_state(session) -> None:  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, LINJIANG_V2_TEST)
    snapshot = ScenarioVersionRepository(session).load(version.id)
    definition = snapshot.definition
    derived = definition.derived_state_definitions["north_basic_engineering_support"]
    contract = compile_ad_hoc_dynamic_goal(
        snapshot,
        (
            AdHocResourceAtLeastRequirementCandidateV1(
                kind="RESOURCE_AT_LEAST",
                region_key="north_industrial_district",
                resource_key="general_engineering_parts",
                minimum=30,
            ),
            AdHocDerivedStateRequirementCandidateV1(
                kind="DERIVED_STATE",
                derived_key="north_basic_engineering_support",
                accepted_values=(derived.available_value,),
            ),
        ),
    )

    text = present_resolved_goal(contract, definition)

    assert definition.world.node("north_industrial_district").name in text
    assert next(
        item.name for item in definition.world.resources if item.key == "general_engineering_parts"
    ) in text
    assert derived.name in text
    assert "north_industrial_district" not in text
    assert "general_engineering_parts" not in text


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
