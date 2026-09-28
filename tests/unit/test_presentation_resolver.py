from app.domain.presentation import PresentationKnowledgeLevel
from app.domain.scenario_v2 import FactDefinitionV2, ResourceDefinitionV2
from app.domain.world import FactValueType, Visibility
from app.services.presentation_resolver import resolve_presentation_profile


def test_product_templates_resolve_bounded_defaults_without_scenario_knowledge() -> None:
    compact = resolve_presentation_profile({"schema_version": 1, "template": "compact"})
    standard = resolve_presentation_profile({"schema_version": 1, "template": "standard"})
    detailed = resolve_presentation_profile({"schema_version": 1, "template": "detailed"})

    assert compact.density.value == "COMPACT"
    assert compact.knowledge_level == PresentationKnowledgeLevel.A
    assert detailed.entity_detail.value == "CAUSALITY"
    assert detailed.plan_default.value == "FULL"
    assert [item.value for item in compact.actor_fields] == ["NAME", "ROLE"]
    assert [item.value for item in standard.actor_fields] == [
        "NAME",
        "ROLE",
        "LOCATION",
        "COMMAND_REACHABILITY",
    ]
    assert [item.value for item in detailed.actor_fields] == [
        "NAME",
        "ROLE",
        "LOCATION",
        "STATUS",
        "TASK",
        "COMMAND_REACHABILITY",
    ]


def test_profile_precedence_is_product_then_global_then_family_then_semantic() -> None:
    resolved = resolve_presentation_profile(
        {
            "schema_version": 1,
            "template": "detailed",
            "global_display": {
                "density": "COMPACT",
                "default_open": "COLLAPSED",
                "summary_slot": "HEADER",
                "semantic_order": ["NAME", "STATUS"],
            },
            "world_entities": {
                "entity_detail": "SUMMARY",
                "knowledge_level": "A",
            },
            "actor_team": {
                "visible_fields": ["NAME", "ROLE"],
                "field_order": ["ROLE", "NAME", "LOCATION"],
            },
            "family_overrides": [
                {
                    "node_family": "FACILITY",
                    "entity_detail": "DETAIL",
                    "default_open": "FULL",
                    "semantic_order": ["NAME", "FACTS"],
                }
            ],
            "semantic_overrides": [{"semantic_key": "facility", "summary_slot": "BODY"}],
        }
    )

    facility = resolved.entity("FACILITY", semantic_key="facility")
    generic = resolved.entity("GENERIC", semantic_key="unknown_old_key")
    assert resolved.density.value == "COMPACT"
    assert facility.summary_slot.value == "BODY"
    assert facility.detail_level.value == "DETAIL"
    assert facility.default_open.value == "FULL"
    assert facility.knowledge_level.value == "A"
    assert [item.value for item in facility.semantic_order] == ["NAME", "FACTS"]
    assert [item.value for item in resolved.actor_fields] == ["ROLE", "NAME"]
    assert generic.summary_slot.value == "HEADER"
    assert generic.detail_level.value == "SUMMARY"


def test_scenario_authored_labels_and_units_are_safe_scalar_metadata() -> None:
    resolved = resolve_presentation_profile(None)
    fact = FactDefinitionV2(
        key="condition",
        name="Condition",
        value_type=FactValueType.ENUM,
        initial_value="READY",
        initial_visibility=Visibility.KNOWN,
        allowed_values=("READY", "BLOCKED"),
        value_labels=(
            {"value": "READY", "label": "Ready"},
            {"value": "BLOCKED", "label": "Blocked"},
        ),
    )
    resource = ResourceDefinitionV2(
        key="water",
        name="Water",
        initial_value=0,
        minimum=0,
        maximum=100,
        unit="liters",
        display_unit="L",
    )

    assert resolved.fact_value_label(fact, "READY") == "Ready"
    assert resolved.fact_value_label(fact, "UNKNOWN") is None
    assert resolved.resource_unit(resource) == "L"


def test_corrupt_historical_profile_falls_back_to_product_default() -> None:
    resolved = resolve_presentation_profile(
        {
            "schema_version": 999,
            "template": "freeform",
            "unsupported_entity_payload": {"node": "hidden"},
        }
    )

    assert resolved.template.value == "standard"
    assert resolved.knowledge_level == PresentationKnowledgeLevel.A_PLUS_B_PLUS_C
