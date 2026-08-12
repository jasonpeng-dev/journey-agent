from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import ClassVar

from app.core.errors import AppError
from app.scenarios.contracts import (
    GoalResolutionResult,
    ObjectiveContractError,
    ObjectiveResolutionStatus,
    ObjectiveScope,
    ScenarioObjectiveCatalog,
)
from app.scenarios.starfire.objective_catalog import StarfireObjectiveKey

RESOLVER_SOURCE = "STARFIRE_DETERMINISTIC"
RESOLVER_VERSION = "starfire-goal-resolver-v1"


class StarfireGoalResolver:
    """Resolve explicit Starfire goals without turning prerequisites into objectives."""

    _patterns: ClassVar[Mapping[str, tuple[str, ...]]] = {
        StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE.value: (
            r"侦察|偵察|情报|情報|探查|recon(?:naissance)?|intelligence|scout",
        ),
        StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value: (
            r"(?:守住|保卫|保衛|清剿|清除|确保安全|確保安全|secure|defend|clear|"
            r"make safe).{0,16}(?:山谷|valley)",
            r"(?:山谷|valley).{0,16}(?:安全|守住|secure|safe)",
        ),
        StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value: (
            r"(?:修复|修復|重建|恢复|恢復|restore|repair|rebuild).{0,20}(?:星火)?(?:驿站|前哨|outpost)",
            r"(?:星火)?(?:驿站|前哨|outpost).{0,20}(?:修复|修復|重建|恢复|恢復|restore|repair|operational)",
        ),
        StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value: (
            r"(?:开放|開放|开通|開通|打通|打开|打開|恢复|恢復|open|reopen).{0,20}"
            r"(?:商路|贸易路线|貿易路線|trade route)",
            r"(?:商路|贸易路线|貿易路線|trade route).{0,20}(?:开放|開放|开通|開通|恢复|恢復|open)",
        ),
    }
    _full_patterns: ClassVar[tuple[str, ...]] = (
        r"完整恢复|完整恢復|全面恢复|全面恢復|恢复整个北方|恢復整個北方",
        r"full northern recovery|restore (?:the )?(?:whole|entire) north",
    )
    _scenario_terms: ClassVar[re.Pattern[str]] = re.compile(
        r"北方|北境|星火|山谷|驿站|前哨|商路|贸易路线|貿易路線|north|starfire|valley|outpost|trade",
        re.IGNORECASE,
    )

    def __init__(self, catalog: ScenarioObjectiveCatalog):
        self.catalog = catalog

    def resolve(self, raw_goal: str) -> GoalResolutionResult:
        normalized = " ".join(raw_goal.strip().lower().split())
        if not normalized:
            return self._unsupported()
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in self._full_patterns):
            return self._resolved([StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value])

        keys = [
            key
            for key, patterns in self._patterns.items()
            if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)
        ]
        if keys:
            return self._resolved(keys)
        if self._scenario_terms.search(normalized):
            return GoalResolutionResult(
                status=ObjectiveResolutionStatus.NEEDS_CLARIFICATION,
                candidate_scopes=tuple(
                    self.catalog.scope([key]) for key in self.catalog.definitions
                ),
                clarification_prompt=(
                    "Please choose the exact Starfire objective: gather intelligence, secure "
                    "the valley, restore the outpost, open the trade route, or full recovery."
                ),
                resolver_source=RESOLVER_SOURCE,
                resolver_version=RESOLVER_VERSION,
            )
        return self._unsupported()

    def confirm_candidate(
        self,
        candidate_scopes: Iterable[ObjectiveScope],
        objective_keys: Iterable[str],
    ) -> GoalResolutionResult:
        try:
            requested = self.catalog.scope(objective_keys)
        except ObjectiveContractError as exc:
            raise AppError(
                "GOAL_RESOLUTION_OUTPUT_INVALID",
                "Goal resolution selected an objective outside the scenario catalog",
                status_code=409,
                details={"contract_code": exc.code},
            ) from exc
        if requested not in tuple(candidate_scopes):
            raise AppError(
                "GOAL_CLARIFICATION_SELECTION_INVALID",
                "The selected objective scope is not one of the clarification candidates",
                status_code=409,
            )
        return GoalResolutionResult(
            status=ObjectiveResolutionStatus.RESOLVED,
            scope=requested,
            resolver_source=RESOLVER_SOURCE,
            resolver_version=RESOLVER_VERSION,
        )

    def _resolved(self, keys: Iterable[str]) -> GoalResolutionResult:
        return GoalResolutionResult(
            status=ObjectiveResolutionStatus.RESOLVED,
            scope=self.catalog.scope(keys),
            resolver_source=RESOLVER_SOURCE,
            resolver_version=RESOLVER_VERSION,
        )

    @staticmethod
    def _unsupported() -> GoalResolutionResult:
        return GoalResolutionResult(
            status=ObjectiveResolutionStatus.UNSUPPORTED,
            resolver_source=RESOLVER_SOURCE,
            resolver_version=RESOLVER_VERSION,
        )
