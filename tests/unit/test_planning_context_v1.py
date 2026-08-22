import json
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.agent.dependency_closure import _scope_actor_actions_to_contracts
from app.agent.generic import (
    GenericAgentService,
    PlanningActionCatalogBuilder,
    _validate_plan_segment_contract,
)
from app.agent.provider import (
    GoalSelection,
    GoalSelectionRequest,
    OpenAICompatibleGenericProvider,
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
    PlanningActionCandidate,
    PlanningContext,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
    PlanViolation,
)
from app.core.config import Settings
from app.domain.enums import AgentPlanStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import AgentPlan, Player
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


def test_planner_actor_actions_are_scoped_without_mutating_global_permissions() -> None:
    actor = PlannerActorState(
        actor_key="logistics",
        role_key="logistics_team",
        capabilities=("EXECUTE_ACTION", "LOGISTICS"),
        allowed_action_keys=(
            "inspect",
            "survey_resources",
            "transport_resource",
            "travel",
        ),
        availability="ACTIVE",
        current_region="central",
        command_reachability="ONLINE",
    )
    contracts = (
        PlannerActionContract(action_key="survey_resources"),
        PlannerActionContract(action_key="travel"),
    )

    projected = _scope_actor_actions_to_contracts((actor,), contracts)

    assert projected[0].allowed_action_keys == ("survey_resources", "travel")
    assert actor.allowed_action_keys == (
        "inspect",
        "survey_resources",
        "transport_resource",
        "travel",
    )


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
    assert all(
        set(item) == {"target_key", "requirements"}
        and all(
            set(requirement).issubset(
                {"action_key", "required_actor_role_key", "cost", "special_requirements"}
            )
            for requirement in item["requirements"]
        )
        for item in context.current_knowledge["known_action_requirements"]
    )
    for action in context.relevant_actions:
        assert "known_requirements" not in action
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
    assert initial_payload["planner_input"]["schema_version"] == 2
    assert "enemy_north_supply_route" not in json.dumps(
        initial_payload, ensure_ascii=False
    )
    assert "planning_context" not in initial_payload
    assert task.current_plan_version == 1


def test_legacy_catalog_is_not_in_canonical_provider_payload(session: Session) -> None:
    runtime, scope = _runtime(session)
    provider = DirectBindingProvider()
    GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session, "open the northern trade route"
    )
    request = provider.requests[0]
    payload = json.dumps(request.provider_payload(), ensure_ascii=False)
    assert "planner_input" in payload
    assert "planning_context" not in payload
    assert "candidate_id" not in payload
    assert request.planning_action_catalog


