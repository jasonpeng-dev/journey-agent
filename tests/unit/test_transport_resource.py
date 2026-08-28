from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import (
    CommandReachability,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import (
    ScenarioDefinitionV2,
    normalize_action_parameters,
    transport_resource_entries,
)
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService


def _definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = {
        "schema_version": 2,
        "metadata": {
            "key": "synthetic_transport",
            "name": "Synthetic Transport",
            "description": "A minimal generic transport contract fixture.",
            "locality": {
                "enabled": True,
                "scoped_resources": True,
                "region_node_type_key": "region",
                "facility_node_type_key": "facility",
                "transport_node_type_key": "transport",
                "located_in_relation_type_key": "located_in",
                "transport_endpoint_relation_type_key": "endpoint",
                "passability_fact_key": "passable",
            },
        },
        "engine_contract": {"key": "declarative-rule-engine", "version": "1"},
        "initialization": {
            "start_node_key": "region_a",
            "primary_actor_key": "carrier",
            "resource_pools": [
                {
                    "pool_key": "source_alpha",
                    "resource_key": "cargo_alpha",
                    "region_key": "region_a",
                    "quantity": 10,
                    "visibility": "VISIBLE",
                    "availability": "AVAILABLE",
                },
                {
                    "pool_key": "source_beta",
                    "resource_key": "cargo_beta",
                    "region_key": "region_a",
                    "quantity": 15,
                    "visibility": "VISIBLE",
                    "availability": "AVAILABLE",
                },
                {
                    "pool_key": "known_base_alpha",
                    "resource_key": "cargo_alpha",
                    "region_key": "region_b",
                    "quantity": 5,
                    "visibility": "VISIBLE",
                    "availability": "AVAILABLE",
                },
                {
                    "pool_key": "hidden_base_alpha",
                    "resource_key": "cargo_alpha",
                    "region_key": "region_b",
                    "quantity": 50,
                    "visibility": "HIDDEN",
                    "availability": "AVAILABLE",
                },
            ],
            "region_resource_knowledge": [
                {
                    "region_key": "region_a",
                    "resource_inventory_visibility": "VISIBLE",
                    "resource_survey_completed": True,
                },
                {
                    "region_key": "region_b",
                    "resource_inventory_visibility": "HIDDEN",
                    "resource_survey_completed": False,
                },
                {
                    "region_key": "region_c",
                    "resource_inventory_visibility": "HIDDEN",
                    "resource_survey_completed": False,
                },
            ],
        },
        "world": {
            "key": "synthetic_transport",
            "name": "Synthetic Transport",
            "node_types": [
                {"key": "region", "name": "Region"},
                {"key": "facility", "name": "Facility"},
                {"key": "transport", "name": "Transport"},
            ],
            "nodes": [
                {
                    "key": region_key,
                    "name": region_key.replace("_", " ").title(),
                    "node_type_key": "region",
                    "initial_access": "AVAILABLE",
                    "initial_visibility": "KNOWN",
                    "interaction_keys": ["transport_destination"],
                }
                for region_key in ("region_a", "region_b", "region_c")
            ]
            + [
                {
                    "key": "edge_ab",
                    "name": "A-B edge",
                    "node_type_key": "transport",
                    "initial_access": "AVAILABLE",
                    "initial_visibility": "KNOWN",
                    "facts": [
                        {
                            "key": "passable",
                            "name": "Passable",
                            "value_type": "BOOLEAN",
                            "initial_value": True,
                            "initial_visibility": "KNOWN",
                        }
                    ],
                },
                {
                    "key": "edge_bc",
                    "name": "B-C edge",
                    "node_type_key": "transport",
                    "initial_access": "AVAILABLE",
                    "initial_visibility": "KNOWN",
                    "facts": [
                        {
                            "key": "passable",
                            "name": "Passable",
                            "value_type": "BOOLEAN",
                            "initial_value": True,
                            "initial_visibility": "KNOWN",
                        }
                    ],
                },
                {
                    "key": "warehouse",
                    "name": "Warehouse",
                    "node_type_key": "facility",
                    "initial_access": "AVAILABLE",
                    "initial_visibility": "KNOWN",
                },
            ],
            "relations": [
                {
                    "source_node_key": "edge_ab",
                    "relation_type_key": "endpoint",
                    "target_node_key": "region_a",
                },
                {
                    "source_node_key": "edge_ab",
                    "relation_type_key": "endpoint",
                    "target_node_key": "region_b",
                },
                {
                    "source_node_key": "edge_bc",
                    "relation_type_key": "endpoint",
                    "target_node_key": "region_b",
                },
                {
                    "source_node_key": "edge_bc",
                    "relation_type_key": "endpoint",
                    "target_node_key": "region_c",
                },
                {
                    "source_node_key": "warehouse",
                    "relation_type_key": "located_in",
                    "target_node_key": "region_a",
                },
            ],
            "resources": [
                {
                    "key": "cargo_alpha",
                    "name": "Cargo Alpha",
                    "initial_value": 0,
                    "minimum": 0,
                    "maximum": 100,
                },
                {
                    "key": "cargo_beta",
                    "name": "Cargo Beta",
                    "initial_value": 0,
                    "minimum": 0,
                    "maximum": 100,
                },
            ],
        },
        "actors": {
            "roles": [
                {
                    "key": "carrier_role",
                    "name": "Carrier",
                    "capabilities": ["PLAN", "EXECUTE_ACTION", "LOGISTICS"],
                }
            ],
            "actor_profiles": [
                {
                    "key": "carrier",
                    "name": "Carrier",
                    "role_key": "carrier_role",
                    "persona": "A logistics operator.",
                    "initial_node_key": "region_a",
                    "allowed_action_keys": ["transport_resource"],
                }
            ],
        },
        "interactions": [
            {
                "key": "transport_destination",
                "name": "Transport Destination",
                "description": "A Region that can receive transported cargo.",
            }
        ],
        "actions": [
            {
                "key": "transport_resource",
                "name": "Transport Resource",
                "description": "Carry cargo across one transport edge.",
                "required_interaction_key": "transport_destination",
                "execution_mode": "IMMEDIATE",
                "parameters": [
                    {
                        "key": "resource_key",
                        "name": "Resource",
                        "value_type": "STRING",
                    },
                    {
                        "key": "amount",
                        "name": "Amount",
                        "value_type": "INTEGER",
                        "minimum": 1,
                        "maximum": 100,
                    },
                ],
                "allowed_actor_capabilities": ["EXECUTE_ACTION"],
                "expected_outcomes": [
                    {"code": "TRANSPORTED", "name": "Transported", "success": True}
                ],
                "planning": {
                    "terminal_effects": [{"node_key": "region_c", "fact_key": "delivered"}],
                    "success_outcome_codes": ["TRANSPORTED"],
                },
                "behavior": "TRANSPORT_RESOURCE",
                "locality": "TRANSPORT_ENDPOINT",
            }
        ],
        "rules": [
            {
                "key": "transport_succeeds",
                "phase": "RESOLVE",
                "action_key": "transport_resource",
                "priority": 0,
                "effects": [{"kind": "EMIT_OUTCOME", "outcome_code": "TRANSPORTED"}],
            }
        ],
        "objectives": [
            {
                "key": "deliver_cargo",
                "name": "Deliver Cargo",
                "description": "Deliver cargo to the final Region.",
                "completion_requirements": [
                    {
                        "key": "cargo_delivered",
                        "node_key": "region_c",
                        "fact_key": "delivered",
                        "accepted_values": [True],
                        "description": "Cargo has been delivered.",
                    }
                ],
            }
        ],
        "goal_resolution": {
            "allow_llm_fallback": False,
            "clarification_prompt": "Choose a cargo destination.",
        },
    }
    region_c = next(node for node in document["world"]["nodes"] if node["key"] == "region_c")
    region_c["facts"] = [
        {
            "key": "delivered",
            "name": "Delivered",
            "value_type": "BOOLEAN",
            "initial_value": False,
            "initial_visibility": "KNOWN",
        }
    ]
    return ScenarioDefinitionV2.model_validate(document)


def _runtime(session: Session, definition: ScenarioDefinitionV2, key: str) -> tuple[Any, Any]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
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


def _transport(
    session: Session,
    scope: Any,
    *,
    target_key: str,
    parameters: dict[str, object],
    key: str,
) -> Any:
    return GenericActionService(session, scope).execute_action(
        actor_key="carrier",
        action_key="transport_resource",
        target_key=target_key,
        parameters=parameters,
        idempotency_key=key,
    )


def _pool(session: Session, instance_id: Any, resource_key: str, region_key: str, pool_key: str):
    return session.get(
        GameInstanceResourceState,
        (instance_id, f"{resource_key}@{region_key}@{pool_key}"),
    )


def test_transport_parameters_normalize_legacy_and_structured_cargo() -> None:
    action = _definition().actions[0]
    assert transport_resource_entries({"resource_key": "cargo_alpha", "amount": 10}) == (
        ("cargo_alpha", 10),
    )
    assert normalize_action_parameters(
        action,
        {
            "resource_key": "cargo_alpha",
            "amount": 10,
        },
    ) == {"resources": [{"resource_key": "cargo_alpha", "amount": 10}]}
    assert normalize_action_parameters(
        action,
        {
            "resources": [
                {"resource_key": "cargo_alpha", "amount": 10},
                {"resource_key": "cargo_beta", "amount": 15},
            ]
        },
    )["resources"] == [
        {"resource_key": "cargo_alpha", "amount": 10},
        {"resource_key": "cargo_beta", "amount": 15},
    ]
    with pytest.raises(ValueError, match="cannot repeat"):
        transport_resource_entries(
            {
                "resources": [
                    {"resource_key": "cargo_alpha", "amount": 1},
                    {"resource_key": "cargo_alpha", "amount": 1},
                ]
            }
        )
    with pytest.raises(ValueError, match="positive integer"):
        transport_resource_entries({"resources": [{"resource_key": "cargo_alpha", "amount": 0}]})


def test_legacy_transport_moves_one_cargo_and_actor(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "legacy-transport")
    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
        key="legacy-transport-1",
    )

    assert result.applied is not None
    assert result.applied.outcome.failure is None
    assert result.operation.parameters == {
        "resources": [{"resource_key": "cargo_alpha", "amount": 10}]
    }
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    source = _pool(session, runtime.instance.id, "cargo_alpha", "region_a", "source_alpha")
    inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_b",
        "__runtime_known_inflow__",
    )
    assert actor is not None and actor.current_node_key == "region_b"
    assert source is not None and source.value == 0
    assert inflow is not None and inflow.value == 10


