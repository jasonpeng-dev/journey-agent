# Scenario authoring and publishing

This is the canonical detailed guide to the Scenario Editor, Draft/Version
lifecycle, safe authoring transforms, validation, portable artifacts, and
Scenario deletion. The player Goal contract lives in
[Custom Goals and Task Compilation](custom-goals.md); planning ownership lives
in [Agent Planning V2](agent-planning-v2.md).

## 1. Lifecycle and authorities

A Scenario is a series identity with one persisted Current Draft and zero or
more immutable Published Versions:

~~~text
create Blank / Example / Clone / imported artifact
  -> Scenario series + one Current Draft
  -> browser Working Copy
  -> explicit Manual Save with expected Draft revision
  -> Validate and inspect readiness
  -> optional read-only sandbox/test
  -> Publish
  -> immutable ScenarioVersion
  -> Game creation from an exact Published Version
~~~

The browser Working Copy is not a second persisted Scenario authority. Editor
typing and structured transforms change the Working Copy first. Initialization
projection, reference analysis, completeness guidance, semantic comparison, and
restore preview are read-only operations over that Working Copy. **Save** is
the persistence authority: it sends the complete document with the expected
Current Draft revision, and the backend rejects stale writes instead of
silently overwriting another edit.

A Draft can be incomplete while it is being authored. Validate records the
current diagnostic result; it does not publish and does not make a Working Copy
persisted. Publish validates again, requires the expected revision and optional
content hash, and creates the next immutable ScenarioVersion. Existing
GameInstances remain bound to the Version they were created from.

The current authored contract is ScenarioDefinitionV3. Existing runtime and
validation services intentionally normalize V3 in memory to the
ScenarioDefinitionV2 semantic model where they still consume that model. A
normalization is not a rewrite of the authored Draft or an upgrade of an old
V2 snapshot.

## 2. ScenarioDefinitionV3 authoring vocabulary

ScenarioDefinitionV3 is the current closed, generic authoring and portable
document contract. It reuses stable world/runtime vocabulary while keeping
failure and provider policy in generic code.

The Editor exposes:

* **Metadata:** Scenario key, display name, and description.
* **World:** node types, Nodes, Node Facts, semantic Relations, Interactions,
  and global Resources.
* **Actors:** Roles, capabilities, Actor profiles, persona/doctrine, initial
  location, allowed Actions, and authority policy.
* **Actions:** parameters, Goal Required slots, required Interaction, execution
  mode, capability/authority policy, expected outcomes, planning projections,
  target contracts, and hints.
* **Rules:** structured Conditions and Effects for action-bound PREFLIGHT and
  RESOLVE behavior.
* **Objectives:** typed completion requirements, public prerequisites, aliases,
  examples, and optional subsumption metadata used by the predefined
  compatibility route.
* **Goal Resolution:** ordered Quick Targets in
  goal_resolution.quick_inputs[]. Provider availability, fallback policy, and
  the Dynamic Goal route are platform-owned.
* **Planning:** author instructions in planning.instructions[]. Legacy V2
  recovery metadata can remain readable for compatibility, but is not current
  V3 authoring authority.
* **Initialization:** starting Node and primary Actor plus the authored initial
  Truth/Knowledge and Resource Pool state.

Authors declare data and contracts. They do not add executable gameplay code,
replace the generic Rule interpreter, bypass Action validation, change
Version immutability, or bind a Scenario to a model provider.

### 2.1 Stable identity and references

Stable machine keys are the identity of authored objects. Display names and
localized labels are presentation. Changing a display name does not change a
Rule, Objective, Relation, Pool, Fact, or Knowledge reference.

Stable-key renames go through the authoring service. The service validates the
transition, updates references atomically in the candidate document, and then
persists it only when the browser explicitly saves. The reference index exposes
Used By navigation. Deletion is blocked when another object still references
the target; ordered root and nested collections use their own identity rules.

## 3. Goal Required slots and Action contracts

ActionDefinitionV2.goal_required_slots declares which Action slots the player
must identify before the Goal can be resolved. It is different from execution
parameter requiredness:

| Declaration | Question it answers |
| --- | --- |
| Goal Required slot | Does the player's WHAT remain ambiguous without this value? |
| Execution-required parameter | Must the eventual Action invocation contain a legal value? |
| Optional slot | Can the player leave the choice to the Planner while the contract remains valid? |

