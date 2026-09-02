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
- **Planning:** Dynamic Goal/clarification metadata and recovery hints.
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

`FactDefinitionV2.goal_addressable` is an authored semantic boundary and
defaults to `false`. It says whether the Fact schema is a public semantic that
players may express as a Dynamic Goal; it is independent of both authoritative
Truth and current Fact Knowledge. A public entity may therefore expose a
goal-addressable Fact schema while its current value remains `UNKNOWN`. The
schema can be included in Dynamic Goal ontology without revealing that value.
Internal, control, and discovery Facts such as requirement-discovery markers
should keep `goal_addressable=false`.

For a goal-addressable Fact, `goal_aliases`, `goal_examples`, and optional
`goal_target_values` are public semantic metadata. They describe how a player
may name the schema and which lossless typed target values may be matched;
they never publish the Fact's current value. The current Linjiang
`central_telecom_hub.operational` Fact uses this boundary for its public
"restore communications" Goal text while its initial Truth remains hidden.

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
- `RESOURCE_AT_LEAST`: `region_key`, `resource_key`, and non-negative `minimum`;
- `DERIVED_STATE`: a public authored `derived_key` and typed `accepted_values`.

A requirement can also declare `knowledge_gate` with `node_key`, `fact_key`, and
`accepted_values`. The requirement formally belongs to the Objective before it becomes public.
Until the gate is Known and satisfied it must not enter Agent/Player Knowledge. After a legal
reveal it remains part of the same frozen ObjectiveScope; no new Objective is created, and
completion remains deterministic.

All cross-entity references use stable machine keys. Display and localized names are presentation,
not identity; editing a display name must not change Rule, Objective, Relation, Pool, or Knowledge
references.

### Derived World State / capability

`derived_states` contains authored `DerivedStateDefinitionV2` capability
schemas. Each definition has a typed available/unavailable value and a
validated dependency list whose entries may reference Facts, resource
thresholds, or another Derived State. The evaluator computes the capability
from the current Runtime Truth and separately from public Knowledge; Actions
may change the underlying dependencies but never directly set a Derived State.
The dependency graph is Scenario semantics, not a Goal AST or provider-authored
formula.

`DerivedStateDefinitionV2.goal_addressable` is `false` by default. Only a
public, goal-addressable Derived State schema is included in the Dynamic Goal
catalog. Its public identity and type may be exposed while its current value,
Truth, and dependency details remain hidden. A `DERIVED_STATE` Objective
requirement points to the authored capability and is evaluated through the
same deterministic completion path.

### Marker rule

Do not add a confirmation Action that merely observes satisfied conditions and
sets a summary marker Fact for Objective completion. If the final state is a
deterministic summary of real world conditions, author a Derived State. If an
existing Fact or Resource predicate already expresses the single obligation,
use that primitive directly instead of adding a Derived wrapper.

The World Goal State vocabulary is intentionally explicit:

- `FACT` is one real authored Fact condition;
- `RESOURCE_AT_LEAST` is one typed Region/resource threshold; and
- `DERIVED_STATE` is a computed capability with multiple meaningful
  dependencies and its own stable semantic identity.

Do not wrap a single real Fact in a Derived State merely to give an Objective a
marker. The current canonical Linjiang authoring is:

| Objective | Completion form |
| --- | --- |
| Task1 `restore_central_communication_capability` | `FACT`: `central_telecom_hub.operational == true` |
| Task2 `restore_north_basic_engineering_support` | `DERIVED_STATE`: `north_basic_engineering_support` |
| Task3 `restore_east_emergency_power_network` | `DERIVED_STATE`: `east_emergency_power_network` |
| Task4 `restore_east_emergency_water_supply` | `DERIVED_STATE`: `east_emergency_water_supply` |
| Task5 `establish_citywide_sustained_emergency_support` | `DERIVED_STATE`: `citywide_sustained_emergency_support` |
| Task6 `establish_sustained_emergency_generation` | `DERIVED_STATE`: `southeast_sustained_emergency_generation` |

