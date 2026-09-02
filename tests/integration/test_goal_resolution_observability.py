from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.provider import (
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    GenericProviderError,
    ProviderCallMetadata,
)
from app.domain.formal_goal import AdHocGoalRequirementCandidateV1
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveRequirementKind
from app.infrastructure.db.models import GoalResolutionAttempt, Player
from app.scenarios.builtin import require_builtin_v2_version
from app.services.play import PlayOrchestrator
from app.services.runtime_initialization import RuntimeInitializationService
from tests.scenario_fixtures import GENERIC_TEST


class _ResolutionProvider:
    model_name = "mock-goal-resolution"

    def __init__(self, response: DynamicGoalInterpretation) -> None:
        self.response = response
        self.requests: list[DynamicGoalInterpretationRequest] = []

    def interpret_dynamic_goal(
        self, request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation:
        self.requests.append(request)
        return self.response


class _HistoryResolutionProvider(_ResolutionProvider):
    def __init__(self, response: DynamicGoalInterpretation) -> None:
        super().__init__(response)
        self._call_metadata_history: list[ProviderCallMetadata] = []

    @property
    def call_metadata_history(self) -> tuple[ProviderCallMetadata, ...]:
        return tuple(self._call_metadata_history)

    def _record_call(self, call_type: str) -> None:
        sequence = len(self._call_metadata_history) + 1
        self._call_metadata_history.append(
            ProviderCallMetadata(
                call_type=call_type,
                call_sequence=sequence,
                latency_ms=100 + sequence,
                provider="mock",
                model=self.model_name,
                thinking_mode="disabled",
                reasoning_effort="low",
                prompt_tokens=10 + sequence,
                prompt_cache_hit_tokens=sequence,
                prompt_cache_miss_tokens=10,
                completion_tokens=20 + sequence,
                reasoning_tokens=sequence,
                total_tokens=30 + sequence,
                outcome="SUCCESS",
            )
        )

    def ground_dynamic_goal_entities(
        self,
        _request: DynamicGoalEntityGroundingRequest,
    ) -> DynamicGoalEntityGrounding:
        self._record_call("DYNAMIC_GOAL_GROUNDING")
        return DynamicGoalEntityGrounding(candidate_keys=("triage_room",))

    def interpret_dynamic_goal(
        self, request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation:
        self._record_call("DYNAMIC_GOAL")
        return super().interpret_dynamic_goal(request)


class _ErrorResolutionProvider(_ResolutionProvider):
    def interpret_dynamic_goal(
        self, _request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation:
        raise GenericProviderError(
            "MODEL_PROVIDER_RESPONSE_INVALID",
            "The model provider returned an invalid Dynamic Goal interpretation",
            validation_diagnostics=(
                {
                    "validation_error_type": "tuple_type",
                    "field_path": "requirements[0].accepted_values",
                    "expected_type": "array",
                    "actual_json_type": "string",
                },
            ),
        )


def _new_game(client: TestClient, session: Session) -> tuple[str, UUID]:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    session.commit()
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version.id), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"]), version.id


def _attempt(session: Session, game_id: str) -> GoalResolutionAttempt:
    row = session.scalar(
        select(GoalResolutionAttempt)
        .where(GoalResolutionAttempt.game_instance_id == UUID(game_id))
        .order_by(GoalResolutionAttempt.created_at.desc(), GoalResolutionAttempt.id.desc())
    )
    assert row is not None
    return row


@pytest.mark.parametrize(
    ("status", "prompt"),
    [("UNSUPPORTED", None), ("NEEDS_CLARIFICATION", "Please clarify the target")],
)
def test_unresolved_goal_attempt_survives_api_rollback(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    prompt: str | None,
) -> None:
    response = DynamicGoalInterpretation(status=status, clarification_prompt=prompt)  # type: ignore[arg-type]
    provider = _ResolutionProvider(response)
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    game_id, version_id = _new_game(client, session)

    goal = "invent warp travel" if status == "UNSUPPORTED" else "make the patient better"
    submitted = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": goal, "idempotency_key": str(uuid4())},
    )

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == status
    assert submitted.json()["task"] is None
    assert len(provider.requests) == 1

    attempt = _attempt(session, game_id)
    assert attempt.scenario_version_id == version_id
    assert attempt.original_goal_text == goal
    assert attempt.normalized_goal_text == " ".join(goal.casefold().replace("_", " ").split())
    assert attempt.resolution_status == status
    assert (
        attempt.goal_hash
        == hashlib.sha256(
            " ".join(goal.casefold().replace("_", " ").split()).encode("utf-8")
        ).hexdigest()
    )
    assert attempt.goal_hash != goal
    assert attempt.provider_purpose == "DYNAMIC_GOAL_INTERPRETATION"
    assert attempt.provider_model == provider.model_name
    assert len(attempt.public_catalog_hash or "") == 64
    assert len(attempt.focused_ontology_hash or "") == 64
    assert attempt.interpretation_status == status
    assert attempt.backend_validation_result == "ACCEPTED"
    assert goal not in str(attempt.provider_metadata)
    assert goal not in str(attempt.interpretation_attempts)

    # The API performs the request rollback after returning an unresolved
    # submission.  The resolver checkpoint must still be queryable.
    session.rollback()
    persisted = _attempt(session, game_id)
    assert persisted.id == attempt.id
    developer = client.get(
        f"/api/v1/developer/games/{game_id}/snapshot",
        headers={"x-developer-token": "test-developer"},
    )
    assert developer.status_code == 200, developer.text
    assert developer.json()["goal_resolution_attempts"][-1]["id"] == str(attempt.id)


def test_resolved_dynamic_attempt_records_safe_provider_diagnostics(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=(True,),
    )
    provider = _ResolutionProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    game_id, version_id = _new_game(client, session)

    submitted = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "make the patient better", "idempotency_key": str(uuid4())},
    )

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "ACCEPTED"
    attempt = _attempt(session, game_id)
    assert attempt.scenario_version_id == version_id
    assert attempt.original_goal_text == "make the patient better"
    assert attempt.normalized_goal_text == "make the patient better"
    assert attempt.resolution_status == "RESOLVED"
    assert attempt.resolver_source == "AD_HOC_DYNAMIC"
    assert attempt.grounded_public_entity_keys == []
    assert attempt.public_catalog_hash and len(attempt.public_catalog_hash) == 64
    assert attempt.focused_ontology_hash and len(attempt.focused_ontology_hash) == 64
    assert attempt.interpretation_status == "RESOLVED"
    assert attempt.attempt_count == 1
    assert attempt.recovery_used is False
    assert attempt.backend_validation_result == "ACCEPTED"
    assert attempt.rejection_code is None
    assert attempt.provider_purpose == "DYNAMIC_GOAL_INTERPRETATION"
    assert attempt.provider_model == provider.model_name
    assert attempt.interpretation_attempts == [
        {
            "stage": "DYNAMIC_GOAL_INTERPRETATION",
            "attempt": 1,
            "status": "RESOLVED",
            "validation": "ACCEPTED",
            "result": "MODEL_ACCEPTED",
        }
    ]


