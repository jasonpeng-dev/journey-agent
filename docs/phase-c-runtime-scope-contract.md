# Phase C0 — Runtime Scope Contract

Status: frozen at the `feature/scenario-system` checkpoint.

## Runtime identity

The runtime ownership chain is:

```text
Player → GameInstance → ScenarioVersion
```

`RuntimeScope` is the immutable domain contract for this chain. It requires:

- `game_instance_id`
- `player_id`
- `scenario_version_id`

`GameInstanceContext` is an application-facing alias for the same contract.
The three identifiers are typed UUID aliases. A missing, null, zero, or
incompatible identifier is a contract error.

The ScenarioVersion is part of the scope, not a value resolved from
`scenario_key` or from the current published pointer. A runtime resolver must
fail closed when the GameInstance or its bound version cannot be loaded; it may
not fall back to the latest version.

The frozen `RuntimeScope` is intentionally not persistence. C0 defines the
boundary that C1–C5 will later apply to persistence and services.

## Ownership

| Owner | Owns |
|---|---|
| Player | Player identity and long-term account information |
| ScenarioVersion | Node, Fact, Relation, Interaction, Resource, and Objective definitions; initial-state definitions; behavior bindings |
| GameInstance | Truth, Knowledge, Access, resource balances, current location/state, AgentTask, ConversationSession, WorldOperation, Decision, and Memory |

Definitions belong to the immutable ScenarioVersion. Values produced while a
game is running belong to GameInstance, even when their shape originated in a
ScenarioVersion definition.

## Explicit non-goals

C0 does not add Scenario CRUD, Draft/Publish, ScenarioVersion persistence,
GameInstance persistence, ORM migrations, authorization, Editor behavior
loading, Objective DSL, Rule DSL, or changes to Phase B Planner behavior.

The existing A/B runtime continues to use its legacy player-scoped adapters
until the migration stages below are implemented.

## Legacy migration map

| Current assumption | Migration owner | Intended change |
|---|---|---|
| `GameService(player_id)` | C4 / C5 | Load a `RuntimeScope` from `game_instance_id`; all runtime reads and writes use the instance root |
| `AgentTask(player_id, scenario_key)` | C5 | Add `game_instance_id` and frozen `scenario_version_id`; active-task uniqueness becomes instance-scoped |
| `WorldOperation(player_id, idempotency_key)` | C5 | Add GameInstance scope; idempotency is `(game_instance_id, idempotency_key)` |
| Truth / Knowledge / Resource rooted at `player_id` | C4 / C5 | Bootstrap and migrate state into GameInstance-owned runtime tables |
| Planner / Validator / ObjectiveEvaluator via static `scenario_key` Registry | C4 / C5 | Resolve a versioned Scenario Binding from the explicit RuntimeScope |
| Player-scoped Session / Decision / Memory | C5 | Bind through GameInstance; classify any intentionally long-term Player memory separately |

No C0 contract authorizes changing these legacy paths yet.
