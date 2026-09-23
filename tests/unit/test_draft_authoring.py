from copy import deepcopy

import pytest

from app.scenarios.authoring import (
    DraftAuthoringError,
    delete_fact,
    delete_nested_object,
    delete_object,
    delete_root_collection_item,
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


def test_node_fact_delete_is_reference_safe_and_scoped_to_node_identity() -> None:
    document = _document()
    patient = next(item for item in document["world"]["nodes"] if item["key"] == "patient_one")
    patient["facts"].append(
        {
            "key": "working_only",
            "name": "Working only",
            "value_type": "BOOLEAN",
            "initial_value": False,
        }
    )
    other = deepcopy(patient)
    other["key"] = "other_node"
    document["world"]["nodes"].append(other)
    document["actions"][0]["planning"]["knowledge_gate"] = {
        "node_key": "patient_one",
        "fact_key": "working_only",
        "accepted_values": [True],
    }

    with pytest.raises(DraftAuthoringError) as referenced:
        delete_fact(document, node_key="patient_one", fact_key="working_only")
    assert referenced.value.code == "SCENARIO_FACT_REFERENCED"
    assert any(edge.source.object_kind == "action" for edge in referenced.value.references)

    document["actions"][0]["planning"]["knowledge_gate"] = None
    changed = delete_fact(document, node_key="patient_one", fact_key="working_only")
    patient_after = next(item for item in changed["world"]["nodes"] if item["key"] == "patient_one")
    other_after = next(item for item in changed["world"]["nodes"] if item["key"] == "other_node")
    assert all(fact["key"] != "working_only" for fact in patient_after["facts"])
    assert any(fact["key"] == "working_only" for fact in other_after["facts"])


def test_locality_passability_fact_contract_is_projected_to_scoped_fact_edges() -> None:
    document = _document()
    patient = next(item for item in document["world"]["nodes"] if item["key"] == "patient_one")
    patient["facts"].append(
        {"key": "passable", "name": "Passable", "value_type": "BOOLEAN", "initial_value": True}
    )
    document["metadata"]["locality"] = {"passability_fact_key": "passable"}

    with pytest.raises(DraftAuthoringError) as referenced:
        delete_fact(document, node_key="patient_one", fact_key="passable")
    assert referenced.value.code == "SCENARIO_FACT_REFERENCED"
    assert any(edge.source.object_kind == "metadata" for edge in referenced.value.references)


def test_dynamic_target_fact_consumers_expand_to_each_matching_scoped_fact() -> None:
    document = _document()
    patient = next(item for item in document["world"]["nodes"] if item["key"] == "patient_one")
    patient["facts"].append(
        {
            "key": "dynamic_flag",
            "name": "Dynamic flag",
            "value_type": "BOOLEAN",
            "initial_value": False,
        }
    )
    document["rules"][0]["condition"] = {
        "kind": "FACT_EQUALS",
        "node": {"kind": "CURRENT_TARGET"},
        "fact_key": "dynamic_flag",
        "value": True,
    }
    document["actions"][0]["planning"]["target_terminal_effects"] = [
        {"fact_key": "dynamic_flag", "value": True}
    ]

    edges = reference_index(document)
    assert any(
        edge.target.object_key == "patient_one"
        and edge.target.field_path == "facts.dynamic_flag"
        and edge.source.object_kind == "rule"
        for edge in edges
    )
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_fact(document, node_key="patient_one", fact_key="dynamic_flag")
    assert referenced.value.code == "SCENARIO_FACT_REFERENCED"


def test_current_target_fact_is_limited_to_explicit_action_targets() -> None:
    document = _document()
    patient = next(item for item in document["world"]["nodes"] if item["key"] == "patient_one")
    patient["facts"].append(
        {
            "key": "target_flag",
            "name": "Target flag",
            "value_type": "BOOLEAN",
            "initial_value": False,
        }
    )
    other = deepcopy(patient)
    other["key"] = "other_node"
    document["world"]["nodes"].append(other)
    document["actions"][0]["target_actor_roles"] = [
        {"role": "TARGET", "target_key": "patient_one"}
    ]
    document["actions"][0]["target_node_type_keys"] = []
    document["rules"][0]["condition"] = {
        "kind": "FACT_EQUALS",
        "node": {"kind": "CURRENT_TARGET"},
        "fact_key": "target_flag",
        "value": False,
    }

    targets = {
        edge.target.object_key
        for edge in reference_index(document)
        if edge.source.object_kind == "rule" and edge.target.field_path == "facts.target_flag"
    }
    assert targets == {"patient_one"}


def test_immutable_current_target_discriminator_excludes_other_authored_values() -> None:
    document = _document()
    patient = next(item for item in document["world"]["nodes"] if item["key"] == "patient_one")
    patient["facts"].append(
        {"key": "profile", "name": "Profile", "value_type": "TEXT", "initial_value": "patient"}
    )
    other = deepcopy(patient)
    other["key"] = "other_node"
    next(fact for fact in other["facts"] if fact["key"] == "profile")["initial_value"] = "other"
    document["world"]["nodes"].append(other)
    document["actions"][0]["target_actor_roles"] = [
        {"role": "PRIMARY", "target_key": "patient_one"},
        {"role": "SECONDARY", "target_key": "other_node"},
    ]
    document["actions"][0]["target_node_type_keys"] = []
    document["rules"][0]["condition"] = {
        "kind": "FACT_EQUALS",
        "node": {"kind": "CURRENT_TARGET"},
        "fact_key": "profile",
        "value": "patient",
    }

    targets = {
        edge.target.object_key
        for edge in reference_index(document)
        if edge.source.object_kind == "rule" and edge.target.field_path == "facts.profile"
    }
    assert targets == {"patient_one"}


def test_genuinely_dynamic_fact_selector_remains_conservative() -> None:
    document = _document()
    patient = next(item for item in document["world"]["nodes"] if item["key"] == "patient_one")
    patient["facts"].append(
        {"key": "dynamic_flag", "name": "Dynamic", "value_type": "BOOLEAN", "initial_value": False}
    )
    other = deepcopy(patient)
    other["key"] = "other_node"
    document["world"]["nodes"].append(other)
    document["actions"][0]["target_actor_roles"] = []
    document["actions"][0]["target_node_type_keys"] = []
    document["rules"][0]["condition"] = {
        "kind": "FACT_EQUALS",
        "node": {"kind": "CURRENT_TARGET"},
        "fact_key": "dynamic_flag",
        "value": False,
    }

    targets = {
        edge.target.object_key
        for edge in reference_index(document)
        if edge.source.object_kind == "rule" and edge.target.field_path == "facts.dynamic_flag"
    }
    assert targets == {"patient_one", "other_node"}


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
    assert any(
        edge.target.object_kind == "relation_type"
        and edge.target.object_key == "located_in"
        for edge in edges
    )


def test_published_stable_keys_cannot_be_renamed_but_new_draft_keys_can() -> None:
    base = _document()
    with pytest.raises(DraftAuthoringError, match="published stable key") as published:
        rename_key(
            deepcopy(base),
            object_kind="interaction",
            old_key="diagnosable",
            new_key="diagnosis_capability",
            protected_document=base,
        )
    assert published.value.code == "SCENARIO_PUBLISHED_STABLE_KEY_RENAME"

    draft = deepcopy(base)
    draft["interactions"].append(
        {"key": "new_interaction", "name": "New interaction", "description": ""}
    )
    renamed = rename_key(
        draft,
        object_kind="interaction",
        old_key="new_interaction",
        new_key="renamed_interaction",
        protected_document=base,
    )
    assert any(item["key"] == "renamed_interaction" for item in renamed["interactions"])


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


def test_root_pool_delete_ignores_its_own_identity_but_blocks_external_pool_refs() -> None:
    document = _document()
    initialization = document.setdefault("initialization", {})
    assert isinstance(initialization, dict)
    initialization["resource_pools"] = [
        {
            "pool_key": "unused_pool",
            "resource_key": "medicine",
            "region_key": None,
            "facility_key": None,
            "quantity": 1,
            "reserved_value": 0,
            "visibility": "VISIBLE",
            "availability": "AVAILABLE",
            "survey_discoverable": False,
        }
    ]
    changed = delete_root_collection_item(
        document, collection="resource_pools", identity="unused_pool"
    )
    assert changed["initialization"]["resource_pools"] == []

    document["rules"][0]["effects"].append(
        {
            "kind": "SET_RESOURCE_POOL_VISIBILITY",
            "pool_key": "unused_pool",
            "visibility": "VISIBLE",
        }
    )
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_root_collection_item(
            document, collection="resource_pools", identity="unused_pool"
        )
    assert referenced.value.code == "SCENARIO_ROOT_COLLECTION_ITEM_REFERENCED"
    assert referenced.value.references


def test_nested_action_parameter_delete_is_scoped_and_reference_safe() -> None:
    document = _document()
    document["rules"][0]["condition"] = {
        "kind": "PARAMETER_COMPARE",
        "parameter_key": "dosage",
        "operator": "GTE",
        "value": 1,
    }
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_nested_object(
            document,
            parent_kind="action",
            parent_key="treat_patient",
            collection="parameters",
            nested_key="dosage",
        )
    assert referenced.value.code == "SCENARIO_NESTED_OBJECT_REFERENCED"
    assert any(
        edge.target.object_kind == "action_parameter"
        for edge in referenced.value.references
    )

    document["rules"][0]["condition"] = None
    document["rules"][2]["effects"][1]["amount"] = {
        "source": "LITERAL",
        "literal": 1,
        "parameter_key": None,
        "multiplier": -1,
    }
    changed = delete_nested_object(
        document,
        parent_kind="action",
        parent_key="treat_patient",
        collection="parameters",
        nested_key="dosage",
    )
    action = next(item for item in changed["actions"] if item["key"] == "treat_patient")
    assert action["parameters"] == []
