from copy import deepcopy

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.scenarios.initialization import (
    BootstrapValueSource,
    FieldUiOwner,
    analyze_bootstrap,
    bootstrap_parity,
    evaluate_initial_derived_states,
    field_ui_owner,
    initialization_projection,
    schema_field_ownership,
)
from tests.scenario_fixtures import GENERIC_TEST


def _synthetic_definition() -> tuple[ScenarioDefinitionV2, dict[str, object]]:
    document = deepcopy(GENERIC_TEST.model_dump(mode="json", exclude_none=True))
    document["world"]["relations"][0]["initial_visibility"] = "HIDDEN"
    document["actors"]["actor_profiles"][0]["command_reachability"] = "DISCONNECTED"
    document["initialization"]["resource_pools"] = [
        {
            "pool_key": "clinic_medicine",
            "resource_key": "medicine",
            "quantity": 4,
            "reserved_value": 1,
            "visibility": "HIDDEN",
            "availability": "UNAVAILABLE",
            "survey_discoverable": True,
        }
    ]
    document["derived_states"] = [
        {
            "key": "patient_stabilized",
            "name": "Patient stabilized",
            "description": "Initial readiness preview",
            "value_type": "ENUM",
            "available_value": "AVAILABLE",
            "unavailable_value": "UNAVAILABLE",
            "allowed_values": ["AVAILABLE", "UNAVAILABLE"],
            "dependencies": [
                {
                    "kind": "FACT",
                    "node_key": "patient_one",
                    "fact_key": "stable",
                    "accepted_values": [True],
                }
            ],
        }
    ]
    return ScenarioDefinitionV2.model_validate(document), document


def test_every_v2_schema_leaf_has_an_explicit_ui_owner() -> None:
    ownership = schema_field_ownership()
    assert len(ownership) > 100
    assert set(ownership.values()) <= set(FieldUiOwner)
    assert ownership["schema_version"] == FieldUiOwner.SYSTEM
    assert ownership["objectives[].key"] == FieldUiOwner.LEGACY
    assert ownership["world.nodes[].facts[].initial_value"] == FieldUiOwner.INITIALIZATION_ONLY
    assert ownership["derived_states[].dependencies[].kind"] == FieldUiOwner.DESIGN_ONLY


def test_bootstrap_only_fields_have_one_owner_and_derived_is_readonly() -> None:
    paths = {
        "world.nodes[].initial_access",
        "world.nodes[].initial_visibility",
        "world.nodes[].facts[].initial_value",
        "world.nodes[].facts[].initial_visibility",
        "world.relations[].initial_visibility",
        "actors.actor_profiles[].initial_node_key",
        "actors.actor_profiles[].command_reachability",
        "initialization.resource_pools[]",
        "initialization.region_resource_knowledge[]",
    }
    assert {field_ui_owner(path) for path in paths} == {FieldUiOwner.INITIALIZATION_ONLY}
    assert field_ui_owner("derived_states[].dependencies[]") == FieldUiOwner.DESIGN_ONLY


def test_completeness_classifies_explicit_default_legacy_engine_and_derived_sources() -> None:
    definition, raw = _synthetic_definition()
    findings = analyze_bootstrap(definition, raw)
    sources = {item.source for item in findings}
    assert BootstrapValueSource.EXPLICIT in sources
    assert BootstrapValueSource.DEFAULT in sources
    assert BootstrapValueSource.ENGINE in sources
    assert BootstrapValueSource.DERIVED in sources
    assert any(item.identity == "pool:clinic_medicine:medicine:global" for item in findings)
    assert any(
        item.identity.endswith(":runtime_status") and item.value == "ACTIVE" for item in findings
    )


def test_projection_has_stable_variable_depth_hierarchy_and_owner_locators() -> None:
    definition, raw = _synthetic_definition()
    projection = initialization_projection(definition, raw)
    domains = {item["id"]: item for item in projection["domains"]}
    assert set(domains) == {"basic", "nodes", "actors", "resources", "relations", "derived"}
    node_groups = domains["nodes"]["groups"]
    assert all(group["id"].startswith("node-type:") for group in node_groups)
    patient = next(
        item for group in node_groups for item in group["items"] if item["id"] == "node:patient_one"
    )
    assert patient["locator"]["section"] == "world-entities"
    assert patient["context"]["key"] == "patient_one"
    derived = domains["derived"]["groups"][0]["items"][0]
    assert derived["readonly"] is True


def test_initial_derived_preview_separates_truth_and_knowledge() -> None:
    definition, _ = _synthetic_definition()
    values = evaluate_initial_derived_states(definition)
    assert values["patient_stabilized"] == {
        "truth": "UNAVAILABLE",
        "knowledge": "UNAVAILABLE",
        "knowledge_status": "KNOWN",
    }


def test_semantic_parity_separates_bootstrap_and_design_changes() -> None:
    definition, _ = _synthetic_definition()
    parity = bootstrap_parity(definition, GENERIC_TEST)
    assert parity["published"] is True
    assert any("command_reachability" in item for item in parity["initialization_changes"])
    assert "derived_states" in parity["design_changes"]
