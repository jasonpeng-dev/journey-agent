from dataclasses import FrozenInstanceError

import pytest

from app.domain.world import AccessState, Visibility
from app.scenarios.contracts import (
    GoalResolutionResult,
    ObjectiveContractError,
    ObjectiveResolutionStatus,
    project_known_relations,
)
from app.scenarios.starfire.definition import STARFIRE_WORLD
from app.scenarios.starfire.objective_catalog import (
    STARFIRE_OBJECTIVE_CATALOG,
    StarfireObjectiveKey,
)
from app.scenarios.starfire.ruleset import (
    StarfireFactState,
    StarfireResources,
    StarfireRuleset,
    StarfireRuleState,
)


@pytest.mark.parametrize(
    ("objective_key", "overrides", "completed"),
    [
        (StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE, {}, False),
        (
            StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE,
            {"valley_intelligence": "PARTIAL"},
            True,
        ),
        (
            StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE,
            {"valley_intelligence": "COMPLETE"},
            True,
        ),
        (StarfireObjectiveKey.SECURE_NORTHERN_VALLEY, {}, False),
        (
            StarfireObjectiveKey.SECURE_NORTHERN_VALLEY,
            {"valley_security": "SAFE"},
            True,
        ),
        (StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST, {}, False),
        (
            StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST,
            {"outpost_status": "OPERATIONAL"},
            True,
        ),
        (
            StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST,
            {"outpost_status": "RESTORED"},
            True,
        ),
        (StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE, {}, False),
        (
            StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE,
            {"trade_route_status": "OPEN"},
            True,
        ),
        (StarfireObjectiveKey.FULL_NORTHERN_RECOVERY, {}, False),
        (
            StarfireObjectiveKey.FULL_NORTHERN_RECOVERY,
            {
                "valley_security": "SAFE",
                "outpost_status": "OPERATIONAL",
                "trade_route_status": "OPEN",
            },
            True,
        ),
    ],
)
def test_starfire_objective_completion_matrix(
    objective_key: StarfireObjectiveKey,
    overrides: dict[str, str],
    completed: bool,
) -> None:
    scope = STARFIRE_OBJECTIVE_CATALOG.scope([objective_key])

    evaluation = STARFIRE_OBJECTIVE_CATALOG.evaluate(scope, _state(**overrides))

    assert evaluation.completed is completed
    assert len(evaluation.objectives) == 1
    assert evaluation.objectives[0].objective_key == objective_key.value


def test_multi_objective_scope_uses_and_completion() -> None:
    scope = STARFIRE_OBJECTIVE_CATALOG.scope(
        [
            StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST,
            StarfireObjectiveKey.SECURE_NORTHERN_VALLEY,
        ]
    )

    incomplete = STARFIRE_OBJECTIVE_CATALOG.evaluate(
        scope,
        _state(valley_security="SAFE", outpost_status="DAMAGED"),
    )
    complete = STARFIRE_OBJECTIVE_CATALOG.evaluate(
        scope,
        _state(valley_security="SAFE", outpost_status="OPERATIONAL"),
    )

    assert not incomplete.completed
    assert complete.completed
    assert [item.completed for item in incomplete.objectives] == [False, True]


def test_scope_normalization_is_stable_unique_and_immutable() -> None:
    scope = STARFIRE_OBJECTIVE_CATALOG.scope(
        [
            StarfireObjectiveKey.SECURE_NORTHERN_VALLEY,
            StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST,
            StarfireObjectiveKey.SECURE_NORTHERN_VALLEY,
        ]
    )

    assert scope.objective_keys == (
        StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value,
        StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value,
    )
    assert isinstance(scope.objective_keys, tuple)
    with pytest.raises(FrozenInstanceError):
        scope.catalog_version = "changed"  # type: ignore[misc]


def test_invalid_and_redundant_objective_scopes_fail_closed() -> None:
    with pytest.raises(ObjectiveContractError) as unsupported:
        STARFIRE_OBJECTIVE_CATALOG.scope(["INVENTED_OBJECTIVE"])
    assert unsupported.value.code == "OBJECTIVE_NOT_SUPPORTED"

    with pytest.raises(ObjectiveContractError) as redundant:
        STARFIRE_OBJECTIVE_CATALOG.scope(
            [
                StarfireObjectiveKey.FULL_NORTHERN_RECOVERY,
                StarfireObjectiveKey.SECURE_NORTHERN_VALLEY,
            ]
        )
    assert redundant.value.code == "OBJECTIVE_SCOPE_REDUNDANT"

    with pytest.raises(ObjectiveContractError) as empty:
        STARFIRE_OBJECTIVE_CATALOG.scope([])
    assert empty.value.code == "OBJECTIVE_SCOPE_EMPTY"


def test_full_recovery_subsumes_only_its_legacy_terminal_conjunction() -> None:
    full_with_intelligence = STARFIRE_OBJECTIVE_CATALOG.scope(
        [
            StarfireObjectiveKey.FULL_NORTHERN_RECOVERY,
            StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE,
        ]
    )

    assert full_with_intelligence.objective_keys == (
        StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value,
        StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE.value,
    )