Missing Goal Required information produces clarification before planning. The
Resolver must not infer it from topology or choose a value on the player's
behalf. An omitted optional slot remains Planner-owned. An explicitly supplied
Actor, source, Resource, amount, Target, or other constraint is frozen in the
FormalGoalContract and remains fixed through INITIAL, REPAIR, and REPLAN.

An Action may combine required actor roles, target actor roles, planning
terminal/supporting effects, resource requirements, locality, required
Interactions, and structured PREFLIGHT/RESOLVE Rules. These are declarative
contracts for generic Closure, Planner, Validator, and Runtime; they are not a
fixed Action sequence.

## 4. Truth, Knowledge, Resources, and Derived State

### 4.1 Truth and initial Knowledge

Authors define authoritative initial Truth and initial public visibility
separately. A Node or Fact can exist in Truth while initially hidden. A Resource
Pool can exist in a Region while its inventory has not been surveyed.

Facility Node identity and Facility Fact Knowledge are separate. Communication
loss does not by itself hide a Facility Node. The initialization fields for
Region Resource Knowledge determine whether inventory is initially visible and
survey-complete; selecting a starting Region or placing an Actor there does not
implicitly reveal the inventory.

goal_addressable on a Fact or Derived State is a public semantic boundary. It
does not expose the current value. Public aliases, examples, and typed target
values describe how a player may name the schema; internal discovery/control
Facts should remain non-addressable.

### 4.2 Knowledge acquisition

Knowledge domains are separate authoring contracts:

* A resource-survey Action can reveal discoverable Resource Pools and Region
  inventory; it does not reveal hidden Facility Truth.
* An inspection Action reveals the selected Facility or Transport facts; it does
  not survey the Region inventory.
* Supported public Rule Effects can reveal Node, Fact, Relation, Region Resource,
  or Pool visibility when explicitly authored.
* A communication-recovery Action can reveal eligible Facility Facts in a
  target Region through generic locality and located_in relations; it does not
  reveal a Region inventory.

Reveal operations publish current Runtime state. They do not copy the
Scenario's initial values into Runtime.

### 4.3 Resource Pool contract

Each initialization.resource_pools entry has a stable pool_key and resource_key,
optional Region/source and Facility associations, quantity, reserved_value,
visibility, availability, survey_discoverable, and optional
availability_requirement. Reserved quantity is Truth but is not free quantity
for consumption or transport.

An availability requirement is static dependency metadata. It does not
automatically synchronize Pool availability when its Fact becomes true. An
Action or Rule must explicitly emit the supported resource-pool availability
Effect. Validation checks references and Effect vocabulary; it does not prove
that every unavailable Pool has a reachable unlock producer.

### 4.4 Objective and Derived State requirements

Objective completion requirements support FACT, RESOURCE_AT_LEAST, and
DERIVED_STATE. A requirement may include a knowledge_gate with node_key,
fact_key, and accepted_values. The requirement belongs to the Objective and
Formal Goal before it is publicly revealed; the gate only controls when the
Planner/Player projection may expose it. It does not become a Truth
prerequisite, create a new Objective, or change the frozen contract.

A Derived State is a computed capability with its own semantic identity and a
validated dependency graph. Dependencies may reference Facts, resource
thresholds, or another Derived State. The evaluator computes authoritative
Truth and public Knowledge values on read. Derived State is not a marker Fact,
not a persisted runtime row, and cannot be directly written by an Action or
Rule.

Use FACT for one real world condition, RESOURCE_AT_LEAST for one typed Region
inventory threshold, and DERIVED_STATE for a capability whose multiple
dependencies have their own meaningful identity. Do not create a confirmation
Action that merely observes conditions and writes a summary marker.

## 5. Formal Goals and Quick Targets

ObjectiveDefinitionV2 remains the PREDEFINED compatibility source for older or
catalog-disabled immutable Versions. Its typed requirements, aliases,
examples, and authored metadata are compiled into the frozen
FormalGoalContractV1 when a Task is created.

The current V3 authoring surface owns only ordered
goal_resolution.quick_inputs[]. A Quick Target is suggested Goal text in the
browser; choosing it fills the normal editable Goal input and does not select
an Objective identity, submit a Goal, edit the Draft, or create a Version.
Provider policy and the AD_HOC_DYNAMIC route are platform-owned.

The runtime also supports AD_HOC_DYNAMIC Goals. A Knowledge-safe public
ontology can yield typed FACT, RESOURCE_AT_LEAST, or public DERIVED_STATE
requirements with implicit AND semantics. The backend validates keys and values
against the exact Published ScenarioVersion and assigns canonical identities.
The Dynamic path cannot invent hidden completion semantics or a knowledge_gate.

