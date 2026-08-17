import json
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService, PlanningActionCatalogBuilder
from app.agent.provider import (
    GoalSelection,
    GoalSelectionRequest,
    OpenAICompatibleGenericProvider,
    PlanningActionCandidate,
    PlanningContext,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)
from app.core.config import Settings
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import Player
from app.scenarios.builtin import STARFIRE_V2, require_builtin_v2_version
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService


class DirectBindingProvider:
    model_name = "direct-binding-test-provider"

    def __init__(self) -> None:
        self.requests: list[PlanRequest] = []

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        raise AssertionError(f"exact Goal should not call provider: {request.goal}")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        return PlanProposal(
            plan_summary="Secure the valley, restore the outpost, and reopen trade.",
            steps=(
                PlanStepProposal(
                    purpose="Make the northern valley safe",
                    action_key="clear_valley",
                    actor_key="han_lie",
                    target_key="northern_valley",
                    parameters={"troop_count": 80, "strategy": "STANDARD"},
                    short_actor_reason="frontline commander",
                ),
                PlanStepProposal(
                    purpose="Secure village support",
                    action_key="negotiate_support",
                    actor_key="lu_ning",
                    target_key="north_village",
                    parameters={"food_offer": 20, "requested_support": "GUIDE"},
                    short_actor_reason="steward handles supplies",
                ),
                PlanStepProposal(
                    purpose="Restore the outpost",
                    action_key="repair_outpost",
                    actor_key="lu_ning",
                    target_key="starfire_outpost",
                    parameters={
                        "repair_level": "FULL",
                        "food_commitment": 30,
                        "gold_commitment": 40,
                    },
                    short_actor_reason="steward manages repairs",
                ),
                PlanStepProposal(
                    purpose="Open the trade route",
                    action_key="test_trade_route",
                    actor_key="lu_ning",
                    target_key="northern_trade_route",
                    parameters={},
                    short_actor_reason="steward operates trade",
                ),
            ),
        )


def _runtime(session: Session):  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, STARFIRE_V2)
    player = Player(name=f"planning-context-{uuid4().hex[:8]}")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=str(uuid4()),
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return runtime, scope


def test_planning_context_is_entity_once_and_knowledge_safe(session: Session) -> None:
    runtime, scope = _runtime(session)
    provider = DirectBindingProvider()
    task = GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session, "open the northern trade route"
    )

    request = provider.requests[0]
    context = request.planning_context
    assert context is not None
    assert {item["action_key"] for item in context.relevant_actions} == {
        "clear_valley",
        "negotiate_support",
        "recon_valley",
        "repair_outpost",
        "test_trade_route",
    }
    assert len({item["action_key"] for item in context.relevant_actions}) == len(
        context.relevant_actions
    )
    assert {item["actor_key"] for item in context.relevant_actors} >= {
        "shen_ce",
        "han_lie",
        "lu_ning",
    }
    assert len({item["actor_key"] for item in context.relevant_actors}) == len(
        context.relevant_actors
    )
    assert "enemy_north_supply_route" not in json.dumps(
        context.model_dump(mode="json"), ensure_ascii=False
    )
    locked_target = next(
        item for item in context.relevant_targets if item["target_key"] == "starfire_outpost"
    )
    assert set(locked_target) == {"target_key"}
    known_node_keys = {item["key"] for item in context.current_knowledge["nodes"]}
    locked_node = next(
        item for item in context.current_knowledge["nodes"] if item["key"] == "starfire_outpost"
    )
    assert locked_node["type"] == "facility"
    assert set(context.current_knowledge) >= {
        "nodes",
        "facts",
        "relations",
        "resources",
        "observations",
    }
    assert context.current_knowledge["facts"]
    assert context.current_knowledge["relations"]
    assert context.current_knowledge["resources"]
    assert all(
        relation["source_node_key"] in known_node_keys
        and relation["target_node_key"] in known_node_keys
        for relation in context.current_knowledge["relations"]
    )
    assert "desired_state" not in context.goal
    assert "completion_requirements" in context.goal
    assert all(set(item) == {"target_key"} for item in context.relevant_targets)
    for action in context.relevant_actions:
        assert "public_prerequisites" not in action
        assert "cost_risk" not in action
        if "soft_signals" in action:
            assert action["soft_signals"].get("hints")
    for actor in context.relevant_actors:
        assert "cost_risk" not in actor
        assert "current_region" not in actor["current_known_state"]
    initial_payload = request.provider_payload()
    assert "replan_reason" not in initial_payload
    assert "repair_attempt" not in initial_payload
    assert "repair_diagnostics" not in initial_payload
    assert "previous_execution_context" not in initial_payload["planning_context"]
    assert task.current_plan_version == 1


