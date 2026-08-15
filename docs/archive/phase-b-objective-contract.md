# Phase B0 — Objective Contract and Characterization

## Status and boundary

Phase B0 defines pure domain semantics for future goal-scoped planning. The new
catalog is deliberately not registered in `ScenarioBinding` and is not consumed by
`AgentTask`, `TaskOrchestrator`, `PlanValidator`, `TaskService`, fallback plans, or
the debug API. Production behavior therefore remains the Phase A Full-Starfire flow.

This phase adds no ORM field, migration, resolver, endpoint, prompt change, early
termination, relation persistence, or planning integration.

## Domain model

### ObjectiveDefinition

An Objective describes a desired terminal world state. It contains:

- a stable scenario-owned key;
- a player-facing name and description;
- one or more terminal fact requirements;
- optional public prerequisite state constraints;
- optional scenario-specific subsumption metadata.

It does not contain Tools, Steps, a preferred sequence, failure scripting, or hidden
mechanics.

### ObjectiveScope

`ObjectiveScope` is an immutable semantic value intended to be owned by a future
`AgentTask`, never by an `AgentPlan`. Its invariants are:

- one scenario and one static catalog version;
- at least one explicit Objective;
- unique, lexically ordered Objective keys;
- AND completion across all explicit Objectives;
- no implicit prerequisite or side-effect expansion.

The Starfire Catalog rejects unsupported keys and rejects a Scope containing
`FULL_NORTHERN_RECOVERY` together with an atomic Objective that Full already
subsumes. Ordinary multi-objective Scopes never normalize to Full.

### Completion and verification

Each `ObjectiveDefinition.completion_requirements` tuple is the single pure source
for both:

- authoritative per-objective and whole-Scope evaluation; and
- future final Strategist verification requirements.

The B0 evaluator accepts a `ScenarioRuntimeState`, evaluates every required fact,
and returns immutable per-requirement, per-objective, and Scope-level results. It is
not connected to production completion yet.

### Prerequisite

A prerequisite is a public state constraint that may be needed before attempting an
Objective. It is planning information, not a Player Objective. Reading prerequisites
does not mutate or expand `ObjectiveScope`.

Prerequisites intentionally contain no Tool sequence or solver behavior. Hidden
mechanics such as the first-clear defeat and the undiscovered enemy supply route are
not represented. Reconnaissance is not declared as a prerequisite for securing the
valley because the deterministic backend permits a direct clearance attempt.

### Goal resolution lifecycle

B0 defines the vocabulary needed by B1 and B2:

```text
UNRESOLVED
  → RESOLVED
  → CONFIRMED

UNRESOLVED
  → NEEDS_CLARIFICATION
  → RESOLVED
  → CONFIRMED

UNRESOLVED
  → UNSUPPORTED
```

Only a future confirmed Task Scope may be frozen and used for normal Planning.
`GoalResolutionResult` represents pure resolution output and provenance; it does not
persist or freeze anything in B0.

## Starfire Objective Catalog

| Objective | Terminal requirements | Public prerequisite constraints |
|---|---|---|
| `GATHER_VALLEY_INTELLIGENCE` | Intelligence is `PARTIAL` or `COMPLETE` | None |
| `SECURE_NORTHERN_VALLEY` | Valley security is `SAFE` | None; Recon remains a strategy choice |
| `RESTORE_STARFIRE_OUTPOST` | Outpost is `OPERATIONAL` or `RESTORED` | Valley is `SAFE` |
| `OPEN_NORTHERN_TRADE_ROUTE` | Trade route is `OPEN` | Valley safe, outpost operational, village has `GUIDE` or `SUPPLIES` |
| `FULL_NORTHERN_RECOVERY` | Valley safe AND outpost operational/restored AND trade route open | Trade support required for the trade attempt |

Full Recovery deliberately preserves the Phase A production evaluator's terminal
conjunction. That old conjunction does not include intelligence, so Full currently
subsumes Secure, Restore, and Open, but not Gather. If product semantics should make
intelligence a Full terminal requirement, that decision must be made before B1
persists frozen Scopes.

Node access and resource authority remain enforcement concerns of ToolExecutor,
GameService, and the Ruleset. They are not duplicated into an Objective DSL.

## Side effects

Ruleset outcomes continue to own all world side effects. For example, repairing the
outpost can unlock the Northern Trade Route node while its status remains `CLOSED`.
That access change does not add `OPEN_NORTHERN_TRADE_ROUTE` to a Restore-only Scope
and does not authorize a trade-route test.

## Relation knowledge projection

`project_known_relations()` defines the minimum future projection rule:

- both endpoints present in Known Node/Access state: include the relation;
- either endpoint absent/hidden: omit the entire relation;
- a `LOCKED` endpoint remains known and does not suppress an otherwise safe relation.

The projection is pure and transient. B0 does not persist relation knowledge or add
relations to the Planner payload.

## Phase B baseline characterization

The B0 characterization suite records that production currently:

- rejects a recon-only Plan through Full-Starfire coverage validation;
- refuses Restore-only completion while Trade remains closed;
- uses different requirements for final Plan verification and Task completion;
- persists ambiguous and unsupported Goal text as an immediately active Task;
- has no Objective Scope or clarification lifecycle fields;
- lets the default Mock Planner ignore Goal text and return one fixed plan;
- sends Goal text, but no frozen Scope, to Replan;
- omits Relations from the planning request;
- advertises Known + Locked targets while execution still fails closed on Access.

These tests intentionally assert current behavior. Later Phase B steps should update
or replace them only when the corresponding production boundary changes.

## B1 implications

B1 must persist more than a list of keys. The minimum Task lifecycle needs:

- resolution status;
- canonical Objective Scope;
- catalog version;
- resolver source and version;
- confirmation provenance;
- an immutable transition once Scope is confirmed and normal Planning starts;
- an explicit legacy compatibility source for historical Full-Starfire Tasks.

The legacy Full default must never be applied to a new ambiguous or unsupported Goal.

## Explicit non-goals

- ORM persistence or migrations
- natural-language Goal resolution
- clarification API or UI
- production planner, validator, completion, verification, or fallback integration
- early termination
- Relation injection or persistence
- Objective, Planning, Condition, or Rule DSLs
- generic dependency graphs or Knowledge Graphs
- Scenario persistence/version/editor systems
- ToolExecutor, GameService, multi-agent, worker, or concurrency redesign