def test_multi_resource_transport_moves_all_cargo_and_actor_atomically(
    session: Session,
) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "multi-transport")
    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={
            "resources": [
                {"resource_key": "cargo_alpha", "amount": 10},
                {"resource_key": "cargo_beta", "amount": 15},
            ]
        },
        key="multi-transport-1",
    )

    assert result.applied is not None and result.applied.outcome.failure is None
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    alpha_source = _pool(session, runtime.instance.id, "cargo_alpha", "region_a", "source_alpha")
    beta_source = _pool(session, runtime.instance.id, "cargo_beta", "region_a", "source_beta")
    alpha_inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_b",
        "__runtime_known_inflow__",
    )
    beta_inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_beta",
        "region_b",
        "__runtime_known_inflow__",
    )
    assert actor is not None and actor.current_node_key == "region_b"
    assert alpha_source is not None and alpha_source.value == 0
    assert beta_source is not None and beta_source.value == 0
    assert alpha_inflow is not None and alpha_inflow.value == 10
    assert beta_inflow is not None and beta_inflow.value == 15
    assert result.operation.outcome is not None
    assert result.operation.outcome["actor_location_update"] == "region_b"


def test_transport_sequentially_uses_projected_actor_and_known_inflow(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "sequential-transport")
    first = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 10}]},
        key="sequential-transport-1",
    )
    second = _transport(
        session,
        scope,
        target_key="region_c",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 10}]},
        key="sequential-transport-2",
    )

    assert first.applied is not None and first.applied.outcome.failure is None
    assert second.applied is not None and second.applied.outcome.failure is None
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    b_inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_b",
        "__runtime_known_inflow__",
    )
    c_inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_c",
        "__runtime_known_inflow__",
    )
    b_knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "region_b"),
    )
    assert actor is not None and actor.current_node_key == "region_c"
    assert b_inflow is not None and b_inflow.value == 0
    assert c_inflow is not None and c_inflow.value == 10
    assert b_knowledge is not None
    assert b_knowledge.resource_inventory_visibility == ResourceInventoryVisibility.HIDDEN
    assert b_knowledge.resource_survey_completed is False


