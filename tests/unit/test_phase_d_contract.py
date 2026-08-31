from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas.phase_d import (
    DeveloperGameSnapshotResponse,
    DraftReplaceRequest,
    GameSummaryResponse,
    GoalSubmissionResponse,
    GoalSubmissionStatus,
    PlayerGameStateResponse,
    PublicGameStatus,
    ScenarioCreateMode,
    ScenarioCreateRequest,
)


def test_draft_transport_accepts_incomplete_json_without_domain_parsing() -> None:
    request = DraftReplaceRequest(
        expected_revision=3,
        definition_document={"metadata": {"name": "Work in progress"}},
    )

    assert request.definition_document == {"metadata": {"name": "Work in progress"}}


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "BLANK", "key": "new_scenario", "name": "New", "example_key": "showcase"},
        {"mode": "CLONE_VERSION", "key": "clone", "name": "Clone"},
        {
            "mode": "EXAMPLE",
            "key": "example",
            "name": "Example",
            "source_version_id": uuid4(),
        },
    ],
)
def test_scenario_create_mode_rejects_wrong_source_shape(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScenarioCreateRequest.model_validate(payload)


def test_scenario_create_modes_have_one_explicit_source() -> None:
    source_version_id = uuid4()

    blank = ScenarioCreateRequest(mode=ScenarioCreateMode.BLANK, key="blank", name="Blank")
    clone = ScenarioCreateRequest(
        mode=ScenarioCreateMode.CLONE_VERSION,
        key="clone",
        name="Clone",
        source_version_id=source_version_id,
    )
    example = ScenarioCreateRequest(
        mode=ScenarioCreateMode.EXAMPLE,
        key="example",
        name="Example",
        example_key="minimum_playable",
    )

    assert blank.source_version_id is None and blank.example_key is None
    assert clone.source_version_id == source_version_id
    assert example.example_key == "minimum_playable"


def test_unresolved_goal_does_not_expose_an_agent_task() -> None:
    unsupported = GoalSubmissionResponse(
        status=GoalSubmissionStatus.UNSUPPORTED,
        explanation="No exact-Version Objective matches the goal",
    )
    assert unsupported.task is None

    with pytest.raises(ValidationError):
        GoalSubmissionResponse(status=GoalSubmissionStatus.ACCEPTED)


def test_player_contract_rejects_hidden_truth_and_developer_contract_owns_it() -> None:
    now = datetime.now(UTC)
    game = GameSummaryResponse(
        id=uuid4(),
        scenario_id=uuid4(),
        scenario_name="测试场景系列",
        scenario_version_id=uuid4(),
        scenario_version_number=1,
        scenario_content_hash="a" * 64,
        status=PublicGameStatus.ACTIVE,
        runtime_revision=1,
        created_at=now,
        updated_at=now,
    )
    player_payload = {
        "game": game.model_dump(),
        "visible_nodes": [],
        "known_facts": [],
        "resources": [],
        "current_task": None,
    }
    assert PlayerGameStateResponse.model_validate(player_payload).known_facts == []

    with pytest.raises(ValidationError):
        PlayerGameStateResponse.model_validate({**player_payload, "truth": {"secret": True}})

    developer = DeveloperGameSnapshotResponse(
        game=game,
        truth={"secret": True},
        knowledge={},
        actors=[],
        tasks=[],
        plans=[],
        operations=[],
        rule_outcomes=[],
        decisions=[],
        memory=[],
        history=[],
    )
    assert developer.truth == {"secret": True}
