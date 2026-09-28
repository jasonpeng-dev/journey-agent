# Journey Agent Architecture

This document is the canonical high-level architecture for Journey Agent.
Detailed authoring and portability semantics live in
[Scenario authoring](scenario-authoring.md). The player Goal contract lives in
[Custom Goals and Task Compilation](custom-goals.md). The detailed planning
boundary lives in [Agent Planning V2](agent-planning-v2.md), and GameInstance
history lives in [GameInstance lifecycle](game-lifecycle.md). Material under
docs/archive/ is historical and is not current implementation authority.

## 1. Product boundary

Journey Agent is a generic, data-driven Scenario runtime with:

* a Web Scenario Editor and explicit Draft lifecycle;
* portable Scenario artifact import/export;
* natural-language Goal resolution;
* an LLM Planner and deterministic Validator;
* declarative Action and Rule execution;
* an explicit Truth/Knowledge boundary; and
* auditable Formal PLAY and Game history.

Scenario data supplies content and contracts. Generic source code interprets
that data; a Scenario key must not select a custom Planner, Validator, Runtime,
or persistence branch.

The current authored document contract is ScenarioDefinitionV3. Runtime paths
that still consume the normalized ScenarioDefinitionV2 semantic model receive
an in-memory projection from V3. This compatibility boundary is intentional:
V3 owns current authoring and portable serialization, while V2 remains the
semantic/runtime and historical compatibility vocabulary where the code still
requires it.

## 2. Authoring authorities

The authoring side has separate authorities:

~~~text
external .scenario.json artifact
  -> read-only preview
  -> explicit import
  -> new Scenario lifecycle identity
       -> one persisted Current Draft
       -> browser Working Copy
            --manual Save-->
       -> Current Draft revision
            --Validate-->
       -> readiness and diagnostics
            --Publish-->
       -> immutable Published ScenarioVersion
~~~

A Scenario is a series identity with exactly one Current Draft and zero or more
immutable Published Versions. The browser Working Copy is a client-side view of
the Draft being edited; it is not a second persisted authority. Typing,
structured Editor transforms, reference analysis, completeness guidance, and
initialization previews can operate on the Working Copy without saving it.

Manual Save is the only normal path that persists a Working Copy as the Current
Draft. The write includes the expected Draft revision and fails closed on a
stale revision. Validate is diagnostic; Publish validates again and creates the
next immutable Version. Publishing does not mutate an existing GameInstance.

### 2.1 Portable artifact boundary

A portable artifact contains the canonical authored ScenarioDefinitionV3 and
portable metadata/hash. It does not carry database lifecycle IDs, Draft
revision, Published Version number/history, Game ID, timestamps, or validation
cache state.

Import always creates a new Scenario series. The target key defaults to the
artifact key and can be explicitly changed. A key conflict is reported; import
does not merge, overwrite, or invent an automatic suffix.

* A Draft artifact creates Scenario plus editable Current Draft. It creates no
  Published Version and no Game.
* A Release artifact creates Scenario plus editable Current Draft and
  Published Version v1. Source history/version numbering is not copied, and no
  Game is created.
* Preview is read-only. The Web adapter reads the selected browser file and
  sends its JSON to the backend; the CLI reads a local file. Neither adapter
  changes the database during preview.

The exact artifact envelope, schema/version checks, target-key rules, and
unsupported legacy behavior are owned by Scenario authoring, not by the
planning or Game lifecycle documents.

## 3. Runtime identity hierarchy

The durable runtime hierarchy is:

~~~text
Player
  -> GameInstance
       -> exact Published ScenarioVersion
            -> AgentTask
                 -> frozen FormalGoalContract
                      -> Dependency Closure / PlannerInput
                           -> AgentPlan / AgentStep
                                -> WorldOperation
~~~

Creating a Game requires an exact immutable Published ScenarioVersion. A mutable
Draft, a Scenario pointer without a Version, or a later publication cannot
rebind an existing Game. A Version imported from a Release artifact is just
another Published Version at runtime; its source artifact history is not a
runtime identity.

The Formal Goal contract is frozen when a Goal becomes an AgentTask. REPAIR and
REPLAN may change the public planning projection, but cannot broaden or replace
that contract.

## 4. Goal, planning, validation, and Runtime

The normal runtime path is:

~~~text
Goal text
  -> Goal resolution and clarification
  -> frozen FormalGoalContract bound to one ScenarioVersion
  -> Dependency Closure
  -> canonical PlannerInput
  -> Planner proposal
  -> deterministic Validator and bounded REPAIR
  -> accepted AgentPlan
  -> Runtime execution
  -> Truth mutation and Knowledge projection
  -> remaining-plan validation
  -> REPLAN or deterministic completion