def test_prerequisites_are_public_constraints_and_do_not_expand_scope() -> None:
    scope = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE])
    original_keys = scope.objective_keys

    prerequisites = STARFIRE_OBJECTIVE_CATALOG.prerequisites(scope)

    assert {item.key for item in prerequisites} == {
        "valley_security_required",
        "outpost_operation_required",
        "trade_support_required",
    }
    assert scope.objective_keys == original_keys
    assert StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value not in scope.objective_keys
    assert StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value not in scope.objective_keys


def test_restore_side_effect_does_not_add_trade_objective() -> None:
    scope = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST])
    outcome = StarfireRuleset().resolve_repair(
        "starfire_outpost",
        "TEMPORARY",
        _state(valley_security="SAFE"),
    )

    evaluation = STARFIRE_OBJECTIVE_CATALOG.evaluate(
        scope,
        _state(
            valley_security="SAFE",
            outpost_status="OPERATIONAL",
            trade_route_status="CLOSED",
        ),
    )

    assert "northern_trade_route" in outcome.unlock_node_keys
    assert evaluation.completed
    assert scope.objective_keys == (StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value,)
    assert StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value not in scope.objective_keys


def test_relation_projection_requires_both_endpoints_known_and_keeps_locked_nodes() -> None:
    known_access = {
        node.key: node.initial_access
        for node in STARFIRE_WORLD.nodes
        if node.initial_visibility == Visibility.KNOWN
    }

    initial = project_known_relations(STARFIRE_WORLD.relations, known_access)

    assert initial
    assert all(
        relation.source_node_key in known_access and relation.target_node_key in known_access
        for relation in initial
    )
    assert all(
        "enemy_north_supply_route" not in {relation.source_node_key, relation.target_node_key}
        for relation in initial
    )
    assert any(
        relation.source_node_key == "northern_valley"
        and relation.target_node_key == "starfire_outpost"
        for relation in initial
    )
    assert known_access["starfire_outpost"] == AccessState.LOCKED

    after_discovery_access = {
        **known_access,
        "enemy_north_supply_route": AccessState.LOCKED,
    }
    after_discovery = project_known_relations(
        STARFIRE_WORLD.relations,
        after_discovery_access,
    )
    assert any(
        "enemy_north_supply_route" in {relation.source_node_key, relation.target_node_key}
        for relation in after_discovery
    )


def test_verification_and_evaluation_share_definition_requirements() -> None:
    scope = STARFIRE_OBJECTIVE_CATALOG.scope(
        [
            StarfireObjectiveKey.SECURE_NORTHERN_VALLEY,
            StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST,
        ]
    )
    requirements = STARFIRE_OBJECTIVE_CATALOG.verification_requirements(scope)
    evaluation = STARFIRE_OBJECTIVE_CATALOG.evaluate(
        scope,
        _state(valley_security="SAFE", outpost_status="OPERATIONAL"),
    )

    expected = tuple(
        requirement
        for objective_key in scope.objective_keys
        for requirement in STARFIRE_OBJECTIVE_CATALOG.definition(
            objective_key
        ).completion_requirements
    )
    evaluated = tuple(
        item.requirement for objective in evaluation.objectives for item in objective.requirements
    )

    assert requirements == expected
    assert evaluated == expected


def test_goal_resolution_contract_separates_ambiguity_from_confirmed_scope() -> None:
    intelligence = STARFIRE_OBJECTIVE_CATALOG.scope(
        [StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE]
    )
    security = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.SECURE_NORTHERN_VALLEY])

    ambiguous = GoalResolutionResult(
        status=ObjectiveResolutionStatus.NEEDS_CLARIFICATION,
        candidate_scopes=(intelligence, security),
        clarification_prompt="Do you want intelligence only, or a secure valley?",
        resolver_source="contract-test",
        resolver_version="v1",
    )
    confirmed = GoalResolutionResult(
        status=ObjectiveResolutionStatus.CONFIRMED,
        scope=security,
        resolver_source="player-confirmation",
        resolver_version="v1",
    )

    assert ambiguous.scope is None
    assert confirmed.scope is security
    with pytest.raises(ObjectiveContractError) as invalid:
        GoalResolutionResult(status=ObjectiveResolutionStatus.RESOLVED)
    assert invalid.value.code == "GOAL_RESOLUTION_SCOPE_REQUIRED"


def _state(**overrides: str) -> StarfireRuleState:
    values = {
        "village_support": "NONE",
        "valley_intelligence": "INCOMPLETE",
        "valley_security": "UNSAFE",
        "ambush_status": "ACTIVE",
        "supply_status": "ACTIVE",
        "outpost_status": "DAMAGED",
        "trade_route_status": "CLOSED",
        **overrides,
    }
    return StarfireRuleState(
        facts={
            ("north_village", "village_support"): StarfireFactState(values["village_support"]),
            ("northern_valley", "valley_intelligence"): StarfireFactState(
                values["valley_intelligence"]
            ),
            ("northern_valley", "valley_security"): StarfireFactState(values["valley_security"]),
            ("northern_valley", "ambush_status"): StarfireFactState(values["ambush_status"]),
            ("enemy_north_supply_route", "supply_status"): StarfireFactState(
                values["supply_status"]
            ),
            ("starfire_outpost", "outpost_status"): StarfireFactState(values["outpost_status"]),
            ("northern_trade_route", "trade_route_status"): StarfireFactState(
                values["trade_route_status"]
            ),
        },
        resources=StarfireResources(soldiers_available=300, food=100, gold=80, morale=60),
    )
