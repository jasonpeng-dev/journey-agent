# Current architecture

This document is the source of truth for the running Journey Agent architecture. It describes
the generic runtime used by the browser product; it is not a historical phase plan.

## Boundary and identity

Journey Agent is a data-driven, goal-directed agent runtime for interactive scenarios. Source
code supplies the generic interpreter and persistence services. An immutable `ScenarioVersion`
supplies the world, actors, actions, rules, objectives, and planning metadata that make one
scenario different from another.

The runtime identity chain is:

```text
Player
  -> GameInstance
       -> exact published ScenarioVersion
            -> RuntimeScope
                 -> generic Agent / Action / Rule services
```

There is one mutable Current Draft per Scenario. Editing a Draft never changes a published
snapshot. Publishing validates the Draft and creates a new immutable Version. A formal
`GameInstance` can only be initialized from a published Version and keeps that exact Version
for its entire lifetime; it never follows a latest pointer.

Scenario definitions use the closed `ScenarioDefinitionV2` contract and the
`declarative-rule-engine` version 1 contract. Authors provide data and supported structured
primitives, not Python/JavaScript gameplay code, a custom interpreter, or a model-specific
runtime.

## Agent request path

The application layer composes a provider once and injects it into the generic goal resolver and
agent service. Routers and React components only adapt requests and projections; they do not
evaluate rules or implement gameplay.

```text
natural-language Goal
  -> exact-match Goal Resolver
       -> optional configured provider fallback
  -> AgentTask with frozen ObjectiveScope
  -> PlanningContext V1 from the exact Version and current Knowledge
  -> deterministic planner or structured model PlanProposal
  -> backend Plan Validator
       -> accepted Plan
       -> bounded Repair (provider mode), or a safe model-plan error
  -> generic Action execution
  -> declarative Rule evaluation and world operation settlement
  -> Truth / Knowledge update
  -> objective verification
  -> completion, bounded Replan, approval, or a reliable blocked state
```

### Goal resolution and ObjectiveScope

The resolver first normalizes and matches the submitted text against the exact Version's
Objective keys, names, aliases, and examples. If the Scenario permits LLM fallback and an
OpenAI-compatible provider is configured, the provider may choose only from the supplied
Objective candidates. Unknown keys are rejected; the model cannot invent an Objective.

An accepted Goal creates one persistent `AgentTask`. Its non-empty, canonical
`ObjectiveScope`, exact ScenarioVersion reference, scope hash, and resolution metadata are
frozen at task creation. Objective completion is evaluated against the scoped Objectives and
their `ObjectiveRequirementV2` values; a Goal completing does not archive the GameInstance.

### Planning, Repair, and Replan

`PlanningContext V1` is the canonical model input. It contains a goal projection, current
player-safe Knowledge, relevant Actions, Actors, Targets, previous execution context, and
Scenario planning hints. It is entity-oriented: the provider chooses Action, Actor, Target,
parameters, and ordering from the supplied context. The compatibility Candidate Catalog remains
an internal migration view and is not the canonical OpenAI-compatible payload.

The deterministic path is used in mock/offline operation. In provider mode, an initial plan or
replan is a proposal only. The backend validates keys, parameter shapes, exact-Version
bindings, public relevance/coverage/order, authority constraints, and rejected-proposal
signatures before persistence. A rejected proposal receives structured diagnostics and may be
repaired a bounded number of times. Provider timeouts, HTTP failures, malformed responses, and
plans that remain invalid surface as explicit application errors or blocked states; they do not
silently turn into a different planner.

Replan keeps the same frozen ObjectiveScope. It is requested after an execution failure,
proposal rejection/approval decision, or an exhausted plan when the objective is not complete.
The generic agent has safety bounds for repairs and replans. A player rejection records the
proposal signature as a runtime constraint, so the same proposal cannot be emitted again for
that Task.

## Generic world execution

`GenericActionService` checks the exact Version Action, Actor capabilities and authority, target
visibility/access, parameters, resources, and approval policy. It creates an instance-scoped
`WorldOperation`; operation settlement and Rule evaluation remain deterministic backend work.

