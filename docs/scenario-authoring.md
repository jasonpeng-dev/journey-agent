# Scenario authoring and publishing

This is the maintained guide to the Scenario Editor and its persistence contract.

## Lifecycle

```text
create Blank / Example / Clone
  -> one mutable Current Draft
  -> edit and autosave with revision checks
  -> validate and inspect readiness diagnostics
  -> optional isolated Draft Preview/Test
  -> publish a new immutable ScenarioVersion
  -> start formal Games from an exact published Version
```

The Draft is an editable JSON document and may be incomplete while it is being authored. The
strict `ScenarioDefinitionV2` model is constructed during validation, sandbox startup, and
publication. A Scenario has exactly one Current Draft; saving a Draft increments its revision
and uses optimistic concurrency. A stale write is rejected rather than silently overwriting
another edit.

## Definition vocabulary

`ScenarioDefinitionV2` is a closed, generic data contract. The Editor exposes these groups:

- **Metadata:** scenario key, display name, and description.
- **World:** node types, nodes, node Facts, semantic Relations, Interactions, and global
  Resources.
- **Actors:** Roles, capabilities, actor profiles, persona/doctrine, initial location, allowed
  Actions, and authority policy.
- **Actions:** parameters, required Interaction, execution mode, actor capability/authority
  policy, expected outcomes, planning projections, and hints.
- **Rules:** action-bound preflight/resolve behavior expressed as structured Conditions and
  Effects.
- **Objectives:** completion `ObjectiveRequirementV2` values, public prerequisites, aliases,
  examples, and optional subsumption metadata.
- **Planning:** goal-resolution fallback/clarification metadata and recovery hints.
- **Initialization:** starting Node and primary Actor.

Authors can define content and vocabulary, but cannot add executable code, edit the generic Rule
interpreter, bypass Action validation, change Version immutability, or bind a scenario to a
specific model provider.

## Editor behavior

The browser Editor has Overview, World, Actors, Actions, Rules, Objectives, Planning,
Initial State, and Validation sections. Objects have stable keys and display names. Display-name
edits do not change references. Stable-key renames go through the authoring service and update
the Draft references atomically. The reference index provides Used By navigation, and deletion
is blocked when another object still references the target.

The World view starts with a simple node/relation graph. Facts and detailed interactions/rules
are inspected when their owning object is selected rather than being rendered as a dense graph.
The structured editor writes back to the same Draft document used by every section; it does not
create a second client-side source of truth.

## Validation and readiness

`POST /api/v1/scenarios/{scenario_id}/draft/validate` reports object paths, diagnostic codes,
severity, and four readiness levels:

1. `STRUCTURALLY_VALID` — the document parses as the supported v2 schema and engine contract.
2. `MINIMUM_RUNNABLE` — the strict definition is available to the runtime.
3. `MINIMUM_PLAYABLE` — the definition has Actions/Rules that project an Objective requirement.
4. `PUBLISH_READY` — no blocking `ERROR` remains.

Warnings such as an Action without a resolve Rule are visible but do not block publication.
Schema/reference errors, missing Objectives or Actions, unsupported engine contracts, and the
minimum-playability failure are blocking. The gate is platform-owned; authors cannot override
it. The validator does not attempt to prove that every possible world state is solvable.

## Draft Preview/Test sandbox

The Validation section can submit the current saved revision to
`POST /api/v1/scenarios/{scenario_id}/draft/sandbox`. The service first constructs and validates
`ScenarioDefinitionV2`. An invalid Draft returns diagnostics and starts no runtime. A valid Draft
runs the existing generic services in a disposable in-memory database and may try one supplied
Goal.

The sandbox is physically separate from formal Game lifecycle: it does not create a persistent
formal `GameInstance` row, does not mutate the Draft or any published Version, and cannot change
another GameInstance. It has no persistence, recovery, or history of its own.

## Versions and Games

Publishing requires the expected Draft revision and (when supplied) the validated content hash.
It creates a new immutable Version; publishing an unchanged semantic document is rejected with
`SCENARIO_PUBLISH_NO_CHANGES`. Previous snapshots remain unchanged.

Version History supports reading a snapshot, restoring its content into the Current Draft, and
starting a new Game from that exact Version. Restore changes only the Draft. The New Game flow
accepts a `scenario_version_id`, never a Draft or a mutable Scenario pointer. Built-in examples
are exposed by `GET /api/v1/scenario-examples` and currently include the Linjiang Infrastructure
Recovery v2.0 definition seeded by `uv run python -m app.seed`.

## Authoring API map

| Capability | Endpoint family |
| --- | --- |
| List/detail/archive Scenarios | `GET /api/v1/scenarios`, `GET /api/v1/scenarios/{id}`, `POST /api/v1/scenarios/{id}/archive` |
| Create Blank/Example/Clone | `POST /api/v1/scenarios` |
| Read/replace Current Draft | `GET/PUT /api/v1/scenarios/{id}/draft` |
| Validate and sandbox-test | `POST /api/v1/scenarios/{id}/draft/validate`, `/draft/sandbox` |
| Publish/restore | `POST /api/v1/scenarios/{id}/draft/publish`, `/draft/restore` |
| References and safe editing | `GET .../draft/references`, `POST .../draft/rename-key`, `POST .../draft/delete-object` |
| Version snapshots | `GET /api/v1/scenarios/{id}/versions`, `GET .../versions/{version_id}` |
| Built-in examples and schema | `GET /api/v1/scenario-examples`, `GET /api/v1/scenario-definition-schema` |

The HTTP layer only translates DTOs and application errors. Draft revision, reference updates,
validation, publication, and version semantics live in `ScenarioService` and the scenario
validation/persistence modules.