See [Custom Goals and Task Compilation](custom-goals.md) for the player-facing
WHAT/HOW boundary and [Agent Planning V2](agent-planning-v2.md) for Closure.

## 6. Editor behavior and Working Copy

The browser Editor includes Overview, World, Actors, Actions, Rules, Derived
States, Quick Targets, Planning Instructions, Initial State, Configuration
Check, and Validation sections. The exact navigation taxonomy is generic and
is driven by the current document structure.

The structured editor writes the same Working Copy document used by every
section. It does not create a second client-side source of truth. Adding an
ordered Quick Target or planning instruction appends a value to the Working
Copy, selects it, and leaves persistence to Manual Save. Discard restores the
last loaded persisted Draft.

Working Copy endpoints are read-only guidance unless explicitly named as a
Draft write:

* reference-analysis returns Used By information for the supplied Working Copy;
* completeness returns authoring guidance while preserving unrelated temporary
  schema issues;
* transform applies a safe rename/delete operation to a candidate Working Copy
  and returns the transformed document;
* initialization-preview projects initial state and can return a partial
  read-only projection for a focused or incomplete Working Copy;
* semantic-diff compares the Working Copy with the current Published Version.

The browser's dirty state is therefore meaningful: navigating after a restore
preview or an edit carries an unsaved Working Copy, and Save remains required.

## 7. Validation and readiness

POST /api/v1/scenarios/{scenario_id}/draft/validate validates the persisted
Draft at the expected revision. The parser accepts current V3 and historical
V2 documents; the validator exposes a normalized runtime definition while
retaining the authored document unchanged.

The response reports diagnostic path, code, severity, type, and authoring
locator where available, plus four readiness levels:

1. **STRUCTURALLY_VALID** — the document parses under a supported authored
   schema and engine contract.
2. **MINIMUM_RUNNABLE** — a structurally valid definition is available to the
   generic runtime.
3. **MINIMUM_PLAYABLE** — the readiness rules find a public Action projection
   that advances a Goal requirement.
4. **PUBLISH_READY** — no blocking ERROR remains.

Warnings, such as an Action without a RESOLVE Rule, are visible but do not block
publication. Schema/reference errors, unsupported engine contracts, missing
Actions, and minimum-playability failures are blocking. Validation does not
prove that every possible world state is solvable, discover every deadlock,
synthesize producers or Pool unlocks, construct routes, or repair the authored
document.

## 8. Draft sandbox

The Validation section can submit the current saved revision to
POST /api/v1/scenarios/{scenario_id}/draft/sandbox. The service validates the
Draft and, when valid, runs generic services in a disposable in-memory
database. It may try one supplied Goal.

The sandbox is isolated from formal Game lifecycle. It does not create a
persistent GameInstance, mutate the Draft or Published Versions, change
another Game, or establish its own recovery/history authority.

## 9. Version history and restore preview

Version History reads immutable snapshots. Selecting a historical Version calls:

~~~text
POST /api/v1/scenarios/{scenario_id}/versions/{version_id}/restore-preview
~~~

The backend checks the current persisted Draft revision, parses the historical
snapshot through the versioned authoring boundary, builds a canonical candidate
document, and returns a semantic diff/readiness handoff. This endpoint is
read-only. It does not replace or save the Current Draft.

The browser may load the candidate into the Editor Working Copy. The result is
marked dirty when it differs from the persisted Draft. The author must inspect
the candidate and press Manual Save; only then does the Current Draft revision
advance. Publishing remains a separate explicit action.

Historical V2 snapshots may be previewed only when the current migration and
authoring compatibility path can represent them safely. A conversion failure is
reported as unsupported; the UI does not silently write a different schema.

Historical restore and external artifact import are different operations:
restore is an immutable Version to the same Scenario's Working Copy; import is
an external artifact to a new Scenario identity.

## 10. Portable Scenario artifacts

The portable envelope is a deterministic UTF-8 JSON document with artifact
type/version, content_type draft or release, schema_version 3, Scenario
metadata, content hash, and canonical ScenarioDefinitionV3 definition.

### 10.1 Import

The Web Import dialog and the CLI both delegate to the same portability
service. The Web client reads a selected browser file and sends JSON to the
artifact preview/import endpoints. The CLI reads and writes local files and
never asks the HTTP server to create a local export file.

