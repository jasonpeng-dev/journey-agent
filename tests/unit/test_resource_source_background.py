from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.dependency_closure import build_dependency_closure
from app.agent.generic import GenericAgentService
from app.agent.planning_context import PlanningContextBuilder
from app.agent.provider import (
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerResourceSourceHint,
)
from app.domain.enums import CommandReachability, ResourcePoolVisibility
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveRequirementKind, ScenarioDefinitionV2
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
    Player,
    ScenarioVersion,
)
from app.scenarios.authoring import DraftAuthoringError, delete_object, reference_index, rename_key
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import (
    canonical_document_payload,
    canonical_payload_hash,
    legacy_resource_source_hint_payload,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_game import GenericGameService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST, predefined_goal_resolution


def _resource_definition() -> Any:
    region_keys = (
        "target_region",
        "primary_region",
        "candidate_region",
        "other_region",
        "unrelated_region",
    )
    nodes = tuple(SimpleNamespace(key=key, node_type_key="region") for key in region_keys)
    node_by_key = {node.key: node for node in nodes}
    return SimpleNamespace(
        world=SimpleNamespace(
            nodes=nodes,
            node=lambda key: node_by_key.get(key),
        ),
        metadata=SimpleNamespace(
            locality=SimpleNamespace(
                enabled=True,
                scoped_resources=True,
                region_node_type_key="region",
                facility_node_type_key="facility",
                transport_node_type_key="transport",
                located_in_relation_type_key="located_in",
                transport_endpoint_relation_type_key="endpoint",
                passability_fact_key=None,
            )
        ),
    )


def _resource_objective() -> Any:
    return SimpleNamespace(
        key="resource_objective",
        completion_requirements=(
            SimpleNamespace(
                key="needs_support_material",
                kind=ObjectiveRequirementKind.RESOURCE_AT_LEAST,
                region_key="target_region",
                resource_key="support_material",
                minimum=5,
            ),
        ),
        prerequisites=(),
    )


def _source_hint() -> PlannerResourceSourceHint:
    return PlannerResourceSourceHint(
        resource_key="support_material",
        primary_region_key="primary_region",
        candidate_region_keys=("candidate_region", "other_region"),
    )


def _resource_planner_input(
    *,
    available_by_region: dict[str, int] | None,
    knowledge_by_region: dict[str, tuple[str, bool]] | None = None,
    include_survey_action: bool = False,
) -> PlannerInput:
    region_keys = (
        "target_region",
        "primary_region",
        "candidate_region",
        "other_region",
        "unrelated_region",
    )
    if knowledge_by_region is None:
        knowledge_by_region = {key: ("VISIBLE", True) for key in region_keys}
    scopes: dict[str, object] = {}
    if available_by_region is not None:
        for region_key, amount in available_by_region.items():
            visibility, surveyed = knowledge_by_region.get(region_key, ("VISIBLE", True))
            scopes[region_key] = {
                "known_total": amount,
                "known_available": amount,
                "resource_inventory_visibility": visibility,
                "resource_survey_completed": surveyed,
                "pools": [],
            }
    known_available = sum(available_by_region.values()) if available_by_region else 0
    resources: dict[str, object] = {}
    if available_by_region is not None:
        resources["support_material"] = {
            "known_total": known_available,
            "known_available": known_available,
            "scopes": scopes,
        }
    actors: tuple[PlannerActorState, ...] = ()
    action_contracts: tuple[PlannerActionContract, ...] = ()
    if include_survey_action:
        actors = (
            PlannerActorState(
                actor_key="observer",
                role_key="observer",
                capabilities=("INSPECT",),
                allowed_action_keys=("survey",),
                availability="ACTIVE",
                current_region="target_region",
                command_reachability="ONLINE",
            ),
        )
        action_contracts = (
            PlannerActionContract(
                action_key="survey",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["INSPECT"],
                },
                target_contract={"kind": "NODE"},
                deterministic_effects=({"type": "REGION_RESOURCE_KNOWLEDGE"},),
            ),
        )
    return PlannerInput(
        actors=actors,
        action_contracts=action_contracts,
        known_world=PlannerKnownWorldSlice(
            nodes=tuple(
                {
                    "key": region_key,
                    "type": "region",
                    "access": "AVAILABLE",
                    "interactions": ["survey_resources"],
                }
                for region_key in region_keys
            ),
            resources=resources,
            resource_knowledge=tuple(
                {
                    "region_key": region_key,
                    "resource_inventory_visibility": visibility,
                    "resource_survey_completed": surveyed,
                }
                for region_key, (visibility, surveyed) in knowledge_by_region.items()
            ),
            resource_source_hints=(_source_hint(),),
        ),
    )


