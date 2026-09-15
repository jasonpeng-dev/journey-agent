import pytest

from app.scenarios.authoring import (
    DraftAuthoringError,
    delete_object,
    locator_for_path,
    reference_index,
    rename_key,
)
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST


def _document():  # type: ignore[no-untyped-def]
    return GENERIC_TEST.model_dump(mode="json")


def test_reference_index_reports_used_by_edges() -> None:
    edges = reference_index(_document())

    assert any(
        edge.source.object_kind == "action"
        and edge.source.object_key == "diagnose_patient"
        and edge.target.object_kind == "interaction"
        and edge.target.object_key == "diagnosable"
        for edge in edges
    )
    assert any(
        edge.target.object_kind == "node" and edge.target.object_key == "patient_one"
        for edge in edges
    )


def test_stable_key_rename_atomically_updates_declared_references() -> None:
    renamed = rename_key(
        _document(),
        object_kind="interaction",
        old_key="diagnosable",
        new_key="diagnosis_capability",
    )

    interactions = {item["key"] for item in renamed["interactions"]}
    assert "diagnosis_capability" in interactions and "diagnosable" not in interactions
    assert renamed["actions"][0]["required_interaction_key"] == "diagnosis_capability"
    patient = next(item for item in renamed["world"]["nodes"] if item["key"] == "patient_one")
    assert "diagnosis_capability" in patient["interaction_keys"]


def test_referenced_delete_is_blocked_and_unreferenced_delete_succeeds() -> None:
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_object(_document(), object_kind="interaction", object_key="diagnosable")
    assert referenced.value.code == "SCENARIO_OBJECT_REFERENCED"
    assert referenced.value.references

    document = _document()
    document["world"]["resources"].append(
        {"key": "unused", "name": "Unused", "initial_value": 0, "minimum": 0}
    )
    changed = delete_object(document, object_kind="resource", object_key="unused")
    assert all(item["key"] != "unused" for item in changed["world"]["resources"])


def test_v2_reference_index_covers_nested_fact_pool_target_role_and_derived_refs() -> None:
    edges = reference_index(LINJIANG_V2_TEST.model_dump(mode="json"))

    assert any(
        edge.target.object_kind == "node" and edge.target.field_path == "facts.operational"
        for edge in edges
    )
    assert any(
        edge.source.object_kind == "action"
        and edge.source.field_path == "target_actor_roles.0.required_actor_role_key"
        and edge.target.object_kind == "role"
        for edge in edges
    )
    assert any(
        edge.target.object_kind == "initialization"
        and edge.target.field_path == "resource_pools.central_general_stock"
        for edge in edges
    )
    assert any(edge.target.object_kind == "derived_state" for edge in edges)
    assert any(edge.target.object_kind == "node_type" for edge in edges)


def test_v2_rename_updates_node_action_and_resource_references_atomically() -> None:
    document = LINJIANG_V2_TEST.model_dump(mode="json")

    renamed_node = rename_key(
        document,
        object_kind="node",
        old_key="central_hospital",
        new_key="central_hospital_renamed",
    )
    assert any(item["key"] == "central_hospital_renamed" for item in renamed_node["world"]["nodes"])
    assert all(
        edge.target.object_key != "central_hospital"
        for edge in reference_index(renamed_node)
        if edge.target.object_kind == "node"
    )

    renamed_action = rename_key(
        document,
        object_kind="action",
        old_key="repair_facility",
        new_key="repair_facility_v2",
    )
    assert any(rule.get("action_key") == "repair_facility_v2" for rule in renamed_action["rules"])

    resource_key = "municipal_repair_materials"
    renamed_resource = rename_key(
        document,
        object_kind="resource",
        old_key=resource_key,
        new_key="municipal_repair_materials_v2",
    )
    assert any(
        pool.get("resource_key") == "municipal_repair_materials_v2"
        for pool in renamed_resource["initialization"]["resource_pools"]
    )


def test_v2_nested_relation_and_public_reference_targets() -> None:
    document = GENERIC_TEST.model_dump(mode="json")
    relation = document["world"]["relations"][0]
    relation_key = "{}__{}__{}".format(
        relation["source_node_key"], relation["relation_type_key"], relation["target_node_key"]
    )
    document.setdefault("actions", [])[0]["planning"]["knowledge_gate"] = {
        "node_key": relation["source_node_key"],
        "fact_key": "condition",
        "accepted_values": [True],
    }
    document.setdefault("rules", [])[0]["effects"].append(
        {"kind": "SET_RELATION_VISIBILITY", "relation_key": relation_key, "visibility": "VISIBLE"}
    )
    document["public_references"] = [
        {"term": "Patient", "ref_type": "NODE", "ref_key": "patient_one"}
    ]
    edges = reference_index(document)
    assert any(
        edge.target.object_kind == "relation" and edge.target.object_key == relation_key
        for edge in edges
    )
    assert any(
        edge.source.object_kind == "public_reference" and edge.target.object_kind == "node"
        for edge in edges
    )


def test_locator_maps_nested_v2_paths_and_leaves_unlocatable_section_paths_raw() -> None:
    document = LINJIANG_V2_TEST.model_dump(mode="json")
    action_locator = locator_for_path(
        document,
        "actions.repair_facility.target_actor_roles.0.target_key",
    )
    assert action_locator is not None
    assert action_locator.object_kind == "action"
    assert action_locator.object_key == "repair_facility"
    assert action_locator.field_path == "target_actor_roles.0.target_key"

    fact_locator = locator_for_path(document, "world.nodes.0.facts.0.initial_value")
    assert fact_locator is not None
    assert fact_locator.object_kind == "node"
    assert fact_locator.field_path == "facts.0.initial_value"
    assert locator_for_path(document, "actions") is None


def test_display_names_are_not_reference_identity() -> None:
    document = _document()
    original = reference_index(document)
    document["interactions"][0]["name"] = "A different display name"
    changed = reference_index(document)
    assert {(edge.source, edge.target) for edge in changed} == {
        (edge.source, edge.target) for edge in original
    }
