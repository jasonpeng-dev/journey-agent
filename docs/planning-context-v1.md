# PlanningContext V1

`PlanningContext V1` is the canonical provider input for Generic Agent
planning. It is built from the exact `ScenarioVersion`, frozen `ObjectiveScope`,
and the current Knowledge projection. It is deliberately a JSON-shaped contract
so the same provider adapter works for every `ScenarioDefinitionV2`.

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

Actions, Actors, and Targets are each serialized once. Provider steps reference
their keys directly:

```json
{
  "plan_summary": "short strategy summary",
  "steps": [
    {
      "purpose": "goal-directed purpose",
      "action_key": "authored_action_key",
      "actor_key": "versioned_actor_key",
      "target_key": "known_target_key",
      "parameters": {},
      "short_actor_reason": "short rationale"
    }
  ]
}
```

`PlanningContextBuilder` performs only four operations: Knowledge filtering,
bounded high-recall relevance retrieval (three public dependency hops),
semantic normalization, and context compression. It never binds an Actor to an
Action/Target, selects a route, or orders steps. Known locked Targets remain
eligible for future steps; hidden Nodes, Facts, Relations, and Effects are
excluded.

The provider chooses decomposition, Action, Actor, Target, parameters, and
ordering. The backend validator checks only hard static constraints, exact
Version/entity references, canonical parameters, static authority, rejected
proposal signatures, and structural ObjectiveScope relevance/coverage. Current
access, resources, Rules, dynamic approval, and mutation remain authoritative in
the existing Generic runtime at execution time.

The former Actor × Action × Target Candidate Catalog remains only as a migration
compatibility projection for old in-process FakeProviders. It is not included in
the canonical OpenAI-compatible payload and is not an authoritative planning
input.