~~~

Goal Resolution decides the player's WHAT. The Planner decides HOW from the
bounded public input. Dependency Closure collects relevant public contracts; it
does not choose an Actor, source, route, order, or recovery plan. The Validator
checks a submitted segment and projected sequential state. Runtime is the only
layer that mutates world Truth and settles WorldOperations.

The detailed Objective/Goal source compatibility, Provider profiles, Closure,
PlannerInput, REPAIR, REPLAN, and stop reasons are maintained in
Agent Planning V2. They are not duplicated here.

## 5. Truth and Knowledge

Truth is authoritative mutable instance state used by Rules and deterministic
completion evaluation. Knowledge is the public projection available to the
Planner, Validator, and Player.

~~~text
UNKNOWN != false
UNKNOWN != zero
UNKNOWN != unavailable
UNKNOWN != blocked
~~~

Hidden Truth never enters PlannerInput or player responses. A legal survey,
inspection, communication, or authored public effect may reveal Knowledge;
inference alone does not. A Derived State is computed on read from the exact
ScenarioVersion and either complete Truth or public Knowledge. It is not a
persisted runtime row and cannot be directly set by an Action or Rule.

The completion evaluator may retain separate Truth and Knowledge results. A
hidden Truth value is not presented as player-visible completion while the
corresponding requirement remains Knowledge UNKNOWN.

## 6. GameInstance and persistence boundary

A GameInstance owns instance-scoped Truth, Knowledge, formal history, runtime
revision, and pacing state. Archive, Checkpoint, and Fork operate on that
identity while preserving exact ScenarioVersion binding. The complete stable
gate, locking, materialization, provenance, idempotency, and inherited-history
contract is in [GameInstance lifecycle](game-lifecycle.md).

Scenario deletion belongs to the authoring boundary. A deletion-impact check
looks across all Published Versions and blocks deletion while any GameInstance
depends on one of them. A successful deletion removes the Scenario-owned
Draft, Versions, and presentation rows atomically; it never silently deletes a
Game. See [Scenario authoring](scenario-authoring.md).

## 7. API and repository boundaries

| Surface | Responsibility |
| --- | --- |
| /api/v1/scenarios | Scenario series, Draft, validation, sandbox, references, Version history, deletion, and authoring transforms |
| /api/v1/scenarios/artifacts/* | Portable artifact preview and new-Scenario import |
| /api/v1/scenarios/{id}/draft/artifact | Persisted Current Draft export |
| /api/v1/scenarios/{id}/latest/artifact | Current Published Version export |
| /api/v1/scenarios/{id}/versions/{version_id}/artifact | Exact Published Version export |
| /api/v1/games | Game creation from an exact Published Version and Formal PLAY lifecycle |
| /api/v1/developer/games | Credential-gated Truth and internal snapshots |
| /health and /ready | Process and database readiness |

Repository responsibilities:

| Path | Responsibility |
| --- | --- |
| app/domain | V3 authored contract, V2 normalized semantic/runtime vocabulary, and domain contracts |
| app/agent | Goal resolution, Formal Goal, Closure, PlannerInput, Provider, Validator, and Agent loop |
| app/services | Scenario/Draft/Version and portability services, Game lifecycle, PLAY, actions, and projections |
| app/scenarios | Versioned document parsing, validation, serialization, semantic diff, and portability codec |
| app/api | HTTP adapters and DTOs |
| app/cli.py | Local artifact validate/import/export adapter |
| frontend/src | Scenario Library, Editor, artifact UX, and Formal PLAY |
| migrations | Database schema history |
| tests | Unit, contract, integration, portability, lifecycle, and browser coverage |

Fresh installation follows the same boundary: migrations run, the application
starts with empty Scenario and Game libraries, and the tracked official example
is imported explicitly through Web or CLI. Startup seed is not the production
authority.

## 8. Canonical documents

* [Scenario authoring](scenario-authoring.md) owns Editor Working Copy,
  Current Draft, validation/readiness, safe reference editing, Version history,
  restore preview, artifact portability, and Scenario deletion.
* [Custom Goals and Task Compilation](custom-goals.md) owns the player Goal,
  Goal Required slots, and WHAT/HOW contract.
* [Agent Planning V2](agent-planning-v2.md) owns Closure, PlannerInput,
  Provider profiles, Validator, REPAIR, REPLAN, and planning continuity.
* [GameInstance lifecycle](game-lifecycle.md) owns Game binding, Archive,
  Checkpoint, Fork, runtime materialization, and formal history.
* This document owns only the high-level topology and authority boundaries.

Historical implementation notes remain under docs/archive/ and should not be
used to infer the current product path.