def _resource_closure(
    *,
    available_by_region: dict[str, int] | None,
    knowledge_by_region: dict[str, tuple[str, bool]] | None = None,
    include_survey_action: bool = False,
) -> Any:
    return build_dependency_closure(
        _resource_definition(),
        (_resource_objective(),),
        _resource_planner_input(
            available_by_region=available_by_region,
            knowledge_by_region=knowledge_by_region,
            include_survey_action=include_survey_action,
        ),
    )


def _linjiang_runtime(session: Session, key: str) -> tuple[Any, Any]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(LINJIANG_V2_TEST)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name=key)
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return runtime, scope


def test_linjiang_resource_source_hints_are_authored_and_quantity_free() -> None:
    hints = {
        item.key: item.source_hint
        for item in LINJIANG_V2_TEST.world.resources
        if item.source_hint is not None
    }
    assert len(hints) == 7
    assert hints["general_engineering_parts"].primary_region_key == "north_industrial_district"
    assert hints["general_engineering_parts"].candidate_region_keys == (
        "west_logistics_district",
        "central_district",
    )
    assert hints["emergency_fuel"].primary_region_key == "south_waterfront_district"
    assert hints["emergency_fuel"].candidate_region_keys == ("north_industrial_district",)

    document = LINJIANG_V2_TEST.model_dump(mode="json")
    resources = document["world"]["resources"]
    assert all(
        "resource_key" not in item["source_hint"]
        and set(item["source_hint"]) <= {"primary_region_key", "candidate_region_keys"}
        for item in resources
        if "source_hint" in item
    )
    assert "public_knowledge" not in document
    assert "source_hint" not in resources[0] or "quantity" not in resources[0]["source_hint"]


def _legacy_linjiang_document() -> dict[str, Any]:
    current = LINJIANG_V2_TEST.model_dump(mode="json")
    legacy = legacy_resource_source_hint_payload(current)
    assert legacy is not None
    return legacy


def test_current_and_legacy_source_hint_documents_normalize_to_same_model() -> None:
    current_document = LINJIANG_V2_TEST.model_dump(mode="json")
    legacy_document = _legacy_linjiang_document()
    current = ScenarioDefinitionV2.model_validate(current_document)
    legacy = ScenarioDefinitionV2.model_validate(legacy_document)

    assert current == LINJIANG_V2_TEST
    assert legacy == current
    assert "public_knowledge" not in legacy.model_dump(mode="json")
    assert (
        legacy.model_dump(mode="json")["public_references"] == current_document["public_references"]
    )
    assert all(
        "resource_key" not in (item.source_hint.model_dump(mode="json") if item.source_hint else {})
        for item in legacy.world.resources
    )

    dual_document = deepcopy(current_document)
    dual_document["public_knowledge"] = deepcopy(legacy_document["public_knowledge"])
    assert ScenarioDefinitionV2.model_validate(dual_document) == current

    dual_document["public_knowledge"]["resource_source_hints"][0]["primary_region_key"] = (
        "central_district"
    )
    with pytest.raises(ValidationError, match="conflict"):
        ScenarioDefinitionV2.model_validate(dual_document)


