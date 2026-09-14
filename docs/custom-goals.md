# Custom Goals and Task Compilation

This document is the product and contract guide for natural-language Custom
Goals. It describes the behavior implemented by the generic runtime;
it is not a branch changelog, release note, or Scenario-specific playbook.

For the high-level system boundary, see [Architecture](architecture.md). For
the detailed planning and validation contract, see
[Agent Planning V2](agent-planning-v2.md). Scenario authors should also read
[Scenario authoring](scenario-authoring.md).

## 1. Product model

The player starts with a sentence, not an Objective ID or an Action script:

```text
natural-language Goal
  -> Goal resolution
  -> frozen FormalGoalContractV1
  -> Dependency Closure
  -> canonical PlannerInput V2
  -> Planner proposal
  -> deterministic Validator
  -> Runtime execution
  -> public Knowledge / REPLAN
  -> deterministic completion
```

Goal resolution determines the player's requested WHAT. It grounds public
entities, interprets a supported terminal requirement, validates it against
the exact immutable ScenarioVersion, and freezes the result. The Planner
decides HOW to achieve that result from the public planning input. Runtime is
the only layer that mutates world Truth; completion is evaluated
deterministically from the authored contract and current state.

The same flow is used for INITIAL planning, bounded internal REPAIR, and
REPLAN. REPAIR corrects a rejected proposal before execution. REPLAN starts a
new planning cycle after execution or a Knowledge change. Neither may broaden
or replace the frozen Goal.

## 2. PREDEFINED and AD_HOC_DYNAMIC

There are two implemented Goal sources:

### PREDEFINED

`PREDEFINED` is the compatibility path for catalog-disabled or older
immutable ScenarioVersions. A submitted sentence may match one authored
Objective key, canonical name, alias, or example. Only a unique explicit match
is accepted; ambiguity requires clarification, and an unmatched sentence is
not silently mapped to the nearest Objective.

### AD_HOC_DYNAMIC

The catalog-enabled path resolves a sentence against public World Goal
State semantics. It can compile typed `FACT`, `RESOURCE_AT_LEAST`, and public
`DERIVED_STATE` requirements with implicit `AND` semantics. The backend assigns
stable requirement identity and compiles the same Formal Goal contract used by
predefined Tasks.

Preset Goal text in the browser is only suggested text that fills the normal
editable Goal input. It is not an Objective selector, does not submit a
separate identity, and does not create an `ObjectiveDefinitionV2`. Dynamic
submission also does not edit the Draft or create a new ScenarioVersion.

`PARAMETERIZED` is reserved in the domain vocabulary but has no implemented V1
resolver or template source. V1 also has no Goal AST, `OR`, generic `NOT`,
Actor Goal, WorkingGoal, or Milestone lifecycle.

## 3. Grounding and the focused ontology

The Dynamic path is deliberately public and exact-Version scoped:

1. Deterministic grounding resolves an exact public entity or unique public
   topology reference when possible.
2. If deterministic grounding is not unique, bounded semantic grounding may
   return only validated public candidate keys, clarification, or unsupported.
3. The backend builds a focused ontology from public entity identities,
   goal-addressable Fact schemas, public Regions and Resources, and public
   goal-addressable Derived State schemas.
4. Goal Interpretation returns typed candidate semantics.
5. Exact-Version validation rejects unknown keys, invalid values, unsupported
   requirement kinds, hidden completion semantics, and provider-supplied
   identity.

The interpreter sees public names, descriptions, aliases, examples, stable
keys, and allowed typed domains. It does not see current Truth values, hidden
Facts, hidden resource quantities or sources, authored Objectives,
Actions, prerequisites, Knowledge gates, or hidden Derived State dependencies.
An entity may therefore have a public goal-addressable schema while its
current value is `UNKNOWN`.

`UNKNOWN` is a first-class boundary:

```text
UNKNOWN != false
UNKNOWN != zero
UNKNOWN != unavailable
UNKNOWN != blocked
```

No hidden value is smuggled into a provider payload or player response. A
legal public reveal may change Knowledge later, but it does not rewrite the
frozen Goal.

## 4. Goal Required and execution requiredness

Action authoring declares Goal-level required slots with
`ActionDefinitionV2.goal_required_slots`. This is a data-driven statement of
what the player must specify in the Goal before planning. It is deliberately
different from execution parameter requiredness:

| Concept | Owner | Meaning |
| --- | --- | --- |
| Goal-required slot | Player / Resolver | The player's WHAT is ambiguous without this slot. |
| Execution-required parameter | Planner / Validator | The eventual Action invocation must contain this value. |
| Optional or omitted slot | Planner | The runtime may still require a legal value, but the Planner owns the choice. |

When a Goal-required slot is missing, the Resolver returns clarification before
the Planner is called. It does not infer a value from topology, choose an
Actor, or turn a missing slot into an arbitrary Action. An optional omitted
slot is represented as `NOT_SPECIFIED` (or the equivalent absent field) and
remains Planner-owned.

Once the player explicitly supplies a constraint, it is canonicalized into
the Formal Goal and remains frozen across INITIAL, REPAIR, and REPLAN. The
Planner may fill only slots the player left open; it may not replace an
explicit Actor, source, Resource, amount, Target, or other parameter
constraint.

