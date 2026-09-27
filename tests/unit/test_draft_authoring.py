from copy import deepcopy

import pytest

import app.scenarios.authoring as authoring
from app.domain.scenario_v2 import RelationDirection, ScenarioDefinitionV2
from app.engine.rules import DeclarativeRuleEngine
from app.scenarios.authoring import (
    DraftAuthoringError,
    delete_fact,
    delete_nested_object,
    delete_object,
    delete_root_collection_item,
    locator_for_path,
    reference_index,
    rename_key,
    validate_generic_identity_transition,
)
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST


def _document():  # type: ignore[no-untyped-def]
    return GENERIC_TEST.model_dump(mode="json")


def _related_fact_document(*, required_fact_selector: bool) -> dict[str, object]:
    document = _document()
    world = document["world"]
    world["node_types"].append({"key": "probe_node_type", "name": "Probe node", "description": ""})
    world["relation_types"] = [
        {"key": "contains", "name": "Contains", "description": ""},
        {"key": "probe_link", "name": "Probe link", "description": ""},
    ]
    fact = {
        "key": "probe_shared_fact",
        "name": "Probe shared fact",
        "description": "",
        "value_type": "BOOLEAN",
        "initial_value": False,
        "initial_visibility": "KNOWN",
        "goal_addressable": False,
        "allowed_values": [],
    }
    for node_key in ("probe_anchor", "probe_neighbor", "probe_unrelated"):
        world["nodes"].append(
            {
                "key": node_key,
                "name": node_key,
                "description": "",
                "node_type_key": "probe_node_type",
                "initial_access": "AVAILABLE",
                "initial_visibility": "KNOWN",
                "interaction_keys": [],
                "facts": [deepcopy(fact)],
            }
        )
    world["relations"].append(
        {
            "source_node_key": "probe_anchor",
            "relation_type_key": "probe_link",
            "target_node_key": "probe_neighbor",
        }
    )
    rule = next(item for item in document["rules"] if item["phase"] == "RESOLVE")
    selector = {
        "kind": "RELATED",
        "anchor_node_key": "probe_anchor",
        "relation_type_key": "probe_link",
        "direction": "SOURCE",
    }
    if required_fact_selector:
        selector["required_fact_key"] = "probe_shared_fact"
        rule["condition"] = {
            "kind": "NODE_VISIBLE",
            "node": selector,
            "visibility": "KNOWN",
        }
    else:
        rule["condition"] = {
            "kind": "FACT_EQUALS",
            "node": selector,
            "fact_key": "probe_shared_fact",
            "value": False,
        }
    return document


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


@pytest.mark.parametrize("new_key", ["Upper", "has-dash", "", "a" * 81])
def test_safe_rename_validates_stable_key_format(new_key: str) -> None:
    with pytest.raises(DraftAuthoringError) as invalid:
        rename_key(
            _document(),
            object_kind="interaction",
            old_key="diagnosable",
            new_key=new_key,
        )
    assert invalid.value.code == "SCENARIO_OBJECT_KEY_INVALID"


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
    document["actions"][0]["target_actor_roles"] = [{"role": "TARGET", "target_key": "patient_one"}]
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
    assert any(edge.target.object_kind == "node_type" for edge in edges)
    assert any(
        edge.target.object_kind == "relation_type" and edge.target.object_key == "located_in"
        for edge in edges
    )
    assert any(edge.target.object_kind == "derived_state" for edge in edges)


def test_target_contract_and_rule_applicability_are_reference_safe_for_rename_and_delete() -> None:
    document = _document()
    action = next(item for item in document["actions"] if item["key"] == "diagnose_patient")
    action["target_contracts"] = [{"target_key": "patient_one", "initial_visibility": "KNOWN"}]
    rule = next(item for item in document["rules"] if item.get("action_key") == "diagnose_patient")
    rule["applicable_target_keys"] = ["patient_one"]

    edges = reference_index(document)
    assert any(
        edge.source.object_kind == "action"
        and edge.source.object_key == "diagnose_patient"
        and edge.target.object_kind == "node"
        and edge.target.object_key == "patient_one"
        for edge in edges
    )
    assert any(
        edge.source.object_kind == "rule"
        and edge.target.object_kind == "node"
        and edge.target.object_key == "patient_one"
        for edge in edges
    )

    renamed = rename_key(
        document,
        object_kind="node",
        old_key="patient_one",
        new_key="patient_primary",
    )
    renamed_action = next(item for item in renamed["actions"] if item["key"] == "diagnose_patient")
    assert renamed_action["target_contracts"][0]["target_key"] == "patient_primary"
    assert any(
        item.get("applicable_target_keys") == ["patient_primary"] for item in renamed["rules"]
    )

    with pytest.raises(DraftAuthoringError) as referenced:
        delete_object(document, object_kind="node", object_key="patient_one")
    assert referenced.value.code == "SCENARIO_OBJECT_REFERENCED"


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
        delete_root_collection_item(document, collection="resource_pools", identity="unused_pool")
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
        edge.target.object_kind == "action_parameter" for edge in referenced.value.references
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


