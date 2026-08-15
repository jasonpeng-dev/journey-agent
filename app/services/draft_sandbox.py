"""Request-scoped isolated Runtime tests for mutable Scenario Drafts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import Player
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.validation import (
    ScenarioDefinitionValidator,
    ScenarioValidationIssue,
)
from app.services.game_lifecycle import PLATFORM_PLAYER_ID
from app.services.play import PlayOrchestrator
from app.services.player_projection import PlayerProjectionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioLifecycleError, ScenarioService


@dataclass(frozen=True, slots=True)
class DraftSandboxResult:
    scenario_id: UUID
    revision: int
    started: bool
    issues: tuple[ScenarioValidationIssue, ...]
    goal_status: str | None = None
    player_state: Any | None = None


class DraftSandboxService:
    """Validate in the caller DB, then run only in a disposable in-memory DB."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def test(
        self,
        scenario_id: UUID,
        *,
        expected_revision: int,
        goal: str | None,
    ) -> DraftSandboxResult:
        scenario = ScenarioService(self.db).get_scenario(scenario_id)
        if scenario.status == "ARCHIVED":
            raise ScenarioLifecycleError(
                "SCENARIO_ARCHIVED", "An archived Scenario cannot start a Draft sandbox"
            )
        draft = ScenarioService(self.db).get_draft(scenario_id)
        if draft.revision != expected_revision:
            raise ScenarioLifecycleError(
                "SCENARIO_DRAFT_CONFLICT",
                "The Scenario Draft revision changed before sandbox startup",
            )
        validation = ScenarioDefinitionValidator().validate(draft.definition_document)
        if not validation.passed or validation.definition is None:
            return DraftSandboxResult(
                scenario_id=scenario_id,
                revision=draft.revision,
                started=False,
                issues=validation.issues,
            )
        return self._run(
            scenario_id=scenario_id,
            revision=draft.revision,
            definition=validation.definition,
            issues=validation.issues,
            goal=goal.strip() if goal and goal.strip() else None,
        )

    @staticmethod
    def _run(
        *,
        scenario_id: UUID,
        revision: int,
        definition: ScenarioDefinitionV2,
        issues: tuple[ScenarioValidationIssue, ...],
        goal: str | None,
    ) -> DraftSandboxResult:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            Base.metadata.create_all(engine)
            with Session(engine, expire_on_commit=False) as sandbox:
                sandbox.add(Player(id=PLATFORM_PLAYER_ID, name="Draft Sandbox Player"))
                scenario = ScenarioDefinitionRepository(sandbox).persist_initial_draft(definition)
                version = (
                    ScenarioService(sandbox).publish_draft(scenario.id, expected_revision=1).version
                )
                runtime = RuntimeInitializationService(sandbox).create(
                    player_id=PLATFORM_PLAYER_ID,
                    scenario_version_id=version.id,
                    creation_key=f"draft-sandbox:{uuid4()}",
                )
                goal_status: str | None = None
                if goal is not None:
                    submission = PlayOrchestrator(
                        sandbox, GameInstanceId(runtime.instance.id)
                    ).submit_goal(goal, idempotency_key=f"draft-sandbox-goal:{uuid4()}")
                    goal_status = (
                        submission.task.status.value
                        if submission.task is not None
                        else submission.resolution.status
                    )
                state = PlayerProjectionService(sandbox).game_state(
                    GameInstanceId(runtime.instance.id)
                )
                return DraftSandboxResult(
                    scenario_id=scenario_id,
                    revision=revision,
                    started=True,
                    issues=issues,
                    goal_status=goal_status,
                    player_state=state,
                )
        finally:
            engine.dispose()


__all__ = ["DraftSandboxResult", "DraftSandboxService"]