Abstract examples:

- An Action can require the player to identify both an Actor and destination
  Target while leaving route choice to the Planner.
- An Action can require a source Region, Resource, amount, and destination
  Target when those values are part of the requested WHAT; cargo normalization
  and other legal execution details remain validated later.
- A target-only Action can leave Actor selection to the Planner. If the player
  names an Actor, that Actor is retained and cannot be swapped by planning.
- An Action can require a Target while leaving its source `NOT_SPECIFIED`.
  The Planner then chooses a source through authored relations; an explicit
  source is frozen and must satisfy the source-to-Target contract.

## 5. WHAT versus HOW

The Resolver freezes WHAT:

- the terminal requirement or requirements;
- explicit player entity identity;
- explicit Actor, source, Resource, amount, Target, and other parameter
  constraints;
- the exact ScenarioVersion and canonical contract proof.

The Planner chooses HOW when the player did not freeze it:

- Actor and resource source selection;
- route, ordering, and timing;
- supporting Actions and Knowledge-acquisition Actions;
- optional parameters and legal execution details.

Dependency Closure only collects and expands relevant public contracts.
PlannerInput carries the bounded public slice. The Validator checks the
submitted segment and projected sequential state. Runtime executes accepted
steps and mutates Truth. None of these layers may silently reinterpret the
player's frozen WHAT.

## 6. Supporting dependency chains

A final Goal does not have to equal one direct Action. Closure preserves
authored producer and prerequisite possibilities so the Planner can compose a
causal chain. For example, an abstract target-state Goal may have this
dependency shape:

```text
target state
  <- supporting capability
  <- prerequisite Action
  <- required resources, actors, or access
```

The exact chain depends on public state, Action contracts, Rules, resources,
actors, and Knowledge. This is a dependency graph, not a fixed backend
script. Closure never chooses the final Actor, route, source, or ordering and
never emits hidden current Truth.

## 7. Target-specific planning contracts

`PlannerTargetBinding` is a sparse public contract for one Action/Target pair.
It may carry:

- authored required Actor role;
- target-specific preconditions and requirements;
- resource requirements;
- deterministic target effects;
- public Knowledge or UNKNOWN dependency evidence.

Closure retains each legal target binding independently and retains every
compatible active Actor candidate. It does not construct a Cartesian
candidate catalog and does not select one candidate on behalf of the Planner.
Authored target identity and producer identity may be retained even when a
Fact is hidden, but the hidden current value is represented as typed
`UNKNOWN` and is never exposed.

## 8. Truth, Knowledge, and completion

Truth is authoritative Runtime state used by Rules and completion evaluation.
Knowledge is the public projection used by the Planner, Validator, and Player.
Survey, inspect, communication recovery, and other explicit public outcomes
may reveal Knowledge; inference alone cannot do so.

`FACT`, `RESOURCE_AT_LEAST`, and `DERIVED_STATE` requirements are evaluated
deterministically. A Derived State is a computed capability with its own
authored identity and dependency graph; it is not a marker Fact and is not a
persisted runtime row. Resource satisfaction uses currently legal public
Knowledge for planning while authoritative completion uses the exact Runtime
Truth contract.

A hidden Truth value satisfying a Dynamic requirement cannot by itself become
player-visible completion while the requirement remains Knowledge `UNKNOWN`.
The public projection must first make the requirement legally confirmable.

## 9. Clarification, retry, and feedback

Clarification means the system still does not know the player's WHAT. Typical
causes include a missing Goal-required slot, ambiguous public entity, or
ambiguous supported interpretation. The Resolver asks for the missing or
ambiguous information and does not retry until it happens to choose a Goal.

Transient provider or provider-format failures use bounded internal retry when
the request is safe to retry. Unsupported player WHAT, invalid explicit
constraints, and deterministic relation conflicts are not converted into a
different Goal. Internal stage, rejection, and provider codes remain
developer diagnostics; the Player receives the existing safe clarification or
unsupported presentation.

## 10. Abstract examples and V1 boundaries

Use these abstract cases to reason about the boundary:

1. A catalog Goal that names a public Fact or Derived capability freezes that
   typed requirement before planning.
2. A Goal that names a Target freezes the requested WHAT; supporting Actions,
   Actor choice, route, and ordering belong to the Planner.
3. A Goal that states a Resource, amount, source, and Target freezes those
   explicit constraints; the Planner still chooses a legal Actor, route, and
   supporting steps unless the player also specified them.

The V1 boundary intentionally excludes Goal AST composition, generic negation,
parameterized templates, Actor Goals, WorkingGoals, Milestones, and an
implemented persistent Goal dependency graph. These are not part of the
current contract.

## 11. Related authorities

- [Architecture](architecture.md): high-level ownership and runtime boundary.
- [Agent Planning V2](agent-planning-v2.md): Closure, PlannerInput,
  Validator, REPAIR, REPLAN, and continuity details.
- [Scenario authoring](scenario-authoring.md): authoring
  `goal_required_slots`, target contracts, Rules, validation, and publishing.
- [GameInstance lifecycle](game-lifecycle.md): exact-Version binding, Goal
  freeze, Formal PLAY, Archive, Checkpoint, and Fork.