@pytest.mark.parametrize("planning_field", ["success_outcome_codes", "wait_success_outcome_codes"])
def test_action_planning_outcome_lists_share_typed_delete_references(planning_field: str) -> None:
    document = _document()
    action = next(item for item in document["actions"] if item["key"] == "diagnose_patient")
    action["expected_outcomes"].append(
        {"code": "PLANNING_ONLY", "name": "Planning only", "success": True}
    )
    action["planning"][planning_field].append("PLANNING_ONLY")

    assert any(
        edge.target.object_kind == "action_outcome"
        and edge.target.object_key == "diagnose_patient:PLANNING_ONLY"
        for edge in reference_index(document)
    )
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_nested_object(
            document,
            parent_kind="action",
            parent_key="diagnose_patient",
            collection="expected_outcomes",
            nested_key="PLANNING_ONLY",
        )
    assert referenced.value.code == "SCENARIO_NESTED_OBJECT_REFERENCED"


def test_unused_expected_outcome_deletes_and_singular_plural_use_same_target_kind() -> None:
    document = _document()
    action = next(item for item in document["actions"] if item["key"] == "diagnose_patient")
    action["expected_outcomes"].append(
        {"code": "UNUSED_OUTCOME", "name": "Unused", "success": True}
    )
    targets = {
        (edge.target.object_kind, edge.target.object_key)
        for edge in reference_index(document)
        if edge.target.object_key == "diagnose_patient:DIAGNOSED"
    }
    assert targets == {("action_outcome", "diagnose_patient:DIAGNOSED")}
    changed = delete_nested_object(
        document,
        parent_kind="action",
        parent_key="diagnose_patient",
        collection="expected_outcomes",
        nested_key="UNUSED_OUTCOME",
    )
    changed_action = next(item for item in changed["actions"] if item["key"] == "diagnose_patient")
    assert all(item["code"] != "UNUSED_OUTCOME" for item in changed_action["expected_outcomes"])


@pytest.mark.parametrize(
    ("policy_collection", "policy_row"),
    [
        ("autonomous_limits", {"parameter_key": "probe_limit", "maximum": 50}),
        ("approval_required_values", {"parameter_key": "probe_limit", "values": [100]}),
    ],
)
def test_actor_authority_policy_parameters_block_scoped_action_parameter_delete(
    policy_collection: str,
    policy_row: dict[str, object],
) -> None:
    document = _document()
    action = next(item for item in document["actions"] if item["key"] == "treat_patient")
    action["parameters"].append(
        {
            "key": "probe_limit",
            "name": "Probe limit",
            "value_type": "INTEGER",
            "required": False,
            "minimum": 0,
            "maximum": 100,
            "allowed_values": [],
        }
    )
    actor = next(
        item for item in document["actors"]["actor_profiles"] if item["key"] == "doctor_lee"
    )
    actor["authority_policy"][policy_collection].append(policy_row)
    ScenarioDefinitionV2.model_validate(document)

    assert any(
        edge.source.object_kind == "actor"
        and edge.source.object_key == "doctor_lee"
        and edge.target.object_kind == "action_parameter"
        and edge.target.object_key == "treat_patient:probe_limit"
        for edge in reference_index(document)
    )
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_nested_object(
            document,
            parent_kind="action",
            parent_key="treat_patient",
            collection="parameters",
            nested_key="probe_limit",
        )
    assert referenced.value.code == "SCENARIO_NESTED_OBJECT_REFERENCED"