def test_immutable_legacy_snapshot_loads_without_rewriting_its_wire_shape(
    session: Session,
) -> None:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(LINJIANG_V2_TEST)
    canonical = canonical_document_payload(LINJIANG_V2_TEST.model_dump(mode="json"))
    legacy = legacy_resource_source_hint_payload(canonical)
    assert legacy is not None
    version = ScenarioVersion(
        scenario_id=scenario.id,
        version_number=1,
        schema_version=2,
        snapshot_document=deepcopy(legacy),
        content_hash=canonical_payload_hash(legacy),
        engine_contract_key=LINJIANG_V2_TEST.engine_contract.key,
        engine_contract_version=LINJIANG_V2_TEST.engine_contract.version,
        published_at=datetime.now(UTC),
    )
    session.add(version)
    session.flush()

    loaded = ScenarioVersionRepository(session).load(version.id)

    assert loaded.definition == ScenarioDefinitionV2.model_validate(legacy)
    assert version.snapshot_document == legacy
    assert "public_knowledge" in version.snapshot_document
    assert all(
        "source_hint" not in resource
        for resource in version.snapshot_document["world"]["resources"]
    )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda document: document["public_knowledge"]["resource_source_hints"][0].update(
            {"resource_key": "missing_resource"}
        ),
        lambda document: document["public_knowledge"]["resource_source_hints"][0].update(
            {"primary_region_key": "south_fuel_terminal"}
        ),
        lambda document: document["public_knowledge"]["resource_source_hints"][0].update(
            {"candidate_region_keys": ["central_district", "central_district"]}
        ),
        lambda document: document["public_knowledge"]["resource_source_hints"].append(
            deepcopy(document["public_knowledge"]["resource_source_hints"][0])
        ),
    ),
)
def test_resource_source_hint_references_and_duplicates_fail_closed(mutate: Any) -> None:
    document = _legacy_linjiang_document()
    mutate(document)
    with pytest.raises(ValidationError):
        ScenarioDefinitionV2.model_validate(document)


def test_scenarios_without_resource_source_background_remain_compatible() -> None:
    document = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    assert "public_knowledge" not in document
    assert ScenarioDefinitionV2.model_validate(document) == GENERIC_TEST


def test_resource_region_references_are_owned_by_resource_and_block_region_delete() -> None:
    document = LINJIANG_V2_TEST.model_dump(mode="json")
    edges = reference_index(document)
    source_edges = [
        edge
        for edge in edges
        if edge.source.object_kind == "resource"
        and (edge.source.field_path or "").startswith("source_hint.")
    ]
    assert source_edges
    assert all(edge.target.object_kind == "node" for edge in source_edges)
    assert not any(
        edge.source.object_kind == "document"
        and edge.source.field_path == "public_knowledge.resource_source_hints"
        for edge in edges
    )
    assert not any(edge.target.object_kind == "resource" for edge in source_edges)

    primary_edge = next(
        edge for edge in source_edges if edge.source.field_path == "source_hint.primary_region_key"
    )
    assert primary_edge.source.object_key is not None
    assert primary_edge.target.object_key is not None
    with pytest.raises(DraftAuthoringError) as caught:
        delete_object(document, object_kind="node", object_key=primary_edge.target.object_key)
    assert caught.value.code == "SCENARIO_OBJECT_REFERENCED"
    assert any(
        edge.source.object_kind == "resource"
        and edge.source.object_key == primary_edge.source.object_key
        and edge.source.field_path == "source_hint.primary_region_key"
        for edge in caught.value.references
    )


@pytest.mark.parametrize(
    ("field_path", "new_key"),
    (
        ("source_hint.primary_region_key", "renamed_primary_region"),
        ("source_hint.candidate_region_keys", "renamed_candidate_region"),
    ),
)
def test_safe_region_rename_rewrites_resource_source_hint_references(
    field_path: str,
    new_key: str,
) -> None:
    document = LINJIANG_V2_TEST.model_dump(mode="json")
    edge = next(
        edge
        for edge in reference_index(document)
        if edge.source.object_kind == "resource" and edge.source.field_path == field_path
    )
    old_key = edge.target.object_key
    assert old_key is not None

    renamed = rename_key(
        document,
        object_kind="node",
        old_key=old_key,
        new_key=new_key,
    )

    resource = next(
        item for item in renamed["world"]["resources"] if item["key"] == edge.source.object_key
    )
    hint = resource["source_hint"]
    if field_path.endswith("candidate_region_keys"):
        assert old_key not in hint["candidate_region_keys"]
        assert new_key in hint["candidate_region_keys"]
    else:
        assert hint["primary_region_key"] == new_key

    rewritten_edge = next(
        edge
        for edge in reference_index(renamed)
        if edge.source.object_kind == "resource"
        and edge.source.object_key == resource["key"]
        and edge.source.field_path == field_path
        and edge.target.object_key == new_key
    )
    assert rewritten_edge.target.object_kind == "node"
    with pytest.raises(DraftAuthoringError) as caught:
        delete_object(renamed, object_kind="node", object_key=new_key)
    assert caught.value.code == "SCENARIO_OBJECT_REFERENCED"


