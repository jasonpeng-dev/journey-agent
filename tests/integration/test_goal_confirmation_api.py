# ruff: noqa: RUF001
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.agent.generic import GenericGoalResolution, GenericGoalResolver
from app.agent.provider import GenericProviderError
from app.domain.enums import ResolvedGoalDraftStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    AgentPlan,
    AgentTask,
    GoalResolutionAttempt,
    Player,
    ResolvedGoalDraft,
)
from app.infrastructure.db.session import configure_sqlite_foreign_keys
from app.scenarios.builtin import require_builtin_v2_version
from app.services.play import PlayOrchestrator
from app.services.runtime_initialization import RuntimeInitializationService
from tests.scenario_fixtures import GENERIC_TEST


def _new_game(client: TestClient, session: Session) -> str:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    session.commit()
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version.id), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _parse(client: TestClient, game_id: str, goal: str, key: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": goal, "idempotency_key": key or str(uuid4())},
    )


def test_parse_success_persists_attempt_and_ready_draft_without_task_or_plan(
    client: TestClient,
    session: Session,
) -> None:
    game_id = _new_game(client, session)

    response = _parse(client, game_id, "stabilize the patient")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "READY_FOR_CONFIRMATION"
    assert payload["draft_id"] is not None
    assert payload["presentation_text"].startswith("已解析目标：")
    assert "task" not in payload
    assert session.scalar(select(func.count()).select_from(AgentTask)) == 0
    assert session.scalar(select(func.count()).select_from(AgentPlan)) == 0
    draft = session.get(ResolvedGoalDraft, UUID(payload["draft_id"]))
    assert draft is not None
    assert draft.status == ResolvedGoalDraftStatus.READY
    assert draft.formal_goal_contract_hash
    attempt = session.get(GoalResolutionAttempt, UUID(payload["resolution_id"]))
    assert attempt is not None and attempt.resolution_status == "RESOLVED"
    assert draft.resolution_attempt_id == attempt.id

    refreshed = client.get(f"/api/v1/games/{game_id}/play")
    assert refreshed.status_code == 200
    assert refreshed.json()["current_goal_draft"] == {
        "draft_id": payload["draft_id"],
        "submitted_goal": "stabilize the patient",
        "presentation_text": payload["presentation_text"],
        "status": "READY",
        "created_at": draft.created_at.isoformat().replace("+00:00", "Z"),
    }


