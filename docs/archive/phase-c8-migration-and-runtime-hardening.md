# Phase C8 — Migration and Runtime Hardening

## Upgrade contract

`c80000000001` is the one-way compatibility boundary between the legacy
`player_id ≈ game` model and the Phase C runtime ownership model.

The migration:

1. Materializes the canonical built-in Starfire definition as an immutable
   `ScenarioVersion`, resolved by canonical content hash rather than by the
   mutable current/latest pointer.
2. Creates one deterministic `legacy-default` `GameInstance` for every legacy
   Player and permanently binds it to that exact version.
3. Copies legacy node, fact, resource, world-fact, officer, session, task,
   memory, operation, and decision state into the Instance-owned graph.
4. Verifies Player/Instance and parent ownership consistency before changing
   constraints.
5. Makes every direct runtime root's `game_instance_id` and every
   `GameInstance.creation_key` non-null, then removes player-scoped operation
   idempotency from the hardened schema.

Migration aborts rather than guessing if the graph cannot be assigned without
crossing an Instance boundary. It never binds a legacy runtime to whichever
version happens to be current at upgrade time.

## Compatibility boundary

The application retains a narrow compatibility path for a physically nullable
pre-C8 database. This supports historical migration tests and rolling an old
database through the migration chain. The path is enabled only after inspecting
the active schema through the current transaction. On a C8 schema, missing
Instance ownership is rejected by both the database and runtime binding code.

The strategic Debug Console and real-provider evaluation now create an explicit
Player, exact ScenarioVersion, GameInstance, initialized Runtime, and Session.
Their API responses expose both `game_instance_id` and `scenario_version_id`.

## Source-of-truth rules after C8

- Objective definitions, requirements, prerequisites, and subsumption come only
  from the persisted ScenarioVersion snapshot.
- The exact BehaviorBundle supplies executable planning/evaluation behavior but
  does not own an Objective Catalog.
- Runtime lookup starts from GameInstance. `player_id` and `scenario_key` remain
  denormalized audit/compatibility fields, never sufficient runtime identities.
- ScenarioVersion lookup is exact-ID only. No runtime latest-version fallback
  exists.
- Message, run, plan, step, and tool execution ownership remains derived through
  the already verified Session/Task parent chain; no duplicate scope columns are
  introduced.

## Recovery

All production recovery uses `RuntimeRecoveryService.recover(game_instance_id)`.
It reloads the immutable snapshot and exact BehaviorBundle and rejects incomplete
or cross-Instance state. Legacy rows become recoverable through the same path
immediately after migration; there is no separate player-based recovery path.
