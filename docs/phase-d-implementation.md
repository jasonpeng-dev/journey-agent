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

## HTTP inventory

- Scenarios: list/detail/create/archive, Current Draft read/update/rename/delete/references,
  validate/publish/restore, immutable Version list/detail, maturity examples.
- Games: active/archive lists, exact-Version create/detail/history/archive, Task abandon.
- Play: player state, Goal submission, continue, approval approve/reject.
- Developer: gated snapshot and history under `/api/v1/developer/games/{id}`.

## Page inventory

- `/scenarios`, `/scenarios/new`, `/scenarios/{id}`
- `/scenarios/{id}/edit/{section}[/{objectKey}]`
- `/games`, `/games/new`, `/games/{id}`

The Game page contains formal Play and an opt-in embedded Developer View. Normal rendering never
downloads Developer data.

## Safety bounds and lifecycle

- One non-terminal Task per GameInstance is enforced in service code and a partial unique DB index.
- Formal Play has a 50-transition invocation bound and Generic planning has a five-replan bound.
- An exact rejected `(actor, action, target, canonical parameters)` signature is persisted on the
  Task and enforced for deterministic and provider-produced plans.
- Candidate exhaustion after Reject becomes `BLOCKED_BY_PLAYER_DECISION`; other planning
  exhaustion becomes `UNREACHABLE_IN_CURRENT_STATE`.
- Archived instances are read-only at application-service boundaries.

## Verification matrix

Backend integration coverage includes lifecycle/version isolation, dual-scenario formal Play,
hidden-Truth exclusion, approval approve/reject, persistence/recovery, archive guards, and migration
roundtrips. Frontend gates include ESLint, TypeScript, Vitest, production build, and a real Chromium
E2E from exact-Version New Game through Goal completion and archive. CI runs on Node 22 and Python
3.12, with PostgreSQL for backend verification.

## Known first-release limits

Phase D is single-user and uses one platform Player identity. Developer access is a shared
server-configured credential, not full authentication/RBAC. Resources are GameInstance-global;
node/actor-specific state remains Facts. Formal Play uses bounded immediate advancement without
realtime pacing, multiplayer, collaborative editing, or arbitrary scripting.
