from __future__ import annotations

from app.domain.scenario_v2 import (
    PublicReferenceTypeV2,
    PublicReferenceV2,
    ScenarioDefinitionV2,
)
from app.scenarios.public_references import (
    PublicReferenceIdentity,
    PublicReferenceIndexBuilder,
)
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST


def _definition_with(*references: PublicReferenceV2) -> ScenarioDefinitionV2:
    document = GENERIC_TEST.model_dump(mode="json")
    document["public_references"] = [item.model_dump(mode="json") for item in references]
    return ScenarioDefinitionV2.model_validate(document)


def test_index_compiles_canonical_key_name_and_arbitrary_authored_reference() -> None:
    definition = _definition_with(
        PublicReferenceV2(
            term="healing supplies",
            ref_type="RESOURCE",
            ref_key="medicine",
        )
    )
    index = PublicReferenceIndexBuilder.build(definition)
    expected = (PublicReferenceIdentity(PublicReferenceTypeV2.RESOURCE, "medicine"),)

    assert index.lookup("use medicine").identities == expected
    assert index.lookup("use Medicine").identities == expected
    assert index.lookup("use healing supplies").identities == expected


def test_same_term_same_identity_dedupes_but_collision_remains_ambiguous() -> None:
    definition = _definition_with(
        PublicReferenceV2(term="supply", ref_type="RESOURCE", ref_key="medicine"),
        PublicReferenceV2(term="supply", ref_type="RESOURCE", ref_key="medicine"),
        PublicReferenceV2(term="supply", ref_type="NODE", ref_key="patient_one"),
    )

    lookup = PublicReferenceIndexBuilder.build(definition).lookup("deliver supply")

    assert lookup.identities == (
        PublicReferenceIdentity(PublicReferenceTypeV2.NODE, "patient_one"),
        PublicReferenceIdentity(PublicReferenceTypeV2.RESOURCE, "medicine"),
    )
    assert len(lookup.ambiguous_matches) == 1


def test_lookup_supports_type_safe_filtering() -> None:
    definition = _definition_with(
        PublicReferenceV2(term="supply", ref_type="RESOURCE", ref_key="medicine"),
        PublicReferenceV2(term="supply", ref_type="NODE", ref_key="patient_one"),
    )

    lookup = PublicReferenceIndexBuilder.build(definition).lookup(
        "deliver supply", ref_types={PublicReferenceTypeV2.RESOURCE}
    )

    assert lookup.identities == (
        PublicReferenceIdentity(PublicReferenceTypeV2.RESOURCE, "medicine"),
    )
    assert not lookup.ambiguous_matches


def test_approved_resource_references_are_deterministic() -> None:
    index = PublicReferenceIndexBuilder.build(LINJIANG_V2_TEST)

    for text, resource_key in (
        ("运输30个电力维修材料", "electrical_repair_parts"),
        ("运输30个应急物资", "emergency_relief_supplies"),
    ):
        lookup = index.lookup(text)

        assert lookup.identities == (
            PublicReferenceIdentity(PublicReferenceTypeV2.RESOURCE, resource_key),
        )
        assert not lookup.ambiguous_matches


def test_unregistered_expression_is_no_exact_hit_not_a_semantic_decision() -> None:
    index = PublicReferenceIndexBuilder.build(LINJIANG_V2_TEST)

    assert index.lookup("电力修理零件").identities == ()


def test_old_v2_document_loads_with_empty_public_reference_collection() -> None:
    document = GENERIC_TEST.model_dump(mode="json")
    document.pop("public_references", None)

    assert ScenarioDefinitionV2.model_validate(document).public_references == ()
