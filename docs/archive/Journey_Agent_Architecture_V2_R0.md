# Journey Agent — Architecture V2 / R0 Generic Game Contract

**Status:** Design Freeze Candidate  
**Purpose:** Freeze the target architecture before Phase R implementation.  
**Scope:** Architecture and ownership only. No Editor UI work. No Runtime behavior change in R0.

---

## 0. Why this refactor exists

The project has already achieved two important foundations:

1. Scenario world data can be published into immutable `ScenarioVersion` snapshots.
2. `GameInstance` is isolated and bound to an exact `scenario_version_id`.

However, a large amount of actual gameplay behavior is still Starfire-specific Python: `StarfireRuleset`, fixed Tools and parameter models, seeded Officers/Roles, authority rules, Starfire Goal resolution, PlanningPolicy, fallback plans, and Starfire-specific parts of `GameService`.

The target architecture is therefore:

> **Source code defines the generic engine; ScenarioVersion defines the game.**

A published `ScenarioVersion` must be sufficiently self-contained that the same backend process can create a new `GameInstance` from any supported Version at any later time without editing Python gameplay code, switching Rulesets, depending on `latest`, or redeploying another backend.

---

# 1. Core architecture principles

## 1.1 Engine vs Game Definition

### Generic Engine — Python source code

Python owns mechanisms that are common to every game:

- Scenario/Draft/Version lifecycle
- schema decoding and validation
- exact-version binding
- `RuntimeScope`
- GameInstance lifecycle
- Truth / Knowledge isolation
- generic Rule Engine
- Condition evaluation
- Effect execution / `RuleOutcome` construction
- generic state application
- generic Action execution
- generic WorldOperation / WAIT lifecycle
- Task / Plan / Step / Replan orchestration
- LLM invocation and structured output validation
- transactions, locking, idempotency and auditing
- security and fail-closed enforcement

Python must not know scenario-specific names, characters, map topology, outcome numbers, or game-specific action semantics.

### Versioned Game Definition — ScenarioVersion data

Anything that a future Scenario Editor author can change and that may differ between two published Versions belongs to the Version:

- world structure
- Nodes / Facts
- Relations
- Resources
- initial Truth / Visibility / Access
- Actors and Roles
- Interactions
- Actions and typed Action parameters
- authority policy
- Objectives
- Goal aliases/examples
- deterministic Rules
- rule conditions
- rule effects
- gameplay numbers and outcome codes
- planning hints/templates when needed

**Rule:** if changing a value should create a new playable game version, that value belongs in `ScenarioVersion`, not in Python gameplay code.

---

# 2. Versioning invariants

A `GameInstance` is permanently bound to:

```text
game_instance_id
player_id
scenario_version_id
```

Runtime must resolve all game-definition content through the exact bound `scenario_version_id`.

Forbidden Runtime fallbacks:

```text
scenario_key -> latest
scenario_key -> static Python world
global Starfire gameplay registry
global Starfire NPC seed as runtime authority
```

A backend cache must be keyed by exact immutable identity, conceptually:

```text
scenario_version_id
+ content_hash
+ engine_contract/version
```

Publishing v2 must never change the behavior of an existing or newly-created v1 Instance.

---

# 3. Scenario lifecycle

```text
Scenario
└─ ScenarioDraft                 mutable
      ↓ validate / publish

ScenarioVersion v1              immutable
ScenarioVersion v2              immutable
ScenarioVersion v3              immutable
```

Phase R keeps the current first-version workflow:

```text
1 Scenario
→ 1 mutable Draft
→ N immutable published Versions
```

Multiple concurrent named Draft branches are **out of scope for V2**.

A Draft may be incomplete and temporarily invalid for Publish.

A Published Version must be immutable and remain readable for as long as its declared document schema and engine contract are supported.

---

# 4. ScenarioDefinition v2

The target aggregate definition is:

```text
ScenarioDefinitionV2
├─ schema_version
├─ metadata
├─ engine_contract
├─ initialization
│  ├─ start_node_key
│  └─ primary_actor_key
│
├─ world
│  ├─ node_types
│  ├─ nodes
│  ├─ facts
│  ├─ relations
│  └─ resources
│
├─ actors
│  ├─ roles
│  └─ actor_profiles
│
├─ interactions
├─ actions
├─ rules
├─ objectives
├─ goal_resolution
└─ planning
```

The definition may continue to be stored as an aggregate Draft document and immutable Version snapshot. R0 does **not** require normalization into many relational definition tables.

Runtime state remains Instance-scoped relational state.

---

# 5. World definition

## 5.1 Node

A Node is a versioned world entity/location that can own Facts and support Interactions.