Thus the current Version has five goal-addressable Derived States. Derived
values are computed on read from the immutable ScenarioVersion and Runtime
Truth or public Knowledge. They are not persisted runtime rows, do not create
an extra `runtime_revision`, and cannot be directly written by an Action or
Rule. Checkpoint/Fork copies Base Runtime and Knowledge state, then recomputes
Derived values in the target.

Task5's `citywide_sustained_emergency_support` is an implicit-AND capability
over the real base contracts: `rail_freight_yard.rail_freight_capability`,
`emergency_supply_warehouse.operational`, `vehicle_depot.emergency_delivery_support`,
`city_distribution_center.operational`, six transport `passable` Facts, and
three typed `RESOURCE_AT_LEAST` requirements for 30 emergency-relief supplies
in Central, East, and Southeast. The rail-freight and emergency-delivery
Facts remain Base Facts because Actions consume them as independent downstream
preconditions, even though the current repair Action sets each alongside its
facility's `operational` Fact. No final activate/establish marker Action is
used, and resource inventory is not converted into a persistent marker Fact.

Task6 keeps `generate_power` as the real discovery Action. Its successful
reveal of `sustained_requirements_discovered` exposes the gated sustained
generation dependencies to public planning and causes REPLAN. The frozen Goal
contract is unchanged; Knowledge gates control exposure, not Truth
satisfaction. `commission_sustained_generation` is not part of the current
canonical Scenario.

## Formal Goals and Dynamic Goals

An authored `ObjectiveDefinitionV2` remains a `PREDEFINED` compatibility
source when the exact Version uses the legacy Objective route. Its typed
completion requirements, authored prerequisites, aliases, and planning
metadata are compiled deterministically into the frozen
`FormalGoalContractV1` when a Task is created. The contract is bound to the
exact immutable ScenarioVersion; later Draft edits or publication do not alter
an existing Task. A Version may set
`goal_resolution.world_goal_state_catalog=true`; in that mode its authored
Objective rows can remain for compatibility and preset text, but do not
shortcut current player Goal routing.

The current runtime also accepts an `AD_HOC_DYNAMIC` Goal. The Dynamic Goal
interpreter receives a Knowledge-safe public ontology and may return one or
more `FACT`, `RESOURCE_AT_LEAST`, or public `DERIVED_STATE` candidates with
implicit `AND` semantics.
The backend validates those candidates against the exact ScenarioVersion,
assigns their stable semantic identities, and compiles the same typed contract
used by predefined Objectives. A dynamic submission does not create an
ObjectiveDefinition, alter the Scenario Draft/Version, or add a new Scenario
objective key.

The Dynamic ontology is built from currently public entity identities plus
goal-addressable Fact schemas (and public Resource/Region semantic metadata),
not only from rows whose current Fact value is Known. It includes names,
descriptions, types, aliases/examples, and allowed public domains, but not
current Truth values, hidden Fact values, hidden resource source/quantity, or
internal discovery metadata. `KNOWN public entity + goal_addressable Fact
schema + UNKNOWN current value` is therefore a valid Goal boundary.

Dynamic interpretation cannot see hidden Truth, hidden Facts, authored
Objective definitions, Actions, prerequisites, knowledge gates, hidden
completion semantics, or non-public Derived State dependencies. It cannot
invent ontology or attach a requirement `knowledge_gate`. If a player Goal
needs authored hidden semantics, it must resolve to a predefined source or a
future deterministic template source; it cannot be supplied by the interpreter
itself. V1 has no parameterized template source, Goal AST, `OR`, generic
`NOT`, Actor Goal, Milestone, or WorkingGoal.

`FormalGoalContractV1` is a Task/runtime contract, not a replacement for
Scenario authoring. Its flat requirement tuple is an implicit conjunction and
its canonical hash excludes display-only descriptions. Requirement Knowledge
and planning availability are runtime projections: revealing a gated authored
requirement makes it public for Planner/Player projection without changing the
frozen Goal or the authored Scenario.

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