def test_provider_payload_keeps_replan_and_repair_context_fields() -> None:
    initial_context = PlanningContext(previous_execution_context={})
    planner_input = PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="actor-one",
                role_key="operator",
                capabilities=("FIELD_COMMAND",),
                allowed_action_keys=("inspect",),
                availability="AVAILABLE",
                current_region="region-one",
                command_reachability="REACHABLE",
            ),
        )
    )
    initial_payload = PlanRequest(
        call_type="INITIAL_PLAN",
        goal="goal",
        planning_context=initial_context,
        planner_input=planner_input,
    ).provider_payload()
    assert set(initial_payload) == {"call_type", "planner_input"}
    assert initial_payload["planner_input"]["schema_version"] == 2
    canonical_actor = initial_payload["planner_input"]["actors"][0]

    replan_context = PlanningContext(
        previous_execution_context={"previous_plan_version": 1},
    )
    replan_payload = PlanRequest(
        call_type="REPLAN",
        goal="goal",
        replan_reason="TRAVEL_BLOCKED",
        planning_context=replan_context,
        planner_input=planner_input.model_copy(
            update={"execution_context": {"previous_plan_version": 1}}
        ),
    ).provider_payload()
    assert replan_payload["replan_reason"] == "TRAVEL_BLOCKED"
    assert replan_payload["planner_input"]["execution_context"] == {
        "previous_plan_version": 1
    }
    assert "repair_attempt" not in replan_payload
    assert "repair_diagnostics" not in replan_payload
    assert replan_payload["planner_input"]["actors"][0] == canonical_actor

    repair_payload = PlanRequest(
        call_type="REPAIR",
        goal="goal",
        repair_attempt=0,
        repair_diagnostics=({"code": "PLAN_REJECTED"},),
        rejected_segment={
            "stop_reason": "OBJECTIVE_COMPLETION",
            "steps": [{"step_id": "step-1"}],
        },
        planning_context=initial_context,
        planner_input=planner_input,
    ).provider_payload()
    assert repair_payload["repair_attempt"] == 0
    assert repair_payload["validator_violations"] == [{"code": "PLAN_REJECTED"}]
    assert repair_payload["rejected_segment"]["steps"][0]["step_id"] == "step-1"
    assert repair_payload["planner_input"]["actors"][0] == canonical_actor


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
    unrelated = next(
        item for item in unrelated_diagnostics if item.get("code") == "OBJECTIVE_IRRELEVANT"
    )
    assert unrelated == {
        "code": "OBJECTIVE_IRRELEVANT",
        "failure_code": "OBJECTIVE_IRRELEVANT",
        "step_id": recon.step_id,
        "action_key": "recon_valley",
        "actor_key": "han_lie",
        "target_key": "northern_valley",
        "dimension": "OBJECTIVE_RELEVANCE",
        "required": "ADVANCES_FROZEN_OBJECTIVE_SCOPE",
        "actual": "NO_DECLARED_RELEVANT_EFFECT",
    }


def test_validator_reports_target_interaction_mismatch_to_provider(
    session: Session,
) -> None:
    runtime, scope = _runtime(session)
    provider = DirectBindingProvider()
    service = GenericAgentService(session, scope, provider=provider)
    task = service.create_task(runtime.session, "open the northern trade route")
    request = provider.requests[0]
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

    # ``clear_valley`` requires ``clear_threat`` while the outpost only
    # declares ``repair``.  The target is known, so this is a static contract
    # mismatch rather than an objective-relevance failure.
    invalid_step = PlanStepProposal(
        purpose="Clear the outpost",
        action_key="clear_valley",
        actor_key="han_lie",
        target_key="starfire_outpost",
        parameters={"troop_count": 80, "strategy": "STANDARD"},
    )
    _steps, diagnostics = service._validate_provider_proposal_v1(
        task,
        definition,
        objectives,
        None,
        2,
        catalog,
        (invalid_step,),
        context,
    )

    assert diagnostics[0] == {
        "code": "TARGET_INTERACTION_INVALID",
        "failure_code": "TARGET_INTERACTION_INVALID",
        "step_id": invalid_step.step_id,
        "action_key": "clear_valley",
        "actor_key": "han_lie",
        "target_key": "starfire_outpost",
        "dimension": "TARGET_INTERACTION",
        "required": "clear_threat",
        "actual": ["repair"],
        "required_interaction_key": "clear_threat",
        "actual_interactions": ("repair",),
    }
    assert all(item.get("code") != "OBJECTIVE_IRRELEVANT" for item in diagnostics)