Example keys:

```text
northern_valley
starfire_outpost
room_101
medical_lab
server_room
```

Node type is versioned data, not a fixed Starfire enum.

A scenario explicitly declares `initialization.start_node_key`; the generic engine must not require a universal `HEADQUARTERS` node type.

## 5.2 Fact

A Fact is a typed property attached to a Node.

V2 minimum Fact value types:

```text
STRING
ENUM
INTEGER
BOOLEAN
```

A Fact definition owns:

```text
key
display metadata
value_type
allowed_values when ENUM
initial_value
initial_visibility
```

Definition belongs to ScenarioVersion.

Current authoritative Fact value belongs to GameInstance Truth.

Knowledge never replaces Truth; it is a filtered projection of what the player/Agent may know.

## 5.3 Resource

Resources are versioned definitions with generic numeric runtime balances.

Minimum definition:

```text
key
display metadata
initial_value
minimum
maximum
reservation_supported
```

Python must not have special built-in fields for:

```text
soldiers
food
gold
morale
```

Those are ordinary resource keys in a specific game.

## 5.4 Relation

A Relation is versioned semantic topology/data:

```text
source_node_key
relation_type_key
target_node_key
```

### Frozen V2 decision

**Relation does not automatically execute gameplay effects.**

For example:

```text
A --UNLOCKS--> B
```

may help rules, planning, visualization, or selectors understand the world, but it does not by itself mutate `B.access`.

Actual state change is explicit in a Rule Effect.

This avoids hidden behavior and keeps the gameplay source of truth in declarative Rules.

Relation type keys are versioned/open data rather than a closed Starfire-only Python enum, subject to stable-key validation.

---

# 6. Actor and Role model

Actors are part of the Versioned Game Definition.

A Role is scenario-defined gameplay data. Python does not require fixed:

```text
STRATEGIST
GENERAL
STEWARD
```

The generic engine may expose only a small fixed set of engine capabilities, initially:

```text
PLAN
EXECUTE_ACTION
INSPECT_STATE
```

A Role can reference those engine capabilities.

An Actor profile owns, at minimum:

```text
key
name
role_key
persona
doctrine
initial_node_key
allowed_action_keys
authority_policy
```

`initialization.primary_actor_key` identifies the single primary planner actor for V2.

### Frozen V2 decision

- One primary planner actor per GameInstance.
- Multiple execution actors are allowed.
- Actor definitions are versioned.
- Runtime actor identity/location/status/memory are Instance-scoped.
- Dynamic actor spawning/deletion is out of scope for V2.

GameInstance initialization creates Instance-owned actors from the exact Version. Runtime must not auto-appoint all globally seeded Starfire NPCs.

---

# 7. Interaction / Action / Tool boundaries

These three concepts are intentionally different.

## 7.1 Interaction

Interaction describes an abstract capability a Node supports.

Examples:

```text
reconnaissance
repairable
trade
openable
hackable
treatable
```

Interaction is Version data.

It does not execute code.

## 7.2 Action

Action is a versioned operation an Agent may execute.

Examples:

```text
clear_valley
repair_outpost
open_door
hack_terminal
treat_patient
```

Minimum Action definition:

```text
key
name / description
required_interaction_key
execution_mode
parameter_schema
allowed_actor_capabilities
authority_policy
expected_outcomes / planning projection
```

Execution mode V2 minimum:

```text
IMMEDIATE
ASYNC
```

### Action parameter model — frozen V2 decision

Parameters use a restricted typed schema, not arbitrary JSON Schema and not Python/Pydantic authored per Action.

Initial supported parameter types:

```text
STRING
ENUM
INTEGER
BOOLEAN
```

Optional constraints:

```text
required
minimum / maximum for INTEGER
allowed_values for ENUM
default where semantically safe
```

Nested arbitrary objects, arrays, executable expressions, and custom validators are out of scope for V2.

## 7.3 Tool

Python should not define one Tool handler per gameplay Action.

Target model:

```text
execute_action(
    action_key,
    target_key,
    parameters
)
```

The model-facing Tool schema may be generated dynamically from `ActionDefinition`, or the model may use one structured generic Tool. This is an implementation choice for Phase R, provided all execution converges on one generic Action path.

Adding a new Action must not require:

- a new Python handler
- a new Pydantic class
- a new GameService scenario-specific method

Engine tools such as runtime inspection/planning orchestration may remain Python engine tools.

---

# 8. Declarative Rule model

Scenario authors do not write Python or JavaScript and the system does not generate Python gameplay files.

Rules are structured, typed, versioned data interpreted by the Generic Rule Engine.

