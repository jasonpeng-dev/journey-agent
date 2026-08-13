# Phase R Final Hardening

This checkpoint finalizes the internal v2 application-service architecture. It does not add
Scenario, Editor, or Play HTTP endpoints; those transport contracts remain Phase D work.

## Frozen runtime contracts

- A Task owns one non-empty, canonical `ObjectiveScope`. Its keys, exact ScenarioVersion catalog
  reference, and content hash are frozen at creation. Completion is AND across every requirement
  of every scoped Objective. ORM guards and database triggers reject later drift.
- Planning may delegate an Action only to an active exact-Version Actor whose allowed Actions,
  Role capabilities, and Actor/Action authority policies permit it. Execution performs the same
  checks again. Autonomous-limit and approval-required policy results fail closed through an
  Instance-owned `ActionDecisionRequest`; approval is consumed by exactly one Action input.
- Deterministic planning remains the test/offline provider. The generic provider protocol and
  OpenAI-compatible adapter support structured Objective selection and Plan proposals. All model
  output is schema-validated and then validated against the exact Version, frozen scope, current
  Knowledge, Actor capabilities, authority, target visibility/access, and Action definitions.
  Hidden Truth is never included in provider planning context.
- Async resolution reloads the exact Version and current locked Instance Truth. Recovery verifies
  Task, Operation, Decision, Session, Actor, state, and memory ownership before returning a graph.

## Legacy boundary

The formal Runtime is v2-only. It has no v1 decoder, static Starfire world/ruleset, Starfire Tool
handler, fixed Goal resolver, gameplay fallback plan, or Scenario-key/latest-version lookup.
Starfire and Medical Emergency execute through the same generic services and declarative rules.

## Phase D boundary

Draft/validate/publish, exact-version load, GameInstance creation, initialization, execution, and
recovery are complete as internal application services. HTTP resource design, Editor APIs, Play
APIs, and UI workflows are intentionally deferred to Phase D.