def test_sufficient_known_inventory_suppresses_source_hints() -> None:
    result = _resource_closure(
        available_by_region={"target_region": 5},
        knowledge_by_region={
            "target_region": ("VISIBLE", True),
            "primary_region": ("HIDDEN", False),
            "candidate_region": ("HIDDEN", False),
            "other_region": ("HIDDEN", False),
            "unrelated_region": ("HIDDEN", False),
        },
    )
    assert result.planner_input.known_world.resource_source_hints == ()


def test_sufficient_inventory_in_non_primary_region_suppresses_source_hints() -> None:
    result = _resource_closure(
        available_by_region={"target_region": 0, "candidate_region": 8},
        knowledge_by_region={
            "target_region": ("VISIBLE", True),
            "primary_region": ("HIDDEN", False),
            "candidate_region": ("VISIBLE", True),
            "other_region": ("HIDDEN", False),
            "unrelated_region": ("HIDDEN", False),
        },
    )
    assert result.planner_input.known_world.resource_source_hints == ()


def test_unknown_or_insufficient_inventory_projects_ordered_source_hints() -> None:
    unknown_knowledge = {
        key: ("HIDDEN", False)
        for key in (
            "target_region",
            "primary_region",
            "candidate_region",
            "other_region",
            "unrelated_region",
        )
    }
    unknown = _resource_closure(
        available_by_region=None,
        knowledge_by_region=unknown_knowledge,
    )
    assert unknown.planner_input.known_world.resource_source_hints == (_source_hint(),)
    assert any(
        item.get("knowledge_status_code") == "RESOURCE_INVENTORY_UNKNOWN"
        for item in unknown.planner_input.known_world.unknown_dependencies
    )

    insufficient = _resource_closure(
        available_by_region={"target_region": 2},
        knowledge_by_region={
            "target_region": ("VISIBLE", True),
            "primary_region": ("HIDDEN", False),
            "candidate_region": ("HIDDEN", False),
            "other_region": ("HIDDEN", False),
            "unrelated_region": ("HIDDEN", False),
        },
    )
    assert insufficient.planner_input.known_world.resource_source_hints == (_source_hint(),)


def test_surveyed_hinted_regions_are_removed_from_pending_source_guidance() -> None:
    result = _resource_closure(
        available_by_region=None,
        knowledge_by_region={
            "target_region": ("HIDDEN", False),
            "primary_region": ("VISIBLE", True),
            "candidate_region": ("HIDDEN", False),
            "other_region": ("VISIBLE", True),
            "unrelated_region": ("HIDDEN", False),
        },
    )
    hint = result.planner_input.known_world.resource_source_hints[0]
    assert hint.primary_region_key is None
    assert hint.candidate_region_keys == ("candidate_region",)


def test_source_hints_do_not_restrict_the_generic_region_catalog() -> None:
    result = _resource_closure(
        available_by_region=None,
        knowledge_by_region={
            key: ("HIDDEN", False)
            for key in (
                "target_region",
                "primary_region",
                "candidate_region",
                "other_region",
                "unrelated_region",
            )
        },
        include_survey_action=True,
    )
    region_keys = {item["key"] for item in result.planner_input.known_world.nodes}
    assert {
        "target_region",
        "primary_region",
        "candidate_region",
        "other_region",
        "unrelated_region",
    } <= region_keys
    assert result.planner_input.known_world.resource_source_hints[0].candidate_region_keys == (
        "candidate_region",
        "other_region",
    )