Minimum conceptual structure:

```text
RuleDefinition
├─ key
├─ phase
├─ action_key
├─ priority
├─ condition
└─ effects
```

V2 phases:

```text
PREFLIGHT
RESOLVE
```

- `PREFLIGHT` rules may deterministically reject an Action before it starts.
- `RESOLVE` rules determine the actual state transition/outcome when an Action resolves.

---

# 9. Condition AST

The initial Condition language is intentionally limited.

Supported primitives:

```text
all
any
not

fact_equals
fact_not_equals
fact_in
fact_compare

resource_compare
parameter_compare

node_visible
node_accessible
relation_exists
```

Allowed operands may reference only:

```text
$current_target
Action parameters
explicit Node / Fact keys
Node selected through a Relation
current Instance Resources
Operation context
```

Forbidden:

```text
Python
JavaScript
filesystem
network
dynamic imports
SQL/query strings
loops/recursion
arbitrary function calls
```

The Rule Engine is pure with respect to persistence: it receives an immutable rule state/context and returns an outcome description.

---

# 10. Effect model

Initial supported Effects:

```text
set_fact

reveal_fact
hide_fact

reveal_node
hide_node
set_node_access

adjust_resource
reserve_resource
release_resource

emit_outcome
emit_failure

write_memory_event
```

All resolved references must belong to the current exact ScenarioVersion and current GameInstance.

Effects may never directly modify another Instance.

---

# 11. Rule matching semantics

### Frozen V2 decision

Rule resolution must be deterministic.

For each `(phase, action_key)`:

1. Load rules from the exact ScenarioVersion.
2. Evaluate conditions against the current authoritative state/context.
3. Order matching rules by descending `priority`.
4. The highest-priority matching rule is selected.
5. If multiple matching rules share the same highest priority and would produce a resolution, Runtime **fails closed** with an ambiguous-rule error.
6. If the Action requires a resolution and no valid rule matches, Runtime **fails closed** with a no-resolution-rule error.

A lower-priority unconditional rule may act as an explicit default/fallback branch.

V2 does not implicitly merge multiple independently matching gameplay rules. If multiple effects belong to one resolution, they are listed together inside the selected Rule.

This keeps replay, validation, diffing and debugging straightforward.

---

# 12. Generic RuleOutcome

Current Starfire-specific output fields such as:

```text
casualties
morale_delta
food_delta
gold_delta
```

must become generic mutations.

Target conceptual output:

```text
GenericRuleOutcome
├─ fact_updates
├─ node_visibility_updates
├─ fact_visibility_updates
├─ node_access_updates
├─ resource_mutations
├─ resource_reservations
├─ memory_events
├─ operation_result
└─ failure
```

Example:

```text
soldiers -= 18
```

becomes conceptually:

```json
{
  "resource_key": "soldiers",
  "operation": "ADD",
  "value": -18
}
```

The Rule Engine builds the outcome.

It does **not** write the database.

---

# 13. GameService boundary

The target `GameService` is a Generic Runtime State Service.

It owns:

```text
RuntimeScope validation
authoritative Truth reads
Knowledge/Visibility/Access reads
Resource reads/reservations
row locking
latest Instance state reload
Rule Engine invocation
RuleOutcome validation
atomic outcome application
audit
commit / rollback
```

It must not contain:

```text
Starfire Node/Fact keys
Starfire Resource names
if MILITARY / elif CONSTRUCTION / elif TRADE
Starfire Ruleset construction
legacy Starfire gameplay projections as Runtime authority
```

`GameService` must not import `app.scenarios.starfire` on the final V2 path.

---

# 14. WorldOperation / WAIT

Existing asynchronous lifecycle is preserved.

```text
execute_action
↓
WorldOperation PENDING
↓
WAIT_FOR_WORLD_EVENT
↓
resolve operation
↓
load exact ScenarioVersion
↓
Generic Rule Engine
↓
GenericRuleOutcome
↓
atomic application
↓
Task resumes
```

WorldOperation becomes generic and stores:

```text
game_instance_id
action_key
target_key
execution_mode
parameters
source_task/step/actor references
idempotency identity
status/result
```

Operation resolution must not branch on Starfire-specific operation types.

---

# 15. Objective model

Objective content remains Version data and is evaluated deterministically against authoritative Truth.

Objective definitions own:

```text
key
name / description
typed completion requirements
prerequisites
subsumption
goal aliases/examples
```

Objective accepted values must be typed consistently with Fact definitions.

Task completion remains:

```text
Persisted Objective definitions
+ frozen ObjectiveScope
+ authoritative Instance Truth
→ deterministic ScopedObjectiveEvaluation
```

