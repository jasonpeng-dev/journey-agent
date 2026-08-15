# PlanningContext V1

`PlanningContext V1` is the canonical provider input for generic Goal planning. It is built from
the exact `ScenarioVersion`, the Task's frozen `ObjectiveScope`, and the current Knowledge
projection. The JSON-shaped contract is deliberately scenario-neutral, so the same provider
adapter serves Starfire, Medical, and future V2 definitions.

## Shape

```text
PlanningContext
├── goal
├── current_knowledge
├── relevant_actions[]
├── relevant_actors[]
├── relevant_targets[]
├── previous_execution_context
└── scenario_planning_hints
```

Each entity is described once. The provider is asked to choose Action, Actor, Target,
parameters, and ordering; the backend does not pre-bind every Actor × Action × Target
combination for the model.

## Building the context

`PlanningContextBuilder` performs Knowledge filtering, bounded relevance retrieval, semantic
normalization, and compression. It includes:

- the exact Version and frozen objective keys in the goal projection;
- public completion requirements and visible prerequisites;
- known Nodes/Facts and observations, never hidden Truth;
- relevant Actions with public world/Knowledge effects, parameter schemas, capability and
  authority constraints, execution mode, and planning hints;
- active Actors whose allowed Actions intersect the relevant set, including public location,
  capabilities, persona, and doctrine;
- known Targets with visible facts and supported Interactions;
- previous operation/failure context for a Replan;
- author-provided planning instructions and recovery hints.

Retrieval is high-recall and bounded. Supporting or epistemic Actions such as recon, inspect,
observe, or probe may be included when their public effects and target affordances relate to the
frozen Objective. A future step does not have to be immediately executable to be a valid plan
member; dynamic access, resource, Rule, and approval checks still happen at execution time.

## Provider contract

`PlanRequest` carries `INITIAL_PLAN`, `REPLAN`, or `REPAIR`, the goal, frozen scope, context,
reason, and structured repair diagnostics. When `planning_context` is present,
`PlanRequest.provider_payload()` omits the legacy `planning_action_catalog` from the
OpenAI-compatible JSON payload. The old `Actor × Action × Target` catalog remains only as an
in-process compatibility projection for older test providers and persisted diagnostics; it is
not the canonical model interface.

The provider must return a structured `PlanProposal` with a non-empty `steps` array. Each step
uses keys supplied in the context and contains a short purpose, Action/Actor/Target keys, and
normalized parameters. It may propose a complete multi-step plan; it does not execute anything
or mutate runtime state.

Goal resolution has a related but separate provider request. Deterministic exact matching of
Objective keys, names, aliases, and examples runs first. LLM fallback, when enabled by the
Scenario and configured at the application boundary, can select only from exact-Version
Objective candidates.

## Deterministic validation

`GenericAgentService` remains authoritative after every provider call. Validation checks:

- non-empty proposals and known Action/Actor/Target bindings;
- Action parameter types/defaults and exact Version references;
- target visibility/context membership, Actor bindings, capabilities, and static authority;
- rejected-proposal signatures recorded by a player decision;
- Objective-directed relevance, public prerequisite ordering, and coverage;
- conversion to the same generic executable steps used by deterministic planning.

Current access, resource availability, Rule preflight, dynamic authority, and approval are still
execution-time concerns. A proposal that fails validation is sent back as structured diagnostics
to a bounded Repair call. Repair stays inside the same frozen ObjectiveScope, must return a full
non-empty plan, and should change only what the diagnostics require. If the bound is exhausted,
the application reports a model-plan rejection/blocked state instead of silently falling back to
another planner.

## Audit metadata and tests

Provider calls persist secret-safe audit metadata on the Task, including call type, repair
attempt, serialized context size, latency, token usage when supplied, proposal bindings, and
validator diagnostics. Raw API keys, hidden Truth, and chain-of-thought are not part of the
player response.

Deterministic tests cover context filtering, supporting Actions, Objective relevance, provider
payload shape, malformed/timeout behavior, Repair constraints, and Starfire/Medical reuse. CI
uses mock/Fake providers; real-model checks are bounded manual/evaluation runs rather than a
claim of a large benchmark.
