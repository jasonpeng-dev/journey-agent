from app.domain.presentation import PresentationActorField
from app.services.presentation_resolver import resolve_presentation_profile


def test_actor_profile_is_limited_to_safe_ordered_fields() -> None:
    compact = resolve_presentation_profile({"schema_version": 1, "template": "compact"})
    detailed = resolve_presentation_profile({"schema_version": 1, "template": "detailed"})
    limited = resolve_presentation_profile(
        {
            "schema_version": 1,
            "template": "detailed",
            "actor_team": {
                "visible_fields": ["NAME", "STATUS"],
                "field_order": ["STATUS", "ROLE", "NAME"],
            },
        }
    )

    assert compact.actor_fields == (
        PresentationActorField.NAME,
        PresentationActorField.ROLE,
    )
    assert PresentationActorField.TASK in detailed.actor_fields
    assert [item.value for item in limited.actor_fields] == ["STATUS", "NAME"]


def test_goal_execution_profile_controls_detail_without_changing_truth_contract() -> None:
    resolved = resolve_presentation_profile(
        {
            "schema_version": 1,
            "template": "compact",
            "goal_execution": {
                "roadmap_detail": "CAUSALITY",
                "plan_default": "FULL",
                "timeline_density": "DETAILED",
            },
        }
    )

    assert resolved.roadmap_detail.value == "CAUSALITY"
    assert resolved.plan_default.value == "FULL"
    assert resolved.timeline_density.value == "DETAILED"
    assert "knowledge" not in resolved.public_document()
    assert "truth" not in resolved.public_document()