No LLM may directly declare task completion.

No separate Starfire Objective Catalog may override Version content.

---

# 16. Goal resolution

Starfire-specific keyword/regex resolution is removed from the target architecture.

Generic resolution order:

1. exact Objective key/alias match
2. deterministic alias/example matching where unambiguous
3. if unresolved, LLM chooses only from the exact Version's Objective candidates
4. LLM output is validated against the Objective Catalog
5. ambiguous cases return clarification candidates
6. an Objective not present in the exact Version can never be invented

---

# 17. Planning and Replan

The Planner is generic.

It builds context from the exact ScenarioVersion and current Knowledge:

```text
visible Nodes/Facts/Relations
Actors
Actions
parameter schemas
permissions
known preconditions
declared/derivable outcomes
Objective requirements
Objective prerequisites
execution mode
WAIT requirements
```

PlanValidator generically checks:

```text
Actor exists in exact Version/Instance
Action exists
Actor is authorized
target supports required Interaction
parameters are valid
WAIT pairing is valid
known state does not prove Action impossible
terminal effects remain compatible with frozen ObjectiveScope
completed effects are not repeated illegally
```

Known-state deterministic Rule simulation may be used to reject an Action that is already provably doomed from the Agent's current Knowledge.

The Validator must never use hidden Truth.

Planning hints/templates may exist as Version data, but:

> **Planning metadata is advisory and may not override Rule definitions.**

Rules remain the executable gameplay source of truth.

---

# 18. Engine Contract

`BehaviorBundle` is reinterpreted as a generic engine compatibility contract rather than a domain-specific Python package.

Target example:

```text
declarative-rule-engine@1
```

It declares compatibility with versions of:

```text
ScenarioDefinition schema
Condition AST
Effect AST
Action schema
Planning projection
Outcome applicator
```

It does not contain:

```text
Starfire world
Starfire actors
Starfire objectives
Starfire tools
Starfire rules
Starfire planning policy
```

Different games can bind the same engine contract while carrying entirely different ScenarioVersion content.

Published old Versions remain runnable only while their declared schema decoder and engine contract implementation are retained.

---

# 19. Persistence ownership

R0 freezes logical ownership, not physical SQL table layout.

## Definition persistence

Recommended direction:

```text
ScenarioDraft
→ mutable aggregate ScenarioDefinition document

ScenarioVersion
→ immutable aggregate ScenarioDefinition snapshot
```

The full semantic Version content participates in canonical serialization and content hash.

Editor-only layout metadata remains outside Runtime semantic hash.

## Runtime persistence

Instance-scoped mutable state remains relational/persistent:

```text
GameInstance
Truth
Knowledge
Visibility
Access
Resource balances/reservations
GameInstanceActors
Session
Memory
Task
Plan
Step
Decision
WorldOperation
Tool/Action execution audit
```

---

# 20. Backward compatibility

R1+ may introduce `ScenarioDefinition v2`, but existing v1 Versions must not become unreadable.

Required migration principle:

```text
v1 decoder retained
v2 decoder added
```

Old Versions and old persisted Instances must either:

1. continue through a supported legacy compatibility path, or
2. be migrated in a tested, deterministic way before the legacy runtime path is removed.

Do not rewrite immutable old Version content in place.

The final Starfire migration must prove that old Version behavior does not drift after newer Versions are published.

---

# 21. Explicit V2 non-goals

The following are intentionally out of scope unless a later phase proves they are necessary:

- arbitrary Python/JavaScript scripting
- dynamic gameplay plugins/modules
- Turing-complete DSL
- arbitrary SQL/database expressions
- arbitrary nested Action parameter objects
- random/probabilistic rules
- full game clock / turn scheduler
- dynamic Actor spawning/deletion
- multiple primary planner Agents
- multiplayer shared-world concurrency
- multiple concurrent Draft branches
- user-defined Engine primitives
- live collaborative Editor
- automatic code generation from Editor content

### Randomness

V2 is deterministic by default.

If randomness is added later, it must be seeded, persisted and replayable. It is not part of R0–R8 unless required by a concrete scenario.

---

# 22. Starfire as a normal V2 game

The final architecture must allow Starfire itself to be represented as ordinary versioned data.

For example, the current behavior:

```text
Action: clear_valley

WHEN:
  target.ambush_status == ACTIVE
  AND related_supply.supply_status != DISRUPTED

THEN:
  valley_intelligence = COMPLETE
  soldiers -= 18
  morale -= 10
  reveal supply route
  outcome = DEFEAT
  failure = ENCOUNTER_DEFEAT
```

