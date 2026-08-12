"""Scenario-aware, fail-closed resolution of interaction targets."""

from collections.abc import Mapping

from app.core.errors import AppError
from app.domain.world import NodeDefinition
from app.scenarios.registry import SCENARIO_WORLDS, ScenarioWorldBinding


class InteractionTargetResolver:
    """Resolve raw scenario targets without embedding scenario-specific node keys."""

    def __init__(self, scenarios: Mapping[str, ScenarioWorldBinding]) -> None:
        self._scenarios = scenarios

    def resolve_target(
        self,
        scenario_key: str,
        raw_target_key: str,
        required_interaction: str,
    ) -> NodeDefinition:
        scenario = self._scenario(scenario_key)
        self._registered_interaction(scenario, required_interaction)
        canonical_key = scenario.resolve_node_key(raw_target_key)
        node = scenario.world.node(canonical_key)
        if node is None:
            raise AppError(
                "INTERACTION_TARGET_NOT_FOUND",
                "The interaction target does not exist in the current scenario",
                details={
                    "scenario_key": scenario_key,
                    "raw_target_key": raw_target_key,
                    "canonical_target_key": canonical_key,
                },
            )
        if not scenario.raw_target_supports_interaction(
            raw_target_key,
            required_interaction,
        ):
            raise AppError(
                "LEGACY_TARGET_INTERACTION_INVALID",
                "The legacy target does not permit the requested interaction",
                details={
                    "scenario_key": scenario_key,
                    "raw_target_key": raw_target_key,
                    "canonical_target_key": canonical_key,
                    "required_interaction": required_interaction,
                },
            )
        if not node.supports(required_interaction):
            raise AppError(
                "INTERACTION_NOT_SUPPORTED",
                "The target node does not support the requested interaction",
                details={
                    "scenario_key": scenario_key,
                    "target_key": canonical_key,
                    "required_interaction": required_interaction,
                },
            )
        return node

    def resolve_unique_target(
        self,
        scenario_key: str,
        required_interaction: str,
    ) -> NodeDefinition:
        scenario = self._scenario(scenario_key)
        self._registered_interaction(scenario, required_interaction)
        candidates = tuple(
            node for node in scenario.world.nodes if node.supports(required_interaction)
        )
        if not candidates:
            raise AppError(
                "INTERACTION_TARGET_NOT_FOUND",
                "No node supports the requested interaction in the current scenario",
                details={
                    "scenario_key": scenario_key,
                    "required_interaction": required_interaction,
                },
            )
        if len(candidates) != 1:
            raise AppError(
                "INTERACTION_TARGET_AMBIGUOUS",
                "The requested interaction does not identify one unique target",
                details={
                    "scenario_key": scenario_key,
                    "required_interaction": required_interaction,
                    "candidate_keys": [node.key for node in candidates],
                },
            )
        return candidates[0]

    def _scenario(self, scenario_key: str) -> ScenarioWorldBinding:
        scenario = self._scenarios.get(scenario_key)
        if scenario is None:
            raise AppError(
                "SCENARIO_NOT_FOUND",
                "The scenario definition is not registered",
                details={"scenario_key": scenario_key},
            )
        return scenario

    @staticmethod
    def _registered_interaction(
        scenario: ScenarioWorldBinding,
        required_interaction: str,
    ) -> None:
        if scenario.world.interaction(required_interaction) is None:
            raise AppError(
                "INTERACTION_NOT_REGISTERED",
                "The required interaction is not registered in the scenario",
                details={
                    "scenario_key": scenario.world.key,
                    "required_interaction": required_interaction,
                },
            )


interaction_target_resolver = InteractionTargetResolver(SCENARIO_WORLDS)