def test_validator_projects_transport_actor_location_for_the_next_hop(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "projected-transport")
    agent = GenericAgentService(session, scope)
    actors = {
        actor.actor_key: actor
        for actor in session.query(GameInstanceActor).all()
        if actor.game_instance_id == runtime.instance.id
    }
    action = definition.actions[0]
    locations = {key: actor.current_node_key for key, actor in actors.items()}
    passability = agent._known_passability(definition)
    facts = agent._known_fact_projection()
    nodes = agent._known_node_keys()
    relations = agent._known_relation_keys(definition)
    reachability = {
        key: CommandReachability(actor.command_reachability) for key, actor in actors.items()
    }
    pools, knowledge = agent._projected_resource_state(definition)
    balance: dict[tuple[str | None, str], int] = {}
    parameters = {"resources": [{"resource_key": "cargo_alpha", "amount": 10}]}

    assert (
        agent._validate_projected_action_state(
            definition,
            action,
            "carrier",
            "region_b",
            parameters,
            locations,
            passability,
            facts,
            nodes,
            relations,
            actors=actors,
            projected_command_reachability=reachability,
        )
        == "edge_ab"
    )
    agent._validate_and_advance_projected_resources(
        definition,
        action,
        "carrier",
        "region_b",
        parameters,
        locations,
        pools,
        knowledge,
        balance,
        (),
    )
    agent._advance_projected_action_state(
        definition,
        action,
        "carrier",
        "region_b",
        parameters,
        locations,
        passability,
        facts,
        nodes,
        relations,
        (),
        projected_command_reachability=reachability,
    )

    assert locations["carrier"] == "region_b"
    assert (
        agent._validate_projected_action_state(
            definition,
            action,
            "carrier",
            "region_c",
            parameters,
            locations,
            passability,
            facts,
            nodes,
            relations,
            actors=actors,
            projected_command_reachability=reachability,
        )
        == "edge_bc"
    )


