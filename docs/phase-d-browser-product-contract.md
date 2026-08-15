# Phase D — Browser Product Contract

Status: frozen at D0. This document defines the adapter and product contract for D1–D7.
It does not replace the Phase R domain or application contracts.

Implementation status: D0-D7 implemented on `feature/scenario-editor`. See
`docs/phase-d-implementation.md` for the adapter map, safety bounds, verification matrix, and
first-release limits.

## 1. Architectural boundary

The governing rule remains:

> Source code defines the generic engine; ScenarioVersion defines the game.

The browser product is an adapter over the existing application services. HTTP routers and UI
components may coordinate services and present DTOs, but may not implement Rule evaluation,
Action effects, Objective completion, authority, exact-Version lookup, or persistence semantics.

The only Phase D application-layer additions are generic product lifecycle behavior that Phase R
intentionally deferred: authoring commands, readiness classification, Game archive/abandon guards,
bounded Play orchestration, and rejected-proposal runtime constraints.

Forbidden additions include v1 document/runtime support, scenario-key-to-latest Runtime lookup,
Starfire gameplay branches, scenario-authored Tools or scripts, provider configuration inside a
ScenarioVersion, and browser-side execution of Rules.

## 2. Platform and trust boundary

Phase D is a single-user product. The server resolves one platform Player identity; request bodies
do not select arbitrary Players. Full authentication, multi-user tenancy, and RBAC are out of scope.

Player and developer reads use separate routers and serializers:

```text
/api/v1/games/...                 player-facing Knowledge projection
/api/v1/developer/games/...       internal Truth/debug projection
```

The developer router is enabled by server configuration and requires a configured credential.
Production defaults to disabled. A Player endpoint never queries or constructs hidden Truth merely
to remove it later. Frontend hiding is not a security boundary.

Scenario authoring endpoints necessarily expose definition content, including initial hidden Facts,
and are part of the author/developer surface rather than the normal Play projection.

## 3. Scenario lifecycle invariants

- A Scenario has one stable identity and exactly one mutable Current Draft row.
- `Scenario.key` is immutable after creation. Draft `metadata.key` and `world.key` mirror it.
- A Draft document is arbitrary JSON and may be incomplete or invalid.
- Strict `ScenarioDefinitionV2` parsing happens during validate/publish, not ordinary persistence.
- A successful Draft write increments `revision`; stale `expected_revision` returns 409.
- Published ScenarioVersions are canonical immutable snapshots and keep their exact content hash.
- Publish creates the next Version only after the platform gate passes.
- Publishing the same canonical content as the current published Version returns
  `409 SCENARIO_PUBLISH_NO_CHANGES`.
- Restoring a Version replaces only the Current Draft, increments its revision, and never mutates
  the source Version.
- Display-name edits never change stable keys or references.
- Stable object-key rename is one server-side atomic authoring command that rewrites every declared
  v2 reference or fails without changing the Draft.
- Deleting a referenced object is rejected by default with its `Used By` references.
- Archiving a Scenario leaves all Versions and existing Games readable. It blocks Draft mutation,
  publish, and creation of new Games from that Scenario.

The Scenario table's `name` is a library projection of the Current Draft metadata name and is
updated in the same transaction. It is not a second independently editable authoring source.

## 4. Validation and readiness

Validation issues contain a severity, stable code, JSON path, message, and, when resolvable, an
object locator (`object_kind`, `object_key`, `field_path`) for Editor navigation.

`ERROR` blocks publish. `WARNING` does not. Authors cannot override the publish gate.

Readiness has four separately reported levels:

1. `STRUCTURALLY_VALID`: strict schema, types, references, and engine contract pass.
2. `MINIMUM_RUNNABLE`: initialization and primary PLAN Actor can materialize a Runtime safely.
3. `MINIMUM_PLAYABLE`: conservative static evidence shows at least one Objective can be advanced by
   declared planning projections and executable Actions.
4. `PUBLISH_READY`: all platform-required checks pass and no ERROR exists.

Reachability is conservative. It may report missing Rules, outcome mappings, Actor access, or
planning projections; it must not claim to prove solvability for every possible world state.

