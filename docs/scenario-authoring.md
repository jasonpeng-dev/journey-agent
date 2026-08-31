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

## Knowledge, Resource Pools, and Objective Requirements

### Truth and initial Knowledge

Authors define world Truth and its initial public visibility independently. A Node can exist in
Truth while initially hidden, and a Fact can have an authoritative value while that value remains
UNKNOWN to the Player and Agent. Likewise, a Resource Pool can exist in a Region whose inventory
has not been surveyed.

Facility Node identity and Facility Fact Knowledge are separate contracts. A Facility may be
authored as a Known Node while its operational or power Facts remain hidden; communication loss
does not by itself hide that Facility's existence.

`initialization.region_resource_knowledge` is the authority for initial Region inventory
visibility and survey completion. Neither choosing a starting Region nor placing an Actor there
automatically makes that Region's Resource inventory Known.

### Knowledge acquisition

The generic Knowledge channels have separate authoring meanings:

- `survey_resources` updates Region Resource Knowledge and may reveal authored, discoverable
  Resource Pools. It does not reveal hidden Facility Truth.
- `inspect` reveals the selected Facility or Transport target's non-Resource facts. It does not
  survey the Region inventory.
- public Rule Effects can reveal Node, Fact, Relation, Region Resource, or Pool visibility when
  the corresponding supported Effect is explicitly authored.
- successful communication recovery uses generic locality and `located_in` relations to reveal
  eligible Facility Facts and their current Runtime values in the target Region. It does not
  reveal Facility identity (Node visibility is authored separately) or that Region's Resource
  inventory.

One channel must not be treated as implicit permission to disclose another Knowledge domain.
Reveal operations publish current Runtime state, not a cached copy of the Scenario's initial
values.

### Resource Pool contract

Each `initialization.resource_pools` entry has a stable `pool_key` and `resource_key`, an optional
Region/source association and Facility link, `quantity`, `reserved_value`, `visibility`,
`availability`, `survey_discoverable`, and an optional `availability_requirement`. Reserved
quantity is part of total Truth but is not free quantity for consumption or transport.

`availability_requirement` describes static unlock/dependency metadata. It is visible to planning
only through the applicable public Knowledge boundary, and it is not a reactive synchronization
rule. Making its Fact true does not automatically change the Pool's Runtime availability. If a
repair or unlock Action should make the Pool `AVAILABLE`, a selected Rule must explicitly emit
`SET_RESOURCE_POOL_AVAILABILITY` or an implemented equivalent Effect.

The current validator checks the requirement's references and supported Effect vocabulary. It
does not prove that every unavailable Pool has a reachable unlock producer.

### Objective requirements

Current objective completion requirements support:

- `FACT`: `node_key`, `fact_key`, and non-empty `accepted_values`;
- `RESOURCE_AT_LEAST`: `region_key`, `resource_key`, and non-negative `minimum`.

A requirement can also declare `knowledge_gate` with `node_key`, `fact_key`, and
`accepted_values`. The requirement formally belongs to the Objective before it becomes public.
Until the gate is Known and satisfied it must not enter Agent/Player Knowledge. After a legal
reveal it remains part of the same frozen ObjectiveScope; no new Objective is created, and
completion remains deterministic.

All cross-entity references use stable machine keys. Display and localized names are presentation,
not identity; editing a display name must not change Rule, Objective, Relation, Pool, or Knowledge
references.

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
it. The validator does not attempt to prove that every possible world state or Objective is
solvable, discover every implicit deadlock, synthesize missing producers or Pool unlocks, construct
routes, or repair the authored Scenario. A broader semantic authoring linter remains future work.

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
