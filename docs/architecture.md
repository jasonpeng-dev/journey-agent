# Journey Agent Architecture

This document is the canonical high-level architecture for the current
Journey Agent runtime. Detailed planning and validation semantics live in
[Agent Planning V2](agent-planning-v2.md). Scenario authoring semantics live
in [Scenario authoring](scenario-authoring.md). Historical design and
migration notes live under [docs/archive](archive/) and are not current
implementation authority.

## 1. Product and system goal

Journey Agent is a generic, data-driven scenario runtime with:

* natural-language goal resolution;
* an LLM Planner;
* deterministic validation;
* an explicit Truth/Knowledge boundary;
* declarative Action and Rule execution;
* auditable Formal PLAY;
* Knowledge-aware replanning.

Scenario content is supplied by an immutable published ScenarioVersion.
The runtime interprets that content through generic source code. A Scenario
must not require a scenario-key branch in Planner, Validator, Runtime, or
persistence code.

## 2. Runtime identity hierarchy

The durable identity hierarchy is:

    Player
      -> GameInstance
           -> exact published ScenarioVersion
                -> AgentTask
                     -> ObjectiveScope
                     -> AgentPlan / AgentStep
                          -> WorldOperation

A Scenario has one mutable Current Draft and immutable published Versions.
Creating a GameInstance binds it to one exact Version and content hash. Editing
or publishing a later Version never changes an existing GameInstance.

ObjectiveScope is created when a Goal becomes an AgentTask. It is frozen for
the Task lifetime and is not expanded by REPAIR or REPLAN.

## 3. Scenario authoring boundary

A ScenarioDefinitionV2 contains declarative:

* metadata;
* world Nodes, Facts, Relations, Interactions, and Resources;
* Roles and Actors;
* Actions and parameters;
* preflight and resolution Rules;
* Objectives and completion requirements;
* goal-resolution and planning metadata;
* initialization.

Authors provide stable machine keys, public names, descriptions, structured
contracts, and data. They do not add executable gameplay code, a custom
interpreter, a model-specific provider, or a scenario-specific runtime branch.

## 4. Agent runtime overview

The request path is:

    Goal
      -> exact-Version Goal Resolver
      -> frozen ObjectiveScope
      -> Dependency Closure
      -> canonical PlannerInput V2
      -> Provider PlanSegment
      -> deterministic Validator
      -> bounded internal REPAIR if needed
      -> accepted AgentPlan
      -> Runtime Action execution
      -> Truth and public Knowledge update
      -> remaining-plan validation
      -> Player pacing / acknowledgement
      -> REPLAN or objective completion

The application composes the configured provider once and injects it into
the generic resolver and Agent service. API routes and React components are
adapters; they do not duplicate Rule evaluation, objective completion,
authority, or Version semantics.

## 5. Truth and Knowledge boundary

Truth is the authoritative mutable instance state used by Rule evaluation
and objective verification. Knowledge is the public projection used by
Planner, Validator, and normal Player responses.

Hidden Truth is never serialized into PlannerInput. UNKNOWN is not false,
zero, unavailable, or blocked. Runtime may reveal new public Knowledge through
survey, inspect, public Action effects, or an explicit deterministic failure.
Inference alone does not reveal hidden state.

Player projections expose known Nodes/Facts/Relations/Resources, accepted
formal Plan History, safe action results, and pacing state. Developer
projections are separate and credential-gated. Provider payloads, Rule ASTs,
credentials, and raw model reasoning are not player data.

## 6. Planning / validation / execution ownership

PlannerInput construction and Dependency Closure select bounded public
relevance. The LLM chooses Action, Actor, Target, Resource source,
parameters, route, and ordering.

The Validator is deterministic authority over a submitted PlanSegment. It
checks bindings, parameters, locality, command reachability, public
preconditions, resources, target contracts, objective relevance, stop
semantics, and sequential projected state. It only rejects Known
deterministic contradictions.

Runtime is the only layer that mutates Truth and settles WorldOperations.
Knowledge projection and objective verification consume the resulting state.
No layer inserts a missing prerequisite or computes a recovery route for the
Planner.

See Agent Planning V2 for the detailed contract and invariants.

## 7. Formal PLAY lifecycle

Formal PLAY uses a single synchronous HTTP request for each initial planning
or replan action:

    user starts planning
      -> INITIAL_PLAN or REPLAN
      -> internal Provider / Validator / REPAIR loop
      -> one final response
      -> accepted formal Plan History or terminal error

