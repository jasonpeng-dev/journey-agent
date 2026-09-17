from __future__ import annotations

import pytest

from app.domain.presentation import validate_presentation_profile
from app.domain.scenario_v2 import (
    FactDefinitionV2,
    RelationTypeDefinitionV2,
    ResourceDefinitionV2,
)
from app.domain.world import FactValueType, Visibility
from app.services.presentation_resolver import resolve_presentation_profile

SYNTHETIC_SCENARIOS = (
    (
        "medical",
        ("patient_condition", "treatment_ready", "blood_supply"),
        "clinical_supplies",
        "treats_patient",
    ),
    (
        "space",
        ("oxygen_level", "hull_integrity", "maintenance_crew"),
        "orbital_spares",
        "services_module",
    ),
    (
        "investigation",
        ("clue_status", "suspect_relation", "evidence_ready"),
        "case_files",
        "links_clue",
    ),
)


@pytest.mark.parametrize(
    ("domain", "fact_keys", "resource_key", "relation_key"),
    SYNTHETIC_SCENARIOS,
)
def test_synthetic_domains_use_typed_metadata_without_infrastructure_vocabulary(
    domain: str,
    fact_keys: tuple[str, str, str],
    resource_key: str,
    relation_key: str,
) -> None:
    facts = tuple(
        FactDefinitionV2(
            key=key,
            name=f"{domain.title()} {key.replace('_', ' ')}",
            value_type=FactValueType.ENUM,
            initial_value="KNOWN",
            initial_visibility=Visibility.KNOWN,
            allowed_values=("KNOWN", "UNKNOWN"),
            value_labels=(
                {"value": "KNOWN", "label": "Known"},
                {"value": "UNKNOWN", "label": "Unknown"},
            ),
        )
        for key in fact_keys
    )
    resource = ResourceDefinitionV2(
        key=resource_key,
        name=f"{domain.title()} supplies",
        initial_value=10,
        minimum=0,
        maximum=100,
        unit="units",
        display_unit="u",
    )
    relation = RelationTypeDefinitionV2(
        key=relation_key,
        name=f"{domain.title()} relation",
    )
    profile = validate_presentation_profile(
        {
            "schema_version": 1,
            "template": "standard",
            "family_overrides": [{"node_family": "GENERIC", "entity_detail": "DETAIL"}],
            "semantic_overrides": [
                {"semantic_key": key, "summary_slot": "BODY"} for key in fact_keys
            ],
        }
    )
    resolved = resolve_presentation_profile(profile.model_dump(mode="json"))

    assert tuple(fact.key for fact in facts) == fact_keys
    assert resource.key == resource_key
    assert relation.key == relation_key
    assert resolved.resource_unit(resource) == "u"
    assert resolved.fact_value_label(facts[0], "KNOWN") == "Known"
    assert all(
        resolved.entity("GENERIC", semantic_key=key).summary_slot.value == "BODY"
        for key in fact_keys
    )
    serialized = str(profile.model_dump(mode="json"))
    assert all(
        legacy_key not in serialized
        for legacy_key in ("operational", "power_supply", "repair_profile")
    )