belongs in Starfire's ScenarioVersion, not in `StarfireRuleset`.

Likewise:

- casualties 18 / 2 / 4 / 3 / 6
- morale deltas
- repair resource requirements
- trade-route conditions
- reveal/unlock behavior
- actor personas and roles
- Action definitions and parameters
- authority policy
- Goal aliases
- recovery/planning hints

must migrate to data if they are part of the authored game.

---

# 23. R0 characterization baseline required before Runtime refactor

R0 design can be completed without changing Runtime, but **before R1–R7 modify behavior, Codex must add/verify characterization tests** that lock the existing Starfire behavior.

Minimum black-box baseline:

## Goal / Objective / Planning
- Starfire Goal resolves to the expected Objective Scope.
- ObjectiveScope stays frozen across Replan.
- Planner only receives Knowledge, never hidden Truth.
- Task completion is authoritative Objective evaluation, not LLM declaration.

## Military failure / Knowledge / Replan
- first `CLEAR_VALLEY` with active ambush + active supply produces deterministic defeat
- casualties = 18
- morale delta = -10
- supply route becomes revealed/known/available as currently implemented
- failure is recoverable
- Replan can target the newly known supply route

## Supply disruption
- `DISRUPT_SUPPLY` updates supply state to `DISRUPTED`
- village guide support produces the current lower casualty branch
- no-guide branch produces the current higher casualty branch

## Second valley clear
- after supply disruption, `CLEAR_VALLEY` succeeds
- valley becomes SAFE
- ambush becomes CLEARED
- outpost unlocks
- current casualty/morale behavior is preserved

## Repair / Trade
- repair preconditions and resource costs match current behavior
- trade-route open conditions match current behavior
- successful terminal Fact updates remain identical

## Runtime lifecycle
- async Action creates WorldOperation
- paired WAIT pauses Task
- resolution rereads latest authoritative Truth
- operation resolution is idempotent
- Task resumes correctly after settlement

## Isolation / Recovery
- two Instances of the same Version do not share state
- two different Versions do not drift into each other
- restart/recovery preserves exact Instance/Version ownership

These tests are the migration oracle. Refactor stages may change internal implementation but must preserve these externally observable semantics until Starfire is intentionally represented by equivalent v2 data.

---

# 24. R0 acceptance criteria

R0 is complete when:

- Engine vs Game Definition ownership is frozen.
- `ScenarioDefinition v2` conceptual structure is frozen.
- Relation semantics are frozen.
- Actor/Role ownership is frozen.
- Interaction/Action/Tool boundary is frozen.
- Action parameter model is frozen.
- Declarative Condition/Effect primitives are frozen.
- Rule matching semantics are frozen.
- Objective/Goal ownership is frozen.
- exact-version Runtime invariant is frozen.
- Engine Contract meaning is frozen.
- V2 non-goals are explicit.
- every known Starfire hardcoded gameplay concern has a target ownership location.
- characterization-test checklist is agreed before Runtime refactoring.
- no Editor UI work begins before Phase R succeeds.

---

# 25. Implementation handoff — Phase R

After this R0 design is accepted, Codex should implement Phase R in this order:

```text
R1 — ScenarioDefinition v2
R2 — Declarative Rule Engine
R3 — Versioned Actor / Role Runtime
R4 — Generic GameService / Outcome Applicator
R5 — Generic Action / Tool / WorldOperation
R6 — Generic Goal / Planning / Replan
R7 — Starfire Data Migration
R8 — Dual-Scenario Proof / Hardening
```

Before behavior-changing implementation begins, first establish the R0 characterization baseline above.

The final R8 proof is:

```text
Version A = Starfire
Version B = a genuinely different game
```

Using the **same backend process**:

```text
Instance A → Version A
Instance B → Version B
```

Both must run their own complete Agent loops with:

- different world
- different actors
- different resources
- different actions
- different rules
- different objectives

without:

```text
modifying Python gameplay code
switching Rulesets
redeploying
using latest-version fallback
sharing runtime state
```

After restart, both Instances must recover against their exact original Versions.

---

# 26. Codex instruction after receiving this manual

Treat this document as the R0 target architecture.

Before implementing R1:

1. Compare this contract against the current code.
2. Report only true blockers or contradictions that would prevent implementation.
3. Add/verify the R0 Starfire characterization tests.
4. Do not redesign the architecture unless a concrete current-code constraint makes a frozen contract impossible.
5. If no blocker remains, start R1 exactly from `ScenarioDefinition v2`.
6. Keep each R stage as a separate tested checkpoint.
7. Do not resume Phase D / Editor work until R8 passes.