def test_resolution_attempt_persists_each_provider_call_in_order(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _HistoryResolutionProvider(
        DynamicGoalInterpretation(
            status="NEEDS_CLARIFICATION",
            clarification_prompt="Please clarify the desired state",
        )
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    game_id, _version_id = _new_game(client, session)

    submitted = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "repair the room", "idempotency_key": str(uuid4())},
    )

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "NEEDS_CLARIFICATION"
    attempt = _attempt(session, game_id)
    calls = attempt.provider_metadata["provider_calls"]
    assert isinstance(calls, list)
    assert [call["call_order"] for call in calls] == [1, 2]
    assert [call["purpose"] for call in calls] == [
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL_INTERPRETATION",
    ]
    assert [call["call_sequence"] for call in calls] == [1, 2]
    assert [call["latency_ms"] for call in calls] == [101, 102]
    assert [call["prompt_tokens"] for call in calls] == [11, 12]
    assert [call["prompt_cache_hit_tokens"] for call in calls] == [1, 2]
    assert [call["prompt_cache_miss_tokens"] for call in calls] == [10, 10]
    assert [call["completion_tokens"] for call in calls] == [21, 22]
    assert [call["reasoning_tokens"] for call in calls] == [1, 2]
    assert [call["model"] for call in calls] == [provider.model_name] * 2