After an accepted Plan, the player acknowledges and executes each Action.
The UI then shows an action briefing/debrief and the next pacing phase. A
Runtime failure, a Knowledge change that invalidates the remaining plan, or a
naturally exhausted INFORMATION_BOUNDARY segment can require player
acknowledgement followed by REPLAN. A completed Objective enters COMPLETED and
does not replan.

Rejected Provider proposals are internal audit records. They are not
player-facing plans and do not create AgentStep or WorldOperation rows.

## 8. Persistence and audit

The main lifecycle entities are:

| Entity | Role |
| --- | --- |
| PlanningCycle | One INITIAL or REPLAN cycle with frozen canonical input |
| PlanningAttempt | One Provider attempt, proposal, violations, and telemetry |
| AgentPlan | Validator-passing executable formal plan |
| AgentStep | Ordered executable Action step |
| WorldOperation | Runtime operation and public outcome |
| PlayerExecutionCheckpoint | Player pacing state, not gameplay authority |

### GameInstance archive and Fork

The lifecycle is deliberately narrower than a save-slot or branch-tree
system:

    ACTIVE --stable archive gate--> ARCHIVED --Fork--> ACTIVE target

Archiving locks the owned root GameInstance, verifies the expected
runtime_revision, and rejects any non-terminal AgentTask, pending
WorldOperation, pending ActionDecisionRequest, or non-zero resource
reservation. A successful archive increments the revision and retains all
runtime Truth/Knowledge rows in read-only form. It does not clean up or
serialize a second snapshot.

Fork is a dedicated materializer. It reads only an ARCHIVED source and the
same exact ScenarioVersion, then performs one transaction:

* COPY node visibility/status, Fact truth/visibility, Resource value and
  visibility/availability, region/relation knowledge, and Actor dynamic state;
* RESET target identity, ACTIVE status, revision 1, creation key,
  reservations, conversation session, and provenance;
* RECOMPUTE static resource/actor metadata and the root current Node from the
  primary Actor, with exact-version validation;
* DROP Tasks, objective scopes, plans, cycles, attempts, operations,
  decisions, pacing history, memory, and messages.

The target stores forked_from_game_instance_id as provenance. A
(player_id, creation_key) is idempotent for the same source and exact
ScenarioVersion; a retry returns the existing target, while reuse against a
different source is rejected. Deleting a source detaches that provenance link
so targets survive.

Provider audit metadata is secret-safe and may include model settings,
timestamps, latency, token usage, request size, finish reason, parsed
proposal, and Validator diagnostics. Raw chain-of-thought and API keys are
not persisted.

All runtime rows are scoped by GameInstance and exact ScenarioVersion
ownership. Goal/action idempotency and Fork creation keys support recovery
without rebinding an instance to a different Version.

Transaction and lock ordering is root-first. Lifecycle archive, delete,
abandon, and writable-scope checks lock the owned GameInstance before
dependent rows. Action mutation paths also establish the root lock before
mutating instance-scoped runtime state; read-only projection paths do not
lock. Fork locks the archived source root, then creates and populates the
target inside one transaction. No path takes a dependent runtime lock and
then attempts to acquire the root lock, preventing a reverse-order cycle
with existing Action mutation.

## 9. API and repository boundaries

| Surface | Purpose |
| --- | --- |
| /api/v1/scenarios | Draft, validation, publication, references, and sandbox |
| /api/v1/games | Player-safe GameInstance, archive/Fork lifecycle, and Formal PLAY |
| /api/v1/developer/games | Credential-gated Truth/internal snapshots |
| /health and /ready | Process and database readiness |

Repository map:

| Path | Responsibility |
| --- | --- |
| app/domain | ScenarioDefinitionV2, ObjectiveScope, world/runtime values |
| app/agent | Goal resolution, PlannerInput, provider, validation, Agent loop |
| app/services | Scenario/Game lifecycle, Formal PLAY, actions, projections |
| app/scenarios | V2 parsing, validation, persistence, built-in definitions |
| app/api | FastAPI adapters and Player/Developer DTOs |
| frontend/src | React/Vite Scenario Library, Editor, and Formal PLAY |
| tests | Unit, contract, integration, lifecycle, provider, and E2E support |
| migrations | Alembic schema history |
| docs | Current architecture, planning, authoring, and historical notes |

## 10. Current canonical documents

* [Agent Planning V2](agent-planning-v2.md): detailed Planner, Validator,
  Runtime, Knowledge, REPAIR, REPLAN, and continuity contract.
* [Scenario authoring](scenario-authoring.md): Draft, Editor, validation,
  sandbox, publication, and Version lifecycle.
* [Archive](archive/): historical design and migration notes only.