def test_legacy_catalog_is_not_in_canonical_provider_payload(session: Session) -> None:
    runtime, scope = _runtime(session)
    provider = DirectBindingProvider()
    GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session, "open the northern trade route"
    )
    request = provider.requests[0]
    payload = json.dumps(request.provider_payload(), ensure_ascii=False)
    assert "planning_context" in payload
    assert "candidate_id" not in payload
    assert request.planning_action_catalog


def test_provider_payload_keeps_replan_and_repair_context_fields() -> None:
    initial_context = PlanningContext(previous_execution_context={})
    initial_payload = PlanRequest(
        call_type="INITIAL_PLAN",
        goal="goal",
        planning_context=initial_context,
    ).provider_payload()
    assert set(initial_payload) == {"call_type", "goal", "planning_context"}
    assert "previous_execution_context" not in initial_payload["planning_context"]

    replan_context = PlanningContext(
        previous_execution_context={"previous_plan_version": 1},
    )
    replan_payload = PlanRequest(
        call_type="REPLAN",
        goal="goal",
        replan_reason="TRAVEL_BLOCKED",
        planning_context=replan_context,
    ).provider_payload()
    assert replan_payload["replan_reason"] == "TRAVEL_BLOCKED"
    assert replan_payload["planning_context"]["previous_execution_context"] == {
        "previous_plan_version": 1
    }
    assert "repair_attempt" not in replan_payload
    assert "repair_diagnostics" not in replan_payload

    repair_payload = PlanRequest(
        call_type="REPAIR",
        goal="goal",
        repair_attempt=0,
        repair_diagnostics=({"code": "PLAN_REJECTED"},),
        planning_context=initial_context,
    ).provider_payload()
    assert repair_payload["repair_attempt"] == 0
    assert repair_payload["repair_diagnostics"] == [{"code": "PLAN_REJECTED"}]


def test_validator_relevance_allows_direct_and_epistemic_steps_but_rejects_unrelated(
    session: Session,
) -> None:
    runtime, scope = _runtime(session)
    provider = DirectBindingProvider()
    service = GenericAgentService(session, scope, provider=provider)
    task = service.create_task(runtime.session, "open the northern trade route")
    request = provider.requests[0]
    proposal = provider.propose_plan(request)
    context = request.planning_context
    assert context is not None
    definition = service._definition()
    objectives = service._objectives(task, definition)
    catalog = PlanningActionCatalogBuilder(session, scope).build(
        definition,
        objectives,
        task=task,
        replan_reason=None,
    )

    # The normal direct proposal contains objective-progressing Actions and is
    # accepted without requiring a backend-generated prerequisite graph.
    _direct_steps, direct_diagnostics = service._validate_provider_proposal_v1(
        task,
        definition,
        objectives,
        None,
        2,
        catalog,
        proposal.steps,
        context,
    )
    assert not direct_diagnostics

    # Reconnaissance is epistemic: it adds public Knowledge before the direct
    # objective Actions.  Its goal-directed relation is enough for acceptance.
    recon = PlanStepProposal(
        purpose="Inspect the northern valley",
        action_key="recon_valley",
        actor_key="han_lie",
        target_key="northern_valley",
        parameters={"troop_count": 20, "approach": "CAUTIOUS"},
    )
    _epistemic_steps, epistemic_diagnostics = service._validate_provider_proposal_v1(
        task,
        definition,
        objectives,
        None,
        3,
        catalog,
        (recon, *proposal.steps),
        context,
    )
    assert not epistemic_diagnostics

    # If the same Action is presented without any objective or Knowledge
    # progression, the validator rejects it as unrelated.  This is a semantic
    # relevance check, not a requirement that every step be in a prerequisite
    # or effect graph.
    unrelated_context = context.model_copy(
        update={
            "relevant_actions": tuple(
                {
                    **entry,
                    "objective_relevance": [],
                    "declared_world_effects": [],
                    "declared_knowledge_effects": [],
                }
                if entry.get("action_key") == "recon_valley"
                else entry
                for entry in context.relevant_actions
            )
        }
    )
    _unrelated_steps, unrelated_diagnostics = service._validate_provider_proposal_v1(
        task,
        definition,
        objectives,
        None,
        4,
        catalog,
        (recon, *proposal.steps),
        unrelated_context,
    )
    assert any(item.get("code") == "OBJECTIVE_IRRELEVANT" for item in unrelated_diagnostics)