def test_openai_compatible_provider_sends_context_not_candidate_catalog() -> None:
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

    def fake_transport(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
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
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(
        Settings(
            _env_file=None,
            app_env="test",
            database_url="sqlite+pysqlite:///:memory:",
            model_provider="openai_compatible",
            model_name="fake-model",
            model_api_key=SecretStr("not-a-real-key"),
        ),
        transport=httpx.MockTransport(fake_transport),
    )
    request = PlanRequest(
        call_type="INITIAL_PLAN",
        planner_input=PlannerInput(
            objective={"exact_scenario_version": "version-1"},
        ),
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
    assert user_payload["planner_input"]["schema_version"] == 2
    assert "planning_context" not in user_payload
    assert "candidate_id" not in json.dumps(user_payload)
    system_prompt = next(
        item["content"]
        for item in captured["json"]["messages"]  # type: ignore[index]
        if item["role"] == "system"
    )
    for legacy_term in (
        "allowed_action_keys",
        "planner_constraints",
        "planner_effects",
        "validator_violations",
    ):
        assert legacy_term not in system_prompt
    for v2_term in (
        "planner_input.objective",
        "planner_input.actors",
        "planner_input.action_contracts",
        "planner_input.target_bindings",
        "planner_input.known_world",
        "projected deterministic effects",
        "boundary_dependency_id",
        "attempt_policy MAY_ATTEMPT is not an information boundary",
        "validate every Step in order against the projected known state",
        "Apply all declared deterministic effects from earlier Steps",
        "known deterministic contradiction",
    ):
        assert v2_term in system_prompt
    repair_body, _repair_size = provider._build_request_body(
        "repair",
        request.model_copy(
            update={
                "call_type": "REPAIR",
                "repair_attempt": 1,
                "rejected_segment": {
                    "stop_reason": "OBJECTIVE_COMPLETION",
                    "steps": [{"step_id": "step-1"}],
                },
                "repair_diagnostics": (
                    PlanViolation(
                        code="LOCALITY_INVALID",
                        step_id="step-1",
                        dimension="LOCALITY",
                        required="SAME_REGION",
                        actual={"actor_region": "region-a"},
                    ),
                ),
            }
        ).provider_payload(),
    )
    repair_prompt = repair_body["messages"][0]["content"]  # type: ignore[index]
    for repair_term in (
        "must eliminate every supplied validator violation",
        "re-evaluate the corrected segment sequentially against projected known state",
        "same dimension / required / actual contradiction",
        "complete, corrected, revalidated PlanSegment",
        "anti_regression_memory is historical contradiction evidence only",
        "does not prescribe or preserve any previous Action, Actor, Target",
        "You may redesign the entire PlanSegment freely",
        "does not reintroduce contradictions represented by this memory",
    ):
        assert repair_term in repair_prompt
    for forbidden_term in (
        "Linjiang",
        "Task 1",
        "Travel before Relay",
        "travel_first",
        "relay_first",
        "known_recovery_effects",
        "recommended recovery action",
    ):
        assert forbidden_term not in system_prompt
        assert forbidden_term not in repair_prompt
    assert captured["json"]["thinking"] == {"type": "disabled"}  # type: ignore[index]
    assert captured["json"]["reasoning_effort"] == "low"  # type: ignore[index]
    assert captured["json"]["max_tokens"] == 8192  # type: ignore[index]
    assert provider.last_call_metadata is not None
    assert provider.last_call_metadata.model == "fake-model"
    assert provider.last_call_metadata.call_type == "INITIAL_PLAN"
    assert provider.last_call_metadata.thinking_mode == "disabled"
    assert provider.last_call_metadata.reasoning_effort == "low"
    assert provider.last_call_metadata.configured_output_token_limit == 8192
    assert provider.last_call_metadata.context_bytes is not None
    assert provider.last_call_metadata.request_size_bytes is not None
    assert provider.last_call_metadata.prompt_cache_hit_tokens == 4
    assert provider.last_call_metadata.prompt_cache_miss_tokens == 6
    assert provider.last_call_metadata.reasoning_tokens == 3
    assert provider.last_call_metadata.final_content_bytes == len(response_content.encode("utf-8"))
    assert provider.last_call_metadata.finish_reason == "stop"


def test_plan_segment_information_boundary_and_step_ids_are_strict() -> None:
    planner_input = PlannerInput(
        action_contracts=(
            PlannerActionContract(
                action_key="survey",
                deterministic_effects=(
                    {"type": "REGION_RESOURCE_KNOWLEDGE", "target": "target_region"},
                ),
            ),
        ),
        known_world=PlannerKnownWorldSlice(
            unknown_dependencies=(
                {
                    "dependency_id": "dependency-resource-source-test",
                    "dimension": "RESOURCE_SOURCE",
                    "resource_key": "repair_parts",
                    "scope_region": "region",
                    "status": "UNKNOWN",
                    "blocks": "SOURCE_SELECTION",
                    "resolvable_by_effect_types": ["REGION_RESOURCE_KNOWLEDGE"],
                },
            )
        ),
    )
    dependency_id = "dependency-resource-source-test"
    acquisition = PlanStepProposal(
        step_id="survey-1",
        action_key="survey",
        actor_key="actor",
        target_key="region",
    )
    valid = PlanProposal(
        stop_reason="INFORMATION_BOUNDARY",
        boundary_dependency_id=dependency_id,
        steps=(acquisition,),
    )
    assert _validate_plan_segment_contract(valid, planner_input) == ()
    assert _validate_plan_segment_contract(
        PlanProposal(stop_reason="OBJECTIVE_COMPLETION", steps=(acquisition,)),
        planner_input,
    ) == ()
    acquisition_not_last = _validate_plan_segment_contract(
        valid.model_copy(
            update={
                "steps": (
                    acquisition,
                    PlanStepProposal(
                        step_id="after-survey",
                        action_key="inspect",
                        actor_key="actor",
                        target_key="region",
                    ),
                )
            }
        ),
        planner_input,
    )[0]
    assert acquisition_not_last.code == "INFORMATION_BOUNDARY_ACQUISITION_NOT_LAST"
    assert acquisition_not_last.dimension == "INFORMATION_BOUNDARY_ACQUISITION"
    assert acquisition_not_last.required == "MATCHING_KNOWLEDGE_ACQUISITION_MUST_BE_LAST_STEP"
    boundary_not_allowed = _validate_plan_segment_contract(
        PlanProposal(
            stop_reason="OBJECTIVE_COMPLETION",
            boundary_dependency_id=dependency_id,
            steps=(acquisition,),
        ),
        planner_input,
    )[0]
    assert boundary_not_allowed.code == "BOUNDARY_DEPENDENCY_NOT_ALLOWED"
    assert boundary_not_allowed.dimension == "SEGMENT_TERMINATION"
    assert boundary_not_allowed.required == "NO_BOUNDARY_DEPENDENCY"
    assert boundary_not_allowed.actual == dependency_id

    missing_acquisition = PlanProposal(
        stop_reason="INFORMATION_BOUNDARY",
        boundary_dependency_id=dependency_id,
        steps=(
            PlanStepProposal(
                step_id="inspect-1",
                action_key="inspect",
                actor_key="actor",
                target_key="region",
            ),
        ),
    )
    assert _validate_plan_segment_contract(missing_acquisition, planner_input)[0].code == (
        "INFORMATION_BOUNDARY_ACQUISITION_MISSING"
    )
    wrong_scope = valid.model_copy(
        update={
            "steps": (
                acquisition.model_copy(update={"target_key": "different-region"}),
            )
        }
    )
    wrong_scope_violation = _validate_plan_segment_contract(wrong_scope, planner_input)[0]
    assert wrong_scope_violation.code == "INFORMATION_BOUNDARY_ACQUISITION_MISSING"
    assert wrong_scope_violation.dependency_id == dependency_id
    assert wrong_scope_violation.required == "MATCHING_SUBMITTED_KNOWLEDGE_ACQUISITION"
    assert wrong_scope_violation.actual == "NO_MATCHING_SUBMITTED_STEP"
    not_relevant = _validate_plan_segment_contract(
        valid,
        planner_input.model_copy(
            update={"known_world": PlannerKnownWorldSlice(unknown_dependencies=())}
        ),
    )[0]
    assert not_relevant.code == "INFORMATION_BOUNDARY_NOT_RELEVANT"
    assert not_relevant.dimension == "INFORMATION_BOUNDARY"
    assert not_relevant.required == "ACTIVE_UNKNOWN_BLOCKING_DEPENDENCY"
    assert not_relevant.actual == dependency_id

    blocked = PlanProposal(stop_reason="BLOCKED", steps=())
    assert _validate_plan_segment_contract(blocked, PlannerInput()) == ()
    assert _validate_plan_segment_contract(blocked, planner_input) == ()
    direct_progress = PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="actor",
                role_key="observer",
                capabilities=("INSPECT_STATE",),
                allowed_action_keys=("survey",),
                availability="ACTIVE",
                current_region="region",
                command_reachability="ONLINE",
            ),
        ),
        action_contracts=(
            PlannerActionContract(
                action_key="survey",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["INSPECT_STATE"],
                },
                target_contract={
                    "kind": "NODE",
                    "required_interaction_key": "survey_resources",
                },
                locality={"type": "ACTOR_SAME_REGION"},
                deterministic_effects=(
                    {"type": "RESOURCE_SURVEY_COMPLETED", "target": "target_region"},
                ),
            ),
        ),
        known_world=PlannerKnownWorldSlice(
            nodes=(
                {
                    "key": "region",
                    "type": "region",
                    "access": "AVAILABLE",
                    "interactions": ["survey_resources"],
                },
            ),
            resource_knowledge=(
                {"region_key": "region", "resource_survey_completed": False},
            ),
        ),
    )
    progress_violation = _validate_plan_segment_contract(blocked, direct_progress)[0]
    assert progress_violation.model_dump(
        mode="json", exclude_none=True, exclude_defaults=True
    ) == {
        "code": "BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS",
        "failure_code": "BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS",
        "dimension": "SEGMENT_TERMINATION",
        "required": "NO_DIRECT_KNOWN_LEGAL_PROGRESS_OPTION",
        "actual": "DIRECT_KNOWN_LEGAL_PROGRESS_OPTION_EXISTS",
    }
    progress_json = json.dumps(progress_violation.model_dump(mode="json"))
    for forbidden in (
        "recommended_action",
        "suggested_actor",
        "suggested_target",
        "suggested_route",
        "next_step",
        "recovery_plan",
    ):
        assert forbidden not in progress_json
    already_surveyed = direct_progress.model_copy(
        update={
            "known_world": direct_progress.known_world.model_copy(
                update={
                    "resource_knowledge": (
                        {"region_key": "region", "resource_survey_completed": True},
                    )
                }
            )
        }
    )
    assert _validate_plan_segment_contract(blocked, already_surveyed) == ()
    binding_cost_is_known = direct_progress.model_copy(
        update={
            "known_world": direct_progress.known_world.model_copy(
                update={
                    "resources": {
                        "survey_parts": {
                            "scopes": {"region": {"known_available": 5}}
                        }
                    }
                }
            ),
            "target_bindings": (
                PlannerTargetBinding(
                    action_key="survey",
                    target_key="region",
                    requirements=(
                        {"cost": {"survey_parts": 5}},
                    ),
                ),
            ),
        }
    )
    assert _validate_plan_segment_contract(blocked, binding_cost_is_known)[0].code == (
        "BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS"
    )
    binding_cost_is_unknown = binding_cost_is_known.model_copy(
        update={
            "known_world": binding_cost_is_known.known_world.model_copy(
                update={
                    "resources": {
                        "survey_parts": {
                            "scopes": {"region": {"known_available": 4}}
                        }
                    }
                }
            )
        }
    )
    assert _validate_plan_segment_contract(blocked, binding_cost_is_unknown) == ()
    composed_only = PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="disconnected-worker",
                role_key="worker",
                capabilities=("EXECUTE_ACTION",),
                allowed_action_keys=("repair", "travel"),
                availability="ACTIVE",
                current_region="remote-region",
                command_reachability="DISCONNECTED",
            ),
        ),
        action_contracts=(
            PlannerActionContract(
                action_key="repair",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE_ACTION"],
                },
                target_contract={"kind": "NODE", "required_interaction_key": "repair"},
                locality={"type": "FACILITY_REGION"},
            ),
            PlannerActionContract(
                action_key="travel",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE_ACTION"],
                },
                target_contract={"kind": "NODE", "required_interaction_key": "travel"},
                locality={"type": "ONE_HOP_TRANSPORT"},
            ),
        ),
    )
    assert _validate_plan_segment_contract(blocked, composed_only) == ()
    assert _validate_plan_segment_contract(
        blocked.model_copy(update={"boundary_dependency_id": dependency_id}),
        PlannerInput(),
    )[0].code == "BOUNDARY_DEPENDENCY_NOT_ALLOWED"
    blocked_steps = _validate_plan_segment_contract(
        blocked.model_copy(update={"steps": (acquisition,)}),
        PlannerInput(),
    )[0]
    assert blocked_steps.code == "BLOCKED_SEGMENT_HAS_STEPS"
    assert blocked_steps.dimension == "SEGMENT_STEPS"
    assert blocked_steps.required == "EMPTY"
    assert blocked_steps.actual == 1
    unknown_route = planner_input.model_copy(
        update={
            "known_world": PlannerKnownWorldSlice(
                unknown_dependencies=(
                    {
                        "dependency_id": "dependency-route-test",
                        "dimension": "TRANSPORT_PASSABILITY",
                        "subject_key": "connector",
                        "fact_key": "passable",
                        "status": "UNKNOWN",
                        "attempt_policy": "MAY_ATTEMPT",
                    },
                )
            )
        }
    )
    route_boundary = PlanProposal(
        stop_reason="INFORMATION_BOUNDARY",
        boundary_dependency_id="dependency-route-test",
        steps=(acquisition,),
    )
    assert _validate_plan_segment_contract(route_boundary, unknown_route)[0].code == (
        "INFORMATION_BOUNDARY_NOT_RELEVANT"
    )
    duplicate = PlanProposal(
        steps=(
            acquisition,
            acquisition.model_copy(update={"action_key": "another"}),
        )
    )
    assert _validate_plan_segment_contract(duplicate, planner_input)[0].code == (
        "STEP_ID_DUPLICATE"
    )
    duplicate_violation = _validate_plan_segment_contract(duplicate, planner_input)[0]
    assert duplicate_violation.dimension == "STEP_ID"
    assert duplicate_violation.required == "UNIQUE"
    assert duplicate_violation.actual == "DUPLICATE"
    blank = acquisition.model_copy(update={"step_id": ""})
    blank_violation = _validate_plan_segment_contract(
        PlanProposal(steps=(blank,)), planner_input
    )[0]
    assert blank_violation.code == "STEP_ID_INVALID"
    assert blank_violation.required == "NON_BLANK"
    assert blank_violation.actual == "BLANK"
    no_steps = _validate_plan_segment_contract(
        PlanProposal(stop_reason="OBJECTIVE_COMPLETION", steps=()), planner_input
    )[0]
    assert no_steps.code == "NO_STEPS"
    assert no_steps.required == "AT_LEAST_ONE_STEP"
    assert no_steps.actual == 0


def test_information_boundary_exhaustion_generates_replan_from_latest_state(
    session: Session,
) -> None:
    runtime, scope = _runtime(session)
    provider = DirectBindingProvider()
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "open the northern trade route",
        initialize_plan=False,
    )
    exhausted = AgentPlan(
        task_id=task.id,
        version=1,
        status=AgentPlanStatus.ACTIVE,
        strategy_summary="knowledge acquisition boundary",
        replan_reason=None,
        created_by_actor_key=task.owner_actor_key,
        source="PROVIDER",
        validation_status="PASSED",
        validation_errors=[],
        stop_reason="INFORMATION_BOUNDARY",
    )
    session.add(exhausted)
    task.current_plan_version = 1
    session.flush()

    executed = agent.execute_next(task, replan_on_failure=False)

    assert executed is not None
    assert len(provider.requests) == 1
    assert provider.requests[0].call_type == "REPLAN"
    assert provider.requests[0].replan_reason == "INFORMATION_BOUNDARY"
    assert provider.requests[0].planner_input is not None
    assert provider.requests[0].planner_input.schema_version == 2