Import is always new-Scenario-only:

1. Parse and validate the envelope and current V3 definition.
2. Choose the artifact key by default, or apply an explicit target key.
3. Preview the target identity, conflict, readiness, and records to create.
4. Reject an existing target key; there is no merge, overwrite, or automatic
   suffix.
5. Commit the new Scenario and Draft in one transaction.

Draft import creates Scenario plus editable Draft and no Published Version or
Game. Release import also validates the current publication gate and creates
Published Version v1. Source lifecycle IDs, version numbers, timestamps,
history, validation cache, and Games are never migrated.

### 10.2 Export

Editor export is the persisted Current Draft only. If the browser Working Copy
is dirty, the Editor asks the author to save first; unsaved browser data is
never represented as a persisted artifact.

Scenario Detail can export the latest Published Version or a specifically
selected historical Version. Only schema v3 Versions are portable. A legacy
schema v2 Version is presented as unsupported for current artifact export; it
is not silently converted to another Version. The exact Version and canonical
authored definition are selected by the backend before serialization.

CLI commands are:

~~~text
journey scenario validate-file path/to/file.scenario.json
journey scenario import path/to/file.scenario.json
journey scenario import path/to/file.scenario.json --target-key new_key
journey scenario export scenario_key --draft --output destination.scenario.json
journey scenario export scenario_key --version 3 --output version.scenario.json
~~~

The export command accepts a Version number or UUID. It refuses silent file
overwrite unless --force is supplied. Web and CLI use the same codec and
service; only their filesystem boundary differs.

## 11. Scenario deletion

Scenario Detail opens a deletion-impact check before deletion. The check
counts the Current Draft, Published Versions, and every GameInstance dependent
on any ScenarioVersion in the series.

If a dependent Game exists, deletion is blocked with a typed conflict and the
UI lists navigation targets for those Games. Authors must delete dependent
Games explicitly first. A Scenario delete never silently deletes a Game.

When no Game dependency remains, deletion removes Scenario-owned presentation
rows, Current Draft, Published Versions, and the Scenario identity in one
transaction. The service clears the current-published pointer before deleting
Version rows so foreign-key behavior is deterministic. A failure rolls back
the operation.

## 12. Authoring API map

| Capability | Endpoint |
| --- | --- |
| List/detail/archive Scenario | GET /api/v1/scenarios, GET /api/v1/scenarios/{id}, POST /api/v1/scenarios/{id}/archive |
| Create Blank/Example/Clone | POST /api/v1/scenarios |
| Read/replace Current Draft | GET/PUT /api/v1/scenarios/{id}/draft |
| Validate | POST /api/v1/scenarios/{id}/draft/validate |
| Initialization preview | POST /api/v1/scenarios/{id}/draft/initialization-preview |
| Semantic diff | POST /api/v1/scenarios/{id}/draft/semantic-diff |
| Sandbox | POST /api/v1/scenarios/{id}/draft/sandbox |
| Working Copy analysis | POST /api/v1/scenarios/{id}/draft/reference-analysis, /draft/completeness, /draft/transform |
| Safe persisted transforms | POST /api/v1/scenarios/{id}/draft/rename-key, /draft/delete-object |
| References | GET /api/v1/scenarios/{id}/draft/references |
| Presentation profile | GET/PUT /api/v1/scenarios/{id}/presentation, GET /presentation/revision, GET /presentation/revisions, POST /presentation/restore |
| Publish | POST /api/v1/scenarios/{id}/draft/publish |
| Version snapshots | GET /api/v1/scenarios/{id}/versions, GET /api/v1/scenarios/{id}/versions/{version_id} |
| Restore preview | POST /api/v1/scenarios/{id}/versions/{version_id}/restore-preview |
| Deletion impact/delete | GET /api/v1/scenarios/{id}/deletion-impact, DELETE /api/v1/scenarios/{id} |
| Artifact preview/import | POST /api/v1/scenarios/artifacts/preview, POST /api/v1/scenarios/artifacts/import |
| Artifact export | GET /api/v1/scenarios/{id}/draft/artifact, /latest/artifact, /versions/{version_id}/artifact |
| Explicit examples/schema | GET /api/v1/scenario-examples, GET /api/v1/scenario-definition-schema |

The HTTP layer adapts DTOs and errors. ScenarioService owns Draft revision,
safe transforms, validation, publication, restore candidate construction, and
deletion. ScenarioPortabilityService owns artifact parsing, target identity,
import transactions, and export selection.
