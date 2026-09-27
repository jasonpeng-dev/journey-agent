"""Regression guards for the Phase H V3 authoring ownership contract."""

from __future__ import annotations

from app.domain.scenario_v3 import ScenarioDefinitionV3
from app.scenarios.initialization import FieldUiOwner
from tests.phase_h_authoring import AuthoringField, build_v3_authoring_field_inventory


def _inventory_by_path() -> dict[str, AuthoringField]:
    return {field.path: field for field in build_v3_authoring_field_inventory()}


def test_phase_h_v3_authoring_matrix_is_complete_and_typed() -> None:
    fields = build_v3_authoring_field_inventory()
    assert len(fields) == 381
    allowed = {
        "AUTHORABLE",
        "SYSTEM_MANAGED",
        "INITIALIZATION_AUTHORABLE",
        "DERIVED_READ_ONLY",
        "LEGACY_COMPATIBILITY",
        "PLATFORM_POLICY",
    }
    assert {field.classification for field in fields} <= allowed
    assert {field.owner for field in fields} <= set(FieldUiOwner)
    assert _inventory_by_path()["world.name"].owner is FieldUiOwner.LEGACY

    current = [field for field in fields if field.current]
    assert len(current) == 328
    assert all(
        field.coverage_status == "COVERED_TYPED"
        and field.section
        and field.control
        and field.owner in {FieldUiOwner.DESIGN_ONLY, FieldUiOwner.INITIALIZATION_ONLY}
        for field in current
    )
    assert len({field.path for field in current}) == len(current)


def test_phase_h_v3_contract_has_portable_current_owners() -> None:
    fields = _inventory_by_path()
    paths = set(fields)
    assert "goal_resolution.quick_inputs" in paths
    assert "planning.instructions" in paths
    assert "planning.recovery_hints" not in paths
    assert fields["goal_resolution.quick_inputs"].owner is FieldUiOwner.DESIGN_ONLY
    assert fields["planning.instructions"].owner is FieldUiOwner.DESIGN_ONLY
    assert fields["metadata.name"].section == "overview"
    assert fields["world.nodes[].initial_access"].section == "initialization"
    assert fields["derived_states[].dependencies[].kind"].section == "derived-states"
    assert fields["initialization.resource_pools[].pool_key"].control == (
        "creation dialog + static identity"
    )
    assert set(ScenarioDefinitionV3.model_fields) >= {
        "metadata",
        "world",
        "initialization",
        "goal_resolution",
    }


def test_phase_h_initialization_fields_have_single_canonical_owner() -> None:
    fields = _inventory_by_path()
    initialization_paths = {
        "initialization.start_node_key",
        "initialization.primary_actor_key",
        "world.nodes[].initial_access",
        "world.nodes[].initial_visibility",
        "world.nodes[].facts[].initial_value",
        "world.nodes[].facts[].initial_visibility",
        "world.relations[].initial_visibility",
        "actors.actor_profiles[].initial_node_key",
        "actors.actor_profiles[].command_reachability",
    }
    assert {fields[path].owner for path in initialization_paths} == {
        FieldUiOwner.INITIALIZATION_ONLY
    }


def test_phase_j_goal_cleanup_coverage_has_one_writer_for_current_fields() -> None:
    fields = _inventory_by_path()
    paths = set(fields)
    assert (
        not {
            "goal_resolution.allow_llm_fallback",
            "goal_resolution.clarification_prompt",
            "goal_resolution.world_goal_state_catalog",
        }
        & paths
    )
    current = [field for field in fields.values() if field.current]
    assert current
    assert len({field.path for field in current}) == len(current)