Objective editing continues to serialize `ObjectiveRequirementV2`. It may reuse Fact/value picker UI
from the Condition Builder but does not broaden Objectives into arbitrary `ConditionV2` trees.

## 5. Draft editing contract

Normal autosave replaces the aggregate Draft document using `expected_revision`:

```text
Dirty -> debounce -> Saving(revision N)
      -> Saved(revision N+1)
      -> Conflict(server revision M)
```

A conflict never silently overwrites server data. The UI retains the local document and offers an
explicit reload or retry after reconciliation. The database Draft remains the only server source of
truth; the browser copy is an unsaved editing buffer, not a shadow Draft.

References and `Used By` come from a server-side, schema-aware reference index. Reference links use
stable object-kind/key routes. The World Graph renders Nodes and Relations; Facts remain details of a
selected Node rather than graph vertices.

## 6. Game and Task lifecycle

A formal Game is created only from an explicit published ScenarioVersion ID. Its Player and Version
bindings never change. No API accepts a Scenario key and resolves `latest` implicitly.

Public Game states are:

```text
PENDING_INITIALIZATION -> ACTIVE <-> SUSPENDED
PENDING_INITIALIZATION -> FAILED
ACTIVE/SUSPENDED -> ARCHIVED
```

Goal completion does not archive or complete the Game. An ACTIVE Game may accept another Goal after
the previous Task reaches a terminal state. One Instance may own at most one non-terminal Task at a
time; this invariant is enforced transactionally and at the database boundary.

Goal resolution precedes Task creation:

```text
NEEDS_CLARIFICATION | UNSUPPORTED       no AgentTask is created
ACCEPTED                                  creates a Task with frozen non-empty ObjectiveScope
```

The public Task outcome vocabulary is:

```text
ACTIVE
NEEDS_PLAYER_INPUT
BLOCKED_BY_PLAYER_DECISION
UNREACHABLE_IN_CURRENT_STATE
COMPLETED
ABORTED
```

Internal Agent enums may remain more detailed; the Player DTO performs an explicit mapping. A
terminal/blocking reason is structured data, not inferred only from display text.

Abandon marks the current Task ABORTED, cancels pending approvals and unresolved Operations that
have not applied a world mutation, and does not roll back completed mutations. Archive performs the
same cancellation before making the whole Instance read-only. Every mutation application service,
not merely the HTTP router, enforces the Instance write guard.

## 7. Formal Play orchestration

`PlayOrchestrator.advance_until_pause()` composes the existing exact-Version Generic Agent, Action,
Rule, Operation, and Objective services. It does not interpret Scenario rules itself.

```text
Goal -> resolve -> plan -> execute Action -> settle generic Operation
     -> apply Rule outcome -> update Knowledge -> evaluate -> replan
     -> completion / approval / player input / blocked / safety bound
```

Formal Play deterministically settles ordinary pending Operations instead of asking the Player to
press a debug settle button. The existing generic async Operation path remains the sole mutation
path. Each invocation has hard bounds on transitions and replans, commits atomically to a safe pause,
and is idempotent. Browser disconnect/reload cannot end a Game or erase persisted progress.

A rejected approval persists a Task-scoped canonical proposal signature over Actor, Action, target,
and parameters. Both deterministic and provider planners must pass the same backend validator, which
rejects that exact proposal. Replan prefers valid autonomous candidates. Candidate exhaustion or the
configured replan limit produces a reliable blocked result instead of another approval loop.

## 8. HTTP resources

All endpoints use `/api/v1`, the existing error envelope, UUID resource IDs, ISO-8601 timestamps,
and request-scoped database transactions. List endpoints use stable ordering and cursor pagination
when their collections can grow without a small definition-bound limit.

### Scenario authoring