Rules are declarative `ConditionV2` trees (`ALL`, `ANY`, `NOT`, fact/resource/parameter
comparisons, visibility/access, and relation existence) with supported `EffectV2` primitives
such as fact/node visibility changes, node access, resource adjustments/reservations, outcomes,
failures, and memory events. The Rule Engine does not contain Starfire or Medical branches.

Truth is the authoritative instance state used for Rule evaluation and objective verification.
Knowledge is the visibility-filtered projection used for planning and normal player responses.
The player projection reads only known Nodes/Facts and public Resources/Actors/Task history.
Developer access is a separate server route and may expose Truth and internal runtime data.

## Formal Play and persistence

Formal Play is a thin orchestration layer over the generic services. It persists player pacing in
`PlayerExecutionCheckpoint` and exposes explicit phases such as plan start, action
acknowledgement, debrief, replan, approval-required, completed, blocked, and aborted. The UI
shows the selected Task's Mission Log and Plan History while the GameInstance can continue after
one Goal completes. One GameInstance permits one active Task at a time; historical Tasks remain
read-only.

Abandoning a Task or archiving a Game cancels pending approvals and unsettled operations that
have not produced a world mutation. Existing mutations are not rolled back. Archived Games are
read-only, while their published ScenarioVersion remains available for new instances.

Runtime rows are scoped by `game_instance_id` and persisted with SQLAlchemy/Alembic. Idempotent
creation keys, checkpoints, task/plan/operation rows, and instance projections allow recovery
after a request or backend restart without rebinding to a different Version.

## Player and Developer API boundary

The FastAPI application exposes two intentionally different response surfaces:

| Surface | Prefix | Purpose |
| --- | --- | --- |
| Health | `/health`, `/ready` | Liveness/readiness checks |
| Scenario authoring | `/api/v1/scenarios` | Scenario/Draft/Version lifecycle, validation, references, and sandbox |
| Player Games | `/api/v1/games` | Published-Version GameInstances, Knowledge-safe Play, history, approvals, and archive |
| Developer Games | `/api/v1/developer/games` | Credential-gated Truth and internal runtime snapshots/history |

Player DTOs never include hidden Truth, Rule ASTs, provider payloads, or raw model internals.
The Developer route uses the configured server-side developer credential gate; this is a
single-user platform boundary, not a full multi-user RBAC system.

## Provider boundary

`MODEL_PROVIDER=mock` builds no HTTP provider and exercises deterministic resolution/planning.
`MODEL_PROVIDER=openai_compatible` constructs the generic OpenAI-compatible adapter with
`MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`, and `MODEL_TIMEOUT_SECONDS`. DeepSeek is used by
pointing that adapter at its compatible endpoint; the ScenarioVersion never names a provider or
model. Provider audit metadata records call type, latency, context bytes, and token usage when
the provider returns it, without logging the API key.

## Where to look in the repository

| Path | Responsibility |
| --- | --- |
| `app/domain/` | Frozen ScenarioDefinitionV2, ObjectiveScope, world and runtime value objects |
| `app/agent/` | Generic resolver, PlanningContext, provider boundary, plan validation, and agent loop |
| `app/services/` | Scenario lifecycle, Game lifecycle, Formal Play, projections, actions, and sandbox |
| `app/scenarios/` | V2 parsing/validation, persistence, built-in Starfire and Medical definitions |
| `app/api/` | FastAPI adapters and Player/Developer DTOs |
| `frontend/src/` | React/Vite Scenario Library, Editor, Games, Formal Play, and Developer toggle |
| `tests/` | Unit, contract, integration, lifecycle, provider, and dual-scenario coverage |
| `migrations/` | Alembic schema history; run migrations through `alembic upgrade head` |

For the detailed provider payload contract see [`planning-context-v1.md`](planning-context-v1.md).
For authoring and publishing rules see [`scenario-authoring.md`](scenario-authoring.md).