def test_actor_policy_parameter_targets_only_allowed_actions_and_unused_parameters_delete() -> None:
    document = _document()
    diagnose = next(item for item in document["actions"] if item["key"] == "diagnose_patient")
    treat = next(item for item in document["actions"] if item["key"] == "treat_patient")
    parameter = {
        "key": "shared_policy_key",
        "name": "Shared policy key",
        "value_type": "INTEGER",
        "required": False,
        "minimum": 0,
        "maximum": 100,
        "allowed_values": [],
    }
    diagnose["parameters"].append(deepcopy(parameter))
    treat["parameters"].append(deepcopy(parameter))
    actor = next(
        item for item in document["actors"]["actor_profiles"] if item["key"] == "doctor_lee"
    )
    actor["allowed_action_keys"] = ["diagnose_patient"]
    actor["authority_policy"]["autonomous_limits"].append(
        {"parameter_key": "shared_policy_key", "maximum": 50}
    )

    targets = {
        edge.target.object_key
        for edge in reference_index(document)
        if edge.source.object_kind == "actor"
        and edge.source.object_key == "doctor_lee"
        and edge.target.object_kind == "action_parameter"
    }
    assert targets == {"diagnose_patient:shared_policy_key"}
    changed = delete_nested_object(
        document,
        parent_kind="action",
        parent_key="treat_patient",
        collection="parameters",
        nested_key="shared_policy_key",
    )
    changed_action = next(item for item in changed["actions"] if item["key"] == "treat_patient")
    assert all(item["key"] != "shared_policy_key" for item in changed_action["parameters"])

    unused = _document()
    action = next(item for item in unused["actions"] if item["key"] == "treat_patient")
    action["parameters"].append(deepcopy(parameter))
    changed = delete_nested_object(
        unused,
        parent_kind="action",
        parent_key="treat_patient",
        collection="parameters",
        nested_key="shared_policy_key",
    )
    changed_action = next(item for item in changed["actions"] if item["key"] == "treat_patient")
    assert all(item["key"] != "shared_policy_key" for item in changed_action["parameters"])


def test_actor_policy_same_parameter_key_resolves_across_each_allowed_action() -> None:
    document = _document()
    parameter = {
        "key": "shared_limit",
        "name": "Shared limit",
        "value_type": "INTEGER",
        "required": False,
        "minimum": 0,
        "maximum": 100,
        "allowed_values": [],
    }
    for action_key in ("diagnose_patient", "treat_patient"):
        action = next(item for item in document["actions"] if item["key"] == action_key)
        action["parameters"].append(deepcopy(parameter))
    actor = next(
        item for item in document["actors"]["actor_profiles"] if item["key"] == "doctor_lee"
    )
    actor["allowed_action_keys"] = ["diagnose_patient", "treat_patient"]
    actor["authority_policy"]["autonomous_limits"].append(
        {"parameter_key": "shared_limit", "maximum": 50}
    )

    targets = {
        edge.target.object_key
        for edge in reference_index(document)
        if edge.source.object_kind == "actor"
        and edge.source.object_key == "doctor_lee"
        and edge.target.object_kind == "action_parameter"
    }
    assert targets == {"diagnose_patient:shared_limit", "treat_patient:shared_limit"}
    for action_key in ("diagnose_patient", "treat_patient"):
        with pytest.raises(DraftAuthoringError) as referenced:
            delete_nested_object(
                document,
                parent_kind="action",
                parent_key=action_key,
                collection="parameters",
                nested_key="shared_limit",
            )
        assert referenced.value.code == "SCENARIO_NESTED_OBJECT_REFERENCED"


def test_related_required_fact_references_candidate_fact_not_anchor_or_unrelated_facts() -> None:
    document = _related_fact_document(required_fact_selector=True)
    definition = ScenarioDefinitionV2.model_validate(document)
    engine = DeclarativeRuleEngine(definition)
    assert engine._related_nodes(
        "probe_anchor",
        "probe_link",
        RelationDirection.SOURCE,
        required_fact_key="probe_shared_fact",
    ) == ("probe_neighbor",)

    targets = {
        edge.target.object_key
        for edge in reference_index(document)
        if edge.source.object_kind == "rule" and edge.target.field_path == "facts.probe_shared_fact"
    }
    assert targets == {"probe_neighbor"}
    with pytest.raises(DraftAuthoringError) as referenced:
        delete_fact(document, node_key="probe_neighbor", fact_key="probe_shared_fact")
    assert referenced.value.code == "SCENARIO_FACT_REFERENCED"
    assert delete_fact(document, node_key="probe_anchor", fact_key="probe_shared_fact")
    assert delete_fact(document, node_key="probe_unrelated", fact_key="probe_shared_fact")