def test_rejected_dynamic_value_type_persists_safe_type_diagnostics(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=("yes",),
    )
    provider = _ResolutionProvider(DynamicGoalInterpretation(requirements=(candidate,)))
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    game_id, _version_id = _new_game(client, session)

    submitted = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "make the patient stable", "idempotency_key": str(uuid4())},
    )

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "UNSUPPORTED"
    attempt = _attempt(session, game_id)
    assert attempt.rejection_code == "FORMAL_GOAL_VALUE_TYPE_INVALID"
    assert attempt.value_type_diagnostics == [
        {
            "expected_value_type": "BOOLEAN",
            "actual_candidate_json_type": "string",
        }
    ]


def test_provider_error_attempt_persists_goal_text(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ErrorResolutionProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    game_id, _version_id = _new_game(client, session)
    goal = "make the patient recover"

    submitted = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": goal, "idempotency_key": str(uuid4())},
    )

    assert submitted.status_code == 502, submitted.text
    attempt = _attempt(session, game_id)
    assert attempt.resolution_status == "ERROR"
    assert attempt.rejection_code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert attempt.original_goal_text == goal
    assert attempt.normalized_goal_text == "make the patient recover"
    assert attempt.provider_metadata["validation_diagnostics"] == [
        {
            "validation_error_type": "tuple_type",
            "field_path": "requirements[0].accepted_values",
            "expected_type": "array",
            "actual_json_type": "string",
        }
    ]


def test_authored_resolution_attempt_is_recorded_without_provider_call(
    session: Session,
) -> None:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    player = Player(name=f"resolution-{uuid4().hex[:8]}")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=str(uuid4()),
    )
    orchestrator = PlayOrchestrator(
        session,
        GameInstanceId(runtime.instance.id),
        provider=None,
    )
    submitted = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submitted.task is not None
    attempt = _attempt(session, str(runtime.instance.id))
    assert attempt.resolution_status == "RESOLVED"
    assert attempt.resolver_source == "DETERMINISTIC"
    assert attempt.grounding_source == "DETERMINISTIC"
    assert attempt.provider_purpose is None
    assert attempt.provider_model is None


def test_goal_resolution_attempt_history_is_not_copied_to_fork(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ResolutionProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    game_id, _version_id = _new_game(client, session)
    submitted = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "invent warp travel", "idempotency_key": str(uuid4())},
    )
    assert submitted.status_code == 200
    assert _attempt(session, game_id).resolution_status == "UNSUPPORTED"

    game = client.get(f"/api/v1/games/{game_id}").json()
    archived = client.post(
        f"/api/v1/games/{game_id}/archive",
        json={"expected_runtime_revision": game["runtime_revision"]},
    )
    assert archived.status_code == 200, archived.text
    forked = client.post(
        f"/api/v1/games/{game_id}/fork",
        json={"creation_key": str(uuid4())},
    )
    assert forked.status_code == 201, forked.text

    target_attempts = tuple(
        session.scalars(
            select(GoalResolutionAttempt).where(
                GoalResolutionAttempt.game_instance_id == UUID(str(forked.json()["id"]))
            )
        )
    )
    assert target_attempts == ()


def test_resolution_checkpoint_does_not_commit_play_session(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.runtime_initialization import RuntimeInitializationService

    version = require_builtin_v2_version(session, GENERIC_TEST)
    player = Player(name=f"resolution-no-main-commit-{uuid4().hex[:8]}")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=str(uuid4()),
    )
    provider = _ResolutionProvider(DynamicGoalInterpretation(status="UNSUPPORTED"))
    orchestrator = PlayOrchestrator(
        session,
        GameInstanceId(runtime.instance.id),
        provider=provider,
    )

    def fail_main_commit() -> None:
        raise AssertionError("Goal resolution audit must not commit the PLAY session")

    monkeypatch.setattr(session, "commit", fail_main_commit)
    submitted = orchestrator.submit_goal("invent warp travel", idempotency_key=str(uuid4()))

    assert submitted.task is None
    assert submitted.resolution.status == "UNSUPPORTED"
    session.rollback()
    assert _attempt(session, str(runtime.instance.id)).resolution_status == "UNSUPPORTED"
