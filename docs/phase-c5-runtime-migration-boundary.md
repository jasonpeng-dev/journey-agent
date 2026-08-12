# Phase C5 — Runtime Scope Migration Boundary

The new runtime read/write path is rooted at an explicit `RuntimeScope`. The
legacy player-scoped path remains available only for rows whose
`game_instance_id` is null so an existing database can reach C8 safely.

## Direct GameInstance ownership

The current persistence graph has direct nullable `game_instance_id` bindings
on these runtime roots:

- `ConversationSession`
- `AgentTask`
- `Memory`
- `WorldOperation`
- `PlayerDecisionRequest`

Truth, Knowledge, Access, flat compatibility facts, Resources, and officer
assignments use new instance-native state tables. Their old player-scoped
tables remain untouched until C8 backfill and constraint hardening.

## Ownership through an existing strict parent

No duplicate Instance column is added where the existing foreign-key chain is
already unambiguous:

- `ConversationMessage → ConversationSession`
- `AgentRun → ConversationSession` and, for task work, `AgentTask`
- `AgentPlan → AgentTask`
- `AgentStep → AgentPlan → AgentTask`
- `ToolExecution → AgentRun`

There is no separate Decision, Plan-memory, or Access persistence subsystem
beyond these models. Creating placeholder tables for checklist names would
weaken rather than clarify ownership.

## Versioned runtime definition

For instance-owned work, Planner, Validator, objective resolution/evaluation,
interaction target resolution, and tools compose:

`GameInstance → exact ScenarioVersion snapshot + exact Behavior implementation`

Objective definitions, requirements, prerequisites, and subsumption are loaded
only from the persisted snapshot. The behavior implementation supplies
executable policies and contains no Objective catalog content. Missing or
mismatched Instance, Player, Version, Scenario key, or behavior binding fails
closed. No runtime lookup resolves a current/latest published version.

## C8 handoff

C8 bootstraps a legacy Starfire version, creates one default GameInstance per
legacy Player runtime, backfills the complete runtime graph, verifies parent
chains, then makes applicable direct ownership columns non-null and removes
unsafe player-only compatibility lookup.