def test_ordinary_related_fact_uses_provable_topology_and_dynamic_selector_stays_conservative() -> (
    None
):
    document = _related_fact_document(required_fact_selector=False)
    ScenarioDefinitionV2.model_validate(document)
    targets = {
        edge.target.object_key
        for edge in reference_index(document)
        if edge.source.object_kind == "rule" and edge.target.field_path == "facts.probe_shared_fact"
    }
    assert targets == {"probe_neighbor"}
    assert delete_fact(document, node_key="probe_unrelated", fact_key="probe_shared_fact")

    dynamic = _related_fact_document(required_fact_selector=False)
    dynamic_rule = next(item for item in dynamic["rules"] if item["phase"] == "RESOLVE")
    dynamic_rule["trigger"] = "STATE"
    dynamic_rule.pop("action_key", None)
    dynamic_rule["condition"]["node"].pop("anchor_node_key")
    dynamic_targets = {
        edge.target.object_key
        for edge in reference_index(dynamic)
        if edge.source.object_kind == "rule" and edge.target.field_path == "facts.probe_shared_fact"
    }
    assert {"probe_anchor", "probe_neighbor", "probe_unrelated"}.issubset(dynamic_targets)
    with pytest.raises(DraftAuthoringError) as conservative:
        delete_fact(dynamic, node_key="probe_unrelated", fact_key="probe_shared_fact")
    assert conservative.value.code == "SCENARIO_FACT_REFERENCED"


def test_safe_action_rename_rewrites_every_exact_inbound_action_reference() -> None:
    document = _document()
    before = reference_index(document)
    assert any(
        edge.target.object_kind == "action" and edge.target.object_key == "treat_patient"
        for edge in before
    )

    renamed = rename_key(
        document,
        object_kind="action",
        old_key="treat_patient",
        new_key="treat_patient_v2",
    )
    after = reference_index(renamed)
    assert all(
        edge.target.object_key != "treat_patient"
        for edge in after
        if edge.target.object_kind == "action"
    )
    assert any(
        edge.target.object_kind == "action" and edge.target.object_key == "treat_patient_v2"
        for edge in after
    )
    assert (
        "treat_patient_v2"
        in next(
            item for item in renamed["actors"]["actor_profiles"] if item["key"] == "doctor_lee"
        )["allowed_action_keys"]
    )
    assert all(item.get("action_key") != "treat_patient" for item in renamed["rules"])


def test_relation_composite_identity_has_no_partial_rename_operation() -> None:
    document = _document()
    relation = document["world"]["relations"][0]
    relation["key"] = "explicit_relation_identity"
    with pytest.raises(DraftAuthoringError) as unsupported:
        rename_key(
            document,
            object_kind="relation",
            old_key="explicit_relation_identity",
            new_key="renamed_relation_identity",
        )
    assert unsupported.value.code == "SCENARIO_OBJECT_KIND_UNSUPPORTED"


def test_relation_composite_identity_must_be_complete_when_created() -> None:
    previous = _document()
    incomplete = deepcopy(previous)
    incomplete["world"]["relations"].append(
        {
            "key": "new_relation",
            "source_node_key": "",
            "relation_type_key": "",
            "target_node_key": "",
        }
    )
    with pytest.raises(DraftAuthoringError) as incomplete_creation:
        validate_generic_identity_transition(previous, incomplete)
    assert incomplete_creation.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    legacy_incomplete = deepcopy(previous)
    legacy_incomplete["world"]["relations"].append(
        {
            "key": "legacy_incomplete_relation",
            "source_node_key": "",
            "relation_type_key": "",
            "target_node_key": "",
        }
    )
    completed_from_legacy = deepcopy(legacy_incomplete)
    legacy_relation = next(
        item
        for item in completed_from_legacy["world"]["relations"]
        if item.get("key") == "legacy_incomplete_relation"
    )
    legacy_relation.update(
        source_node_key="patient_one",
        relation_type_key="contains",
        target_node_key="triage_room",
    )
    with pytest.raises(DraftAuthoringError) as legacy_completion:
        validate_generic_identity_transition(legacy_incomplete, completed_from_legacy)
    assert legacy_completion.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    complete = deepcopy(previous)
    complete["world"]["relations"].append(
        {
            "key": "new_relation",
            "source_node_key": "patient_one",
            "relation_type_key": "contains",
            "target_node_key": "triage_room",
        }
    )
    validate_generic_identity_transition(previous, complete)

    changed = deepcopy(complete)
    relation = next(
        item for item in changed["world"]["relations"] if item.get("key") == "new_relation"
    )
    relation["target_node_key"] = "patient_one"
    with pytest.raises(DraftAuthoringError) as immutable:
        validate_generic_identity_transition(complete, changed)
    assert immutable.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"