def test_openai_compatible_provider_sends_context_not_candidate_catalog(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}
    response_content = json.dumps(
        {
            "plan_summary": "test",
            "steps": [
                {
                    "purpose": "inspect",
                    "action_key": "inspect",
                    "actor_key": "actor_one",
                    "target_key": "node_one",
                    "parameters": {},
                }
            ],
        }
    )

    def fake_post(*_args: object, **kwargs: object) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": response_content,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "prompt_cache_hit_tokens": 4,
                    "prompt_cache_miss_tokens": 6,
                    "completion_tokens": 8,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                    "total_tokens": 18,
                },
            },
            request=httpx.Request("POST", "https://provider.test/chat/completions"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleGenericProvider(
        Settings(
            _env_file=None,
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            model_provider="openai_compatible",
            model_name="fake-model",
            model_api_key=SecretStr("not-a-real-key"),
        )
    )
    request = PlanRequest(
        call_type="INITIAL_PLAN",
        planning_context=PlanningContext(
            goal={"exact_scenario_version": "version-1"},
            relevant_actions=({"action_key": "inspect"},),
            relevant_actors=({"actor_key": "actor_one"},),
            relevant_targets=({"target_key": "node_one"},),
        ),
        planning_action_catalog=(
            PlanningActionCandidate(
                candidate_id="candidate_secret_compat",
                action_key="inspect",
                action_name="Inspect",
                actor_key="actor_one",
                actor_name="Actor One",
                target_key="node_one",
                target_name="Node One",
                currently_executable=True,
            ),
        ),
    )
    proposal = provider.propose_plan(request)

    assert proposal.steps[0].action_key == "inspect"
    user_payload = json.loads(
        next(
            item["content"]
            for item in captured["json"]["messages"]  # type: ignore[index]
            if item["role"] == "user"
        )
    )
    assert "planning_context" in user_payload
    assert "candidate_id" not in json.dumps(user_payload)
    assert captured["json"]["thinking"] == {"type": "enabled"}  # type: ignore[index]
    assert captured["json"]["reasoning_effort"] == "low"  # type: ignore[index]
    assert provider.last_call_metadata is not None
    assert provider.last_call_metadata.model == "fake-model"
    assert provider.last_call_metadata.call_type == "INITIAL_PLAN"
    assert provider.last_call_metadata.thinking_mode == "disabled"
    assert provider.last_call_metadata.context_bytes is not None
    assert provider.last_call_metadata.request_size_bytes is not None
    assert provider.last_call_metadata.prompt_cache_hit_tokens == 4
    assert provider.last_call_metadata.prompt_cache_miss_tokens == 6
    assert provider.last_call_metadata.reasoning_tokens == 3
    assert provider.last_call_metadata.final_content_bytes == len(response_content.encode("utf-8"))
    assert provider.last_call_metadata.finish_reason == "stop"