def test_legacy_and_current_forms_have_equivalent_goal_context_planner_and_closure(
    session: Session,
) -> None:
    runtime, scope = _linjiang_runtime(session, "resource-source-canonical-equivalence")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "establish sustained emergency generation",
        resolved_goal=predefined_goal_resolution("establish_sustained_emergency_generation"),
        initialize_plan=False,
    )
    current_definition = ScenarioDefinitionV2.model_validate(
        LINJIANG_V2_TEST.model_dump(mode="json")
    )
    legacy_definition = ScenarioDefinitionV2.model_validate(_legacy_linjiang_document())
    assert legacy_definition == current_definition

    current_projection = SharedKnowledgeProjection(session, scope, current_definition)
    legacy_projection = SharedKnowledgeProjection(session, scope, legacy_definition)
    assert (
        legacy_projection.public_resource_source_hints()
        == current_projection.public_resource_source_hints()
    )

    builder = PlanningContextBuilder(session, scope)
    current_objectives = agent._objectives(task, current_definition)
    legacy_objectives = agent._objectives(task, legacy_definition)
    assert legacy_objectives == current_objectives
    current_context = builder.build(
        current_definition,
        current_objectives,
        task=task,
        replan_reason=None,
    )
    legacy_context = builder.build(
        legacy_definition,
        legacy_objectives,
        task=task,
        replan_reason=None,
    )
    assert legacy_context.goal == current_context.goal
    assert legacy_context.current_knowledge == current_context.current_knowledge

    current_closure = builder.build_v2_closure(
        current_definition,
        current_objectives,
        task=task,
        replan_reason=None,
    )
    legacy_closure = builder.build_v2_closure(
        legacy_definition,
        legacy_objectives,
        task=task,
        replan_reason=None,
    )
    assert legacy_closure.planner_input == current_closure.planner_input
    assert legacy_closure.relevance_reason == current_closure.relevance_reason
    assert (
        legacy_closure.planner_input.known_world.resource_source_hints
        == current_closure.planner_input.known_world.resource_source_hints
    )


def test_south_survey_reveals_emergency_fuel_but_not_gated_task6_requirement(
    session: Session,
) -> None:
    runtime, scope = _linjiang_runtime(session, "resource-source-south-survey")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "establish sustained emergency generation",
        resolved_goal=predefined_goal_resolution("establish_sustained_emergency_generation"),
        initialize_plan=False,
    )
    definition = LINJIANG_V2_TEST

    initial = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    assert not any(
        item.get("resource_key") == "emergency_fuel" and item.get("required_amount") == 100
        for item in initial.planner_input.known_world.unknown_dependencies
    )

    south_pool_key = "emergency_fuel@south_waterfront_district@south_emergency_fuel"
    before = session.get(GameInstanceResourceState, (runtime.instance.id, south_pool_key))
    assert before is not None
    assert before.visibility == ResourcePoolVisibility.HIDDEN
    water_team = session.get(GameInstanceActor, (runtime.instance.id, "water_repair_team_alpha"))
    assert water_team is not None
    water_team.command_reachability = CommandReachability.ONLINE.value

    outcome = GenericGameService(session, scope).execute(
        actor_key="water_repair_team_alpha",
        action_key="survey_resources",
        target_node_key="south_waterfront_district",
        parameters={},
    )
    assert outcome.outcome.failure is None

    after = session.get(GameInstanceResourceState, (runtime.instance.id, south_pool_key))
    assert after is not None
    assert after.visibility == ResourcePoolVisibility.VISIBLE
    assert after.value == 120
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "south_waterfront_district"),
    )
    assert knowledge is not None
    assert knowledge.resource_survey_completed is True

    projection = SharedKnowledgeProjection(session, scope, definition)
    public_hints = projection.public_resource_source_hints()
    assert any(item["resource_key"] == "emergency_fuel" for item in public_hints)
    visible_pool = next(
        item
        for item in projection.visible_resource_pools()
        if item.pool_key == "south_emergency_fuel"
    )
    assert visible_pool.quantity == 120

    after_survey = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason="INFORMATION_BOUNDARY",
    )
    assert not any(
        item.get("resource_key") == "emergency_fuel" and item.get("required_amount") == 100
        for item in after_survey.planner_input.known_world.unknown_dependencies
    )
    fuel_hints = {
        item.resource_key: item
        for item in after_survey.planner_input.known_world.resource_source_hints
    }
    assert fuel_hints["emergency_fuel"].primary_region_key is None
    assert fuel_hints["emergency_fuel"].candidate_region_keys == ("north_industrial_district",)