def test_multi_resource_shortfall_does_not_partially_mutate(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "multi-shortfall")
    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={
            "resources": [
                {"resource_key": "cargo_alpha", "amount": 5},
                {"resource_key": "cargo_beta", "amount": 16},
            ]
        },
        key="multi-shortfall-1",
    )

    assert result.applied is not None
    assert result.applied.outcome.failure is not None
    assert result.applied.outcome.failure.code == "TRANSPORT_RESOURCE_INSUFFICIENT"
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    alpha_source = _pool(session, runtime.instance.id, "cargo_alpha", "region_a", "source_alpha")
    beta_source = _pool(session, runtime.instance.id, "cargo_beta", "region_a", "source_beta")
    alpha_inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_b",
        "__runtime_known_inflow__",
    )
    assert actor is not None and actor.current_node_key == "region_a"
    assert alpha_source is not None and alpha_source.value == 10
    assert beta_source is not None and beta_source.value == 15
    assert alpha_inflow is None


def test_unknown_base_cannot_be_used_beyond_known_inflow(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "unknown-shortfall")
    first = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 10}]},
        key="unknown-shortfall-1",
    )
    second = _transport(
        session,
        scope,
        target_key="region_c",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 15}]},
        key="unknown-shortfall-2",
    )

    assert first.applied is not None and first.applied.outcome.failure is None
    assert second.applied is not None
    assert second.applied.outcome.failure is not None
    assert second.applied.outcome.failure.code == "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN"
    b_inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_b",
        "__runtime_known_inflow__",
    )
    c_inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_c",
        "__runtime_known_inflow__",
    )
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    assert b_inflow is not None and b_inflow.value == 10
    assert c_inflow is None
    assert actor is not None and actor.current_node_key == "region_b"


def test_known_base_and_known_inflow_can_be_combined(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "known-base-inflow")
    first = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 10}]},
        key="known-base-inflow-1",
    )
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "region_b"),
    )
    assert knowledge is not None
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    knowledge.resource_survey_completed = True
    session.flush()
    second = _transport(
        session,
        scope,
        target_key="region_c",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 12}]},
        key="known-base-inflow-2",
    )

    assert first.applied is not None and first.applied.outcome.failure is None
    assert second.applied is not None and second.applied.outcome.failure is None
    base = _pool(session, runtime.instance.id, "cargo_alpha", "region_b", "known_base_alpha")
    inflow = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_b",
        "__runtime_known_inflow__",
    )
    assert base is not None and base.value == 3
    assert inflow is not None and inflow.value == 0


def test_unsurveyed_region_without_inflow_remains_unknown(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "unknown-without-inflow")
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    assert actor is not None
    actor.current_node_key = "region_b"
    session.flush()
    result = _transport(
        session,
        scope,
        target_key="region_c",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 1}]},
        key="unknown-without-inflow-1",
    )

    assert result.applied is not None
    assert result.applied.outcome.failure is not None
    assert result.applied.outcome.failure.code == "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN"


def test_multi_resource_transport_rejects_duplicate_and_invalid_amounts() -> None:
    with pytest.raises(ValueError):
        transport_resource_entries(
            {
                "resources": [
                    {"resource_key": "cargo_alpha", "amount": 1},
                    {"resource_key": "cargo_alpha", "amount": 2},
                ]
            }
        )
    with pytest.raises(ValueError):
        transport_resource_entries({"resources": [{"resource_key": "cargo_alpha", "amount": -1}]})


def test_transport_source_pools_keep_their_public_state_contract(session: Session) -> None:
    definition = _definition()
    runtime, scope = _runtime(session, definition, "public-state-contract")
    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 10}]},
        key="public-state-contract-1",
    )
    assert result.applied is not None and result.applied.outcome.failure is None
    hidden = _pool(
        session,
        runtime.instance.id,
        "cargo_alpha",
        "region_b",
        "hidden_base_alpha",
    )
    assert hidden is not None
    assert hidden.value == 50
    assert hidden.visibility == ResourcePoolVisibility.HIDDEN
    assert hidden.availability == ResourcePoolAvailability.AVAILABLE
