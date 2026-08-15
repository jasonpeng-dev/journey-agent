# Phase D implementation

## Architecture

The browser and HTTP modules are adapters over existing domain/application contracts. Scenario
authoring delegates to `ScenarioService`; runtime creation delegates to
`RuntimeInitializationService`; formal Play is coordinated by the thin `PlayOrchestrator`, which
calls `GenericAgentService` and `GenericActionService`. Declarative Rule evaluation and world
mutation remain in the Phase R Generic Engine.

Player and Developer projections are physically separate server routes. `PlayerProjectionService`
queries only `Visibility.KNOWN` facts. `/api/v1/developer/...` has a server-side credential gate and
may return Truth, ObjectiveScope, plans/steps, operations, decisions, rule outcomes, and memory.

Formal Play keeps runtime contracts and player presentation distinct:

- `ObjectiveScope` remains the immutable Task contract created by Phase R.
- `MissionRoadmapProjector` remains available as a generic internal projection, but Formal Player
  Play no longer presents it as the Agent's execution plan.
- Every persisted `AgentPlan` and its TOOL steps is projected into player-safe Plan History. The
  latest Plan is expanded by default. A superseded Plan is never rewritten: its completed and
  failed meaningful Actions remain visible, while non-terminal old Actions are shown cancelled.
  WAIT/settle steps remain internal.
- Historical Actions, approvals, safe results, and persisted knowledge deltas are projected into a
  semantic Mission Log without exposing Plan version numbers, WAIT/settle Steps, Rule internals,
  raw error codes, or operation payloads. The current briefing/debrief is a separate sibling panel,
  not an entry inside the historical log's scroll region.

Formal Play presentation pacing is persisted separately in `PlayerExecutionCheckpoint`; it never
changes `AgentTaskStatus` or authority. Goal submission stops at `AWAITING_ACTION_ACK`. “知悉，执行”
runs exactly one meaningful Action cycle—Action, internal async settlement, Rules, world mutation,
Knowledge, objective evaluation, and any required replan—then stops at `AWAITING_DEBRIEF_ACK` unless
the Task completed, blocked, or reached a real approval. “收到，继续任务” only advances the product
presentation to the next briefing. A real authority decision remains `APPROVAL_REQUIRED` and uses
the separate persisted approve/reject API.

## HTTP inventory

- Scenarios: list/detail/create/archive, Current Draft read/update/rename/delete/references,
  validate/publish/restore, immutable Version list/detail, maturity examples.
- Games: active/archive lists, exact-Version create/detail/history/archive, Task abandon, and
  authoritative permanent delete for ACTIVE or archived instances.
- Play: player state, Goal submission, action acknowledgement, debrief acknowledgement, approval
  approve/reject.
- Developer: gated snapshot and history under `/api/v1/developer/games/{id}`.

## Page inventory

- `/scenarios`, `/scenarios/new`, `/scenarios/{id}`
- `/scenarios/{id}/edit/{section}[/{objectKey}]`
- `/games`, `/games/new`, `/games/{id}`

The Game page contains formal Play and an opt-in embedded Developer View. Normal rendering never
downloads Developer data. It presents Known World, historical Mission Log plus current Agent
report, and persisted Plan History as the three product columns while retaining the Phase R Debug
console's visual language. The Editor Validation section also provides Draft Preview/Test. The main database
is read-only for
this operation: a Draft is strictly parsed as `ScenarioDefinitionV2`, then published and executed
only inside a request-scoped in-memory database using the existing Generic Runtime services. Invalid
Drafts return diagnostics without starting a sandbox. The disposable Runtime is destroyed after the
response and cannot create or mutate a formal GameInstance, Draft, or published Version.

## Safety bounds and lifecycle

- One non-terminal Task per GameInstance is enforced in service code and a partial unique DB index.
- Each player acknowledgement advances at most one meaningful Action cycle. Isolated Draft sandbox
  auto-driving has a 50-checkpoint bound, and Generic planning retains its five-replan bound.
- An exact rejected `(actor, action, target, canonical parameters)` signature is persisted on the
  Task and enforced for deterministic and provider-produced plans.
- Provider Plans use the generic Knowledge-safe `PlanningContext V1`. It defines Goal, Current
  Knowledge, Relevant Actions, Relevant Actors, Relevant Targets, Previous Execution Context, and
  Scenario Planning Hints. Each Action, Actor, and Target is serialized once and connected by keys;
  the old Actor x Action x Target Candidate Catalog is retained only as an in-process migration
  projection and is omitted from the OpenAI-compatible payload.
- The provider itself chooses Goal decomposition, Action, Actor, Target, parameters, and order. The
  context builder performs only knowledge filtering, bounded high-recall retrieval (three public
  dependency hops), semantic normalization, and compression. Known future locked targets remain in
  the context; hidden nodes/facts/effects do not.
- Provider proposals use direct `{action_key, actor_key, target_key, parameters}` steps with a short
  purpose/reason. The authoritative validator checks only hard static constraints, rejected proposal
  signatures, public ObjectiveScope coverage/order, and structural relevance; current access,
  resources, Rule preflight, and dynamic approval remain execution-time concerns. Invalid proposals
  receive safe diagnostics for at most two repair attempts before `MODEL_PLAN_REJECTED`.
- Each selected step is revalidated through the existing authoritative Generic Action preflight at
  execution time. A future step that is still unavailable cannot mutate the world and triggers a
  complete replan rather than being treated as immediately unreachable.
- A successfully resolved exact supporting proposal is not selected repeatedly by recovery replans;
  it remains eligible when its declared effects are genuinely required by an unsatisfied Objective
  requirement. This prevents bounded replans from being consumed by already-completed recovery work.
- Candidate exhaustion after Reject becomes `BLOCKED_BY_PLAYER_DECISION`; other planning
  exhaustion becomes `UNREACHABLE_IN_CURRENT_STATE`.
- Archived instances are read-only at application-service boundaries.
- Permanent delete takes an ownership lock and deletes the full instance-scoped FK closure in one
  transaction. Explicit ordered deletes make the behavior reliable even for SQLite connections
  without FK pragmas; the exact ScenarioVersion and other instances are never touched.

## Verification matrix

Backend integration coverage includes lifecycle/version isolation, dual-scenario formal Play,
hidden-Truth exclusion, approval approve/reject, persistence/recovery, archive guards, and migration
roundtrips. Frontend gates include ESLint, TypeScript, Vitest, production build, and a real Chromium
E2E from exact-Version New Game through Goal completion and archive. CI runs on Node 22 and Python
3.12, with PostgreSQL for backend verification.

## Known first-release limits

Phase D is single-user and uses one platform Player identity. Developer access is a shared
server-configured credential, not full authentication/RBAC. Resources are GameInstance-global;
node/actor-specific state remains Facts. Formal Play uses synchronous request/response checkpoints
without SSE, WebSockets, artificial delays, multiplayer, collaborative editing, or arbitrary
scripting.