```text
GET    /api/v1/scenarios
POST   /api/v1/scenarios
GET    /api/v1/scenarios/{scenario_id}
POST   /api/v1/scenarios/{scenario_id}/archive

GET    /api/v1/scenarios/{scenario_id}/draft
PUT    /api/v1/scenarios/{scenario_id}/draft
POST   /api/v1/scenarios/{scenario_id}/draft/validate
POST   /api/v1/scenarios/{scenario_id}/draft/publish
POST   /api/v1/scenarios/{scenario_id}/draft/restore
POST   /api/v1/scenarios/{scenario_id}/draft/rename-key
POST   /api/v1/scenarios/{scenario_id}/draft/delete-object
GET    /api/v1/scenarios/{scenario_id}/draft/references

GET    /api/v1/scenarios/{scenario_id}/versions
GET    /api/v1/scenarios/{scenario_id}/versions/{version_id}
GET    /api/v1/scenario-examples
```

Scenario creation is a discriminated `BLANK`, `CLONE_VERSION`, or `EXAMPLE` request. Clone copies an
exact Version into a new Scenario Draft and rewrites only the new Scenario identity metadata.

### Games and Play

```text
GET    /api/v1/games?status=active|archived
POST   /api/v1/games
GET    /api/v1/games/{game_id}
POST   /api/v1/games/{game_id}/archive
GET    /api/v1/games/{game_id}/history
GET    /api/v1/games/{game_id}/play
POST   /api/v1/games/{game_id}/goals
POST   /api/v1/games/{game_id}/continue
POST   /api/v1/games/{game_id}/tasks/{task_id}/abandon
POST   /api/v1/games/{game_id}/approvals/{decision_id}/approve
POST   /api/v1/games/{game_id}/approvals/{decision_id}/reject
```

Creation and Goal submission require client idempotency keys. Continue and decision requests use
persisted Task/Decision identity and optimistic versions to reject stale browser actions.

### Developer

```text
GET    /api/v1/developer/games/{game_id}/snapshot
GET    /api/v1/developer/games/{game_id}/history
```

Developer responses may include Truth, Knowledge, Actors, ObjectiveScope, Tasks, Plans/Steps,
Operations, Rule outcomes, decisions, Memory, and runtime history. They are never embedded in a
Player response.

## 9. Browser pages

```text
/scenarios                              Scenario Library
/scenarios/new                          Blank / Clone / Example
/scenarios/{id}                         Scenario detail and Version history
/scenarios/{id}/edit/{section}          Current Draft Editor
/scenarios/{id}/versions/{version_id}   immutable Version view
/games                                  Active and archived Games
/games/new                              exact published Version selection
/games/{id}                             Play page with embedded Developer toggle
```

The frontend uses React, TypeScript, Vite, React Router, and a server-state query layer. The first
World Graph uses native SVG. Unit/component tests run in Vitest; browser product E2E runs in
Playwright Chromium. Development and CI use Node 22. FastAPI serves the built assets in production.

## 10. Transaction and error contract

One write request is one database transaction. A service error rolls back the entire request.
Expected conflicts use 409, missing resources 404, disabled/unauthorized developer access 403 or
404, malformed transport input 422, and invalid publish content 409 with structured validation
issues. Application exceptions are translated at the HTTP adapter; services do not import FastAPI.

Required stable conflict codes include:

```text
SCENARIO_DRAFT_CONFLICT
SCENARIO_DRAFT_INVALID
SCENARIO_DRAFT_HASH_MISMATCH
SCENARIO_PUBLISH_NO_CHANGES
SCENARIO_OBJECT_REFERENCED
GAME_INSTANCE_CONFLICT
GAME_INSTANCE_READ_ONLY
GAME_ACTIVE_TASK_EXISTS
ACTION_DECISION_INVALID
REJECTED_PROPOSAL_FORBIDDEN
```

## 11. Verification contract

Every D-stage checkpoint runs backend tests, Ruff lint/format, strict mypy, and migration checks.
From D2 onward it also runs frontend lint, typecheck, unit tests, and production build. D5 adds Game
lifecycle integration tests; D6/D7 add Chromium E2E. Final CI has separate backend, frontend, and E2E
jobs and verifies PostgreSQL migrations, restart/recovery, dual scenarios, exact-Version isolation,
archive read-only behavior, approval rejection, and absence of hidden Truth in every Player DTO.