@pytest.mark.parametrize("status", ["NEEDS_CLARIFICATION", "UNSUPPORTED"])
def test_failed_parse_has_attempt_but_no_draft_or_task(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    game_id = _new_game(client, session)
    monkeypatch.setattr(
        GenericGoalResolver,
        "resolve",
        lambda *_args: GenericGoalResolution(status, source="TEST_TERMINAL"),
    )

    response = _parse(client, game_id, "a terminal test goal")

    assert response.status_code == 200
    assert response.json()["status"] == status
    assert response.json()["draft_id"] is None
    assert response.json()["presentation_text"]
    assert session.scalar(select(func.count()).select_from(GoalResolutionAttempt)) == 1
    assert session.scalar(select(func.count()).select_from(ResolvedGoalDraft)) == 0
    assert session.scalar(select(func.count()).select_from(AgentTask)) == 0


def test_new_parse_supersedes_ready_even_when_provider_fails(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = _new_game(client, session)
    first = _parse(client, game_id, "stabilize the patient").json()
    first_id = UUID(first["draft_id"])

    def fail(*_args):  # type: ignore[no-untyped-def]
        raise GenericProviderError("MODEL_PROVIDER_TIMEOUT", "private provider detail")

    monkeypatch.setattr(GenericGoalResolver, "resolve", fail)
    failed = _parse(client, game_id, "another goal")

    assert failed.status_code == 504
    session.expire_all()
    first_draft = session.get(ResolvedGoalDraft, first_id)
    assert first_draft is not None
    assert first_draft.status == ResolvedGoalDraftStatus.SUPERSEDED
    assert (
        session.scalar(
            select(func.count())
            .select_from(ResolvedGoalDraft)
            .where(ResolvedGoalDraft.status == ResolvedGoalDraftStatus.READY)
        )
        == 0
    )
    attempts = tuple(session.scalars(select(GoalResolutionAttempt)))
    assert [item.resolution_status for item in attempts] == ["RESOLVED", "ERROR"]


def test_parse_idempotency_replays_without_resolver_and_conflicts_on_new_goal(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = _new_game(client, session)
    key = str(uuid4())
    original = GenericGoalResolver.resolve
    calls = 0

    def counted(self, goal, definition):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(self, goal, definition)

    monkeypatch.setattr(GenericGoalResolver, "resolve", counted)
    first = _parse(client, game_id, "stabilize the patient", key)
    replay = _parse(client, game_id, "stabilize the patient", key)
    conflict = _parse(client, game_id, "diagnose the patient", key)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert calls == 1
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "GOAL_IDEMPOTENCY_CONFLICT"
    assert session.scalar(select(func.count()).select_from(GoalResolutionAttempt)) == 1
    assert session.scalar(select(func.count()).select_from(ResolvedGoalDraft)) == 1


def test_confirm_creates_exact_task_checkpoint_without_resolver_or_planner(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = _new_game(client, session)
    parsed = _parse(client, game_id, "stabilize the patient").json()
    draft = session.get(ResolvedGoalDraft, UUID(parsed["draft_id"]))
    assert draft is not None
    expected_hash = draft.formal_goal_contract_hash

    def forbidden(*_args):  # type: ignore[no-untyped-def]
        raise AssertionError("Confirm must not call the Goal Resolver")

    monkeypatch.setattr(GenericGoalResolver, "resolve", forbidden)
    confirmed = client.post(f"/api/v1/games/{game_id}/goal-drafts/{draft.id}/confirm")

    assert confirmed.status_code == 200, confirmed.text
    task = confirmed.json()
    assert task["goal"] == "stabilize the patient"
    assert task["execution_phase"] == "AWAITING_PLAN_START"
    assert task["plan"] is None
    persisted = session.get(AgentTask, UUID(task["id"]))
    assert persisted is not None
    assert persisted.formal_goal_contract_hash == expected_hash
    assert session.scalar(select(func.count()).select_from(AgentTask)) == 1
    assert session.scalar(select(func.count()).select_from(AgentPlan)) == 0
    session.expire_all()
    draft = session.get(ResolvedGoalDraft, draft.id)
    assert draft is not None
    assert draft.status == ResolvedGoalDraftStatus.CONFIRMED
    assert draft.confirmed_task_id == persisted.id
    assert draft.confirmed_at is not None

    started = client.post(
        f"/api/v1/games/{game_id}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert started.status_code == 200, started.text
    assert started.json()["current_task"]["execution_phase"] != "AWAITING_PLAN_START"


def test_repeated_confirm_returns_same_task(client: TestClient, session: Session) -> None:
    game_id = _new_game(client, session)
    parsed = _parse(client, game_id, "stabilize the patient").json()
    url = f"/api/v1/games/{game_id}/goal-drafts/{parsed['draft_id']}/confirm"

    first = client.post(url)
    second = client.post(url)

    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert session.scalar(select(func.count()).select_from(AgentTask)) == 1


def test_concurrent_confirm_returns_one_bound_task(tmp_path: Path) -> None:
    database = tmp_path / "goal-confirm-concurrency.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    configure_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        version = require_builtin_v2_version(setup, GENERIC_TEST)
        player = Player(name=f"concurrent-confirm-{uuid4().hex[:8]}")
        setup.add(player)
        setup.flush()
        runtime = RuntimeInitializationService(setup).create(
            player_id=player.id,
            scenario_version_id=version.id,
            creation_key=str(uuid4()),
        )
        game_id = runtime.instance.id
        setup.commit()
    with factory() as parsing:
        submitted = PlayOrchestrator(parsing, GameInstanceId(game_id)).submit_goal(
            "stabilize the patient",
            idempotency_key=str(uuid4()),
        )
        assert submitted.draft is not None
        draft_id = submitted.draft.id
        parsing.commit()

    barrier = Barrier(2)

    def confirm() -> UUID:
        with factory() as db:
            barrier.wait()
            task = PlayOrchestrator(db, GameInstanceId(game_id)).confirm_goal_draft(draft_id)
            db.commit()
            return task.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        task_ids = tuple(executor.map(lambda _index: confirm(), range(2)))

    assert task_ids == (draft_id, draft_id)
    with factory() as verification:
        assert verification.scalar(select(func.count()).select_from(AgentTask)) == 1
        draft = verification.get(ResolvedGoalDraft, draft_id)
        assert draft is not None
        assert draft.status == ResolvedGoalDraftStatus.CONFIRMED
        assert draft.confirmed_task_id == draft_id


def test_superseded_and_wrong_game_drafts_cannot_be_confirmed(
    client: TestClient,
    session: Session,
) -> None:
    first_game = _new_game(client, session)
    second_game = _new_game(client, session)
    first = _parse(client, first_game, "stabilize the patient").json()
    _parse(client, first_game, "diagnose the patient")

    superseded = client.post(f"/api/v1/games/{first_game}/goal-drafts/{first['draft_id']}/confirm")
    wrong_game = client.post(f"/api/v1/games/{second_game}/goal-drafts/{first['draft_id']}/confirm")

    assert superseded.status_code == 409
    assert superseded.json()["error"]["code"] == "GOAL_DRAFT_SUPERSEDED"
    assert wrong_game.status_code == 404
    assert wrong_game.json()["error"]["code"] == "GOAL_DRAFT_NOT_FOUND"
    assert session.scalar(select(func.count()).select_from(AgentTask)) == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("formal_goal_contract_hash", "0" * 64, "FORMAL_GOAL_CONTRACT_HASH_MISMATCH"),
        ("formal_goal_contract_schema_version", 99, "FORMAL_GOAL_CONTRACT_VERSION_MISMATCH"),
        ("scenario_content_hash", "0" * 64, "FORMAL_GOAL_SCENARIO_HASH_MISMATCH"),
        ("formal_goal_compiler_version", "invalid@0", "FORMAL_GOAL_COMPILER_VERSION_MISMATCH"),
    ],
)
def test_confirm_rejects_tampered_draft_integrity(
    client: TestClient,
    session: Session,
    field: str,
    value: object,
    code: str,
) -> None:
    game_id = _new_game(client, session)
    parsed = _parse(client, game_id, "stabilize the patient").json()
    draft_id = UUID(parsed["draft_id"])
    session.execute(
        update(ResolvedGoalDraft).where(ResolvedGoalDraft.id == draft_id).values({field: value})
    )
    session.commit()

    response = client.post(f"/api/v1/games/{game_id}/goal-drafts/{draft_id}/confirm")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"] == "目标确认暂时失败，请重新尝试。"
    assert session.scalar(select(func.count()).select_from(AgentTask)) == 0