def test_generic_identity_transition_rejects_key_and_composite_relation_changes() -> None:
    previous = _document()
    changed_action = deepcopy(previous)
    diagnose_action = next(
        item for item in changed_action["actions"] if item["key"] == "diagnose_patient"
    )
    diagnose_action["key"] = "diagnose_renamed"
    with pytest.raises(DraftAuthoringError) as action_error:
        validate_generic_identity_transition(previous, changed_action)
    assert action_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    blank_action_key = deepcopy(previous)
    diagnose_action = next(
        item for item in blank_action_key["actions"] if item["key"] == "diagnose_patient"
    )
    diagnose_action["key"] = ""
    with pytest.raises(DraftAuthoringError) as blank_action_error:
        validate_generic_identity_transition(previous, blank_action_key)
    assert blank_action_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    blank_outcome_code = deepcopy(previous)
    diagnose_action = next(
        item for item in blank_outcome_code["actions"] if item["key"] == "diagnose_patient"
    )
    diagnose_action["expected_outcomes"][0]["code"] = ""
    with pytest.raises(DraftAuthoringError) as blank_outcome_error:
        validate_generic_identity_transition(previous, blank_outcome_code)
    assert blank_outcome_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    changed_derived = deepcopy(previous)
    changed_derived.setdefault("derived_states", []).append(
        {
            "key": "status_summary",
            "name": "Status summary",
            "description": "",
            "value_type": "BOOLEAN",
            "available_value": True,
            "unavailable_value": False,
            "dependencies": [],
        }
    )
    validate_generic_identity_transition(previous, changed_derived)
    renamed_derived = deepcopy(changed_derived)
    renamed_derived["derived_states"][0]["key"] = "status_summary_v2"
    with pytest.raises(DraftAuthoringError) as derived_error:
        validate_generic_identity_transition(changed_derived, renamed_derived)
    assert derived_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"
    blank_derived = deepcopy(changed_derived)
    blank_derived["derived_states"][0]["key"] = ""
    with pytest.raises(DraftAuthoringError) as blank_derived_error:
        validate_generic_identity_transition(changed_derived, blank_derived)
    assert blank_derived_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    changed_relation = deepcopy(previous)
    relation = changed_relation["world"]["relations"][0]
    relation["key"] = "explicit_relation_identity"
    relation["target_node_key"] = "triage_room"
    with pytest.raises(DraftAuthoringError) as relation_error:
        validate_generic_identity_transition(previous, changed_relation)
    assert relation_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    blank_relation_endpoint = deepcopy(previous)
    blank_relation_endpoint["world"]["relations"][0]["target_node_key"] = ""
    with pytest.raises(DraftAuthoringError) as blank_relation_error:
        validate_generic_identity_transition(previous, blank_relation_endpoint)
    assert blank_relation_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    explicit_relation = deepcopy(previous)
    explicit_relation["world"]["relations"][0]["key"] = "explicit_relation_identity"
    changed_explicit_relation = deepcopy(explicit_relation)
    changed_explicit_relation["world"]["relations"][0]["target_node_key"] = "triage_room"
    with pytest.raises(DraftAuthoringError) as explicit_relation_error:
        validate_generic_identity_transition(explicit_relation, changed_explicit_relation)
    assert explicit_relation_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"


def test_delete_reference_analysis_failure_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_analysis(_document: dict[str, object]) -> tuple[object, ...]:
        raise RuntimeError("synthetic reference scanner failure")

    monkeypatch.setattr(authoring, "reference_index", fail_analysis)
    with pytest.raises(DraftAuthoringError) as failed:
        delete_object(_document(), object_kind="resource", object_key="unused")
    assert failed.value.code == "SCENARIO_REFERENCE_ANALYSIS_FAILED"
