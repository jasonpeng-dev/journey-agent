# GameInstance lifecycle

This document is the canonical detailed contract for GameInstance lifecycle
behavior. It describes the current Archive, Checkpoint, and Fork services,
their stable-point requirements, materialized state, provenance, and player
presentation.

## 1. Identity and exact ScenarioVersion binding

A GameInstance is an independent runtime identity owned by a Player. It binds
to one exact published ScenarioVersion through scenario_version_id. The
runtime never rebinds an existing GameInstance to a mutable Draft, a Scenario
pointer, or a different published Version.

Every lifecycle-created target receives a new GameInstance identity and keeps
the source player's ownership. The source and target have separate
instance-scoped Truth, Knowledge, runtime rows, and formal history rows after
materialization.

## 1.1 Goal freeze and Formal PLAY

Submitting a Goal to an ACTIVE GameInstance creates at most one non-terminal
AgentTask for that instance. A catalog-enabled Version resolves player text
through its public World Goal State catalog and compiles an
`AD_HOC_DYNAMIC` `FormalGoalContractV1`; a catalog-disabled or older immutable
Version may use the authored `PREDEFINED` compatibility route. The frozen
contract stores its canonical JSON, hash, source kind, compiler version, and
exact ScenarioVersion/content-hash proof inside the AgentTask. Preset Goal
entries in the UI only fill the same editable text input and do not submit an
Objective or Task identity.

The V1 contract is a flat implicit `AND` of `FACT`, `RESOURCE_AT_LEAST`, and
public `DERIVED_STATE` requirements. Its deterministic Truth evaluator owns
completion. Derived State values are recomputed from the exact ScenarioVersion
and instance Runtime/Knowledge state; they are not directly writable runtime
rows. A predefined requirement may be hidden behind an authored
`knowledge_gate`; the requirement is already in the frozen contract, while the
GameInstance Knowledge projection controls when it is visible to the Agent and
Player. A Dynamic Goal cannot add a gate or hidden completion semantic, and it
does not create a Scenario ObjectiveDefinition.

Authoritative Truth satisfaction is not the same as player-visible completion.
The completion evaluator keeps both results. If a Dynamic Goal requirement is
still Knowledge `UNKNOWN`, satisfying it in hidden Truth cannot immediately
publish `SUCCEEDED` to the Player; a legal public Knowledge projection must
make the requirement confirmable first. This preserves deterministic Truth
evaluation without turning hidden state into a completion oracle.

Derived State follows the same separation: its authoritative value is computed
from complete Truth, while its player/Agent value is computed from public
Knowledge. Derived values are not persisted runtime rows and do not create an
extra runtime revision. Actions and Rules mutate Base Runtime state; the
capability is recomputed after those mutations. Checkpoint and Fork copy the
Base Runtime/Knowledge state and recompute Derived values under the exact
ScenarioVersion.

`ObjectiveScope` remains a predefined/legacy compatibility projection. Dynamic
Tasks do not have an authored ObjectiveScope. Neither planning, REPAIR, nor
REPLAN may expand the frozen Formal Goal; REPAIR is pre-execution proposal
correction, while REPLAN is post-execution planning from the new public
Runtime/Knowledge projection. There is no persisted WorkingGoal or Milestone
lifecycle in the current product.

## 2. States and the stable gate

The lifecycle uses PENDING_INITIALIZATION during creation, ACTIVE for a
playable instance, and ARCHIVED for an immutable read-only instance.

An ACTIVE source can cross the stable gate only when:

* the caller supplies the current expected_runtime_revision;
* no AgentTask is non-terminal;
* no WorldOperation is pending;
* no ActionDecisionRequest is pending;
* no resource reservation remains active.

Ordinary Archive changes the same GameInstance from ACTIVE to ARCHIVED,
increments runtime_revision, and retains its runtime rows in read-only form.
It does not create a second snapshot. An ARCHIVED GameInstance cannot receive
normal runtime mutations or be used as an active play target.

The archive, Checkpoint, and Fork services verify the revision and stable gate
under the GameInstance root lock. A stale expected_runtime_revision fails with
a conflict rather than overwriting a newer runtime state.

## 3. Checkpoint

A Checkpoint is an immutable archived snapshot:

    ACTIVE A
        -> independent ARCHIVED B
        -> A remains unchanged

The Checkpoint service requires A to be ACTIVE, stable, and at the supplied
expected_runtime_revision. B binds to the same exact ScenarioVersion and
materializes the runtime state and formal history visible at A's source
revision.

B records:

* checkpointed_from_game_instance_id = A;
* checkpoint_source_runtime_revision = A's source revision;
* forked_from_game_instance_id = null;
* status = ARCHIVED;
* runtime_revision = 1.

Checkpoint uses inherited_task_count = 0 because B is an archived snapshot,
not a new playable branch. The copied tasks and their formal history remain
visible in B. If B is later Forked, the new playable target establishes its
inherited-history boundary from B's complete copied task history.

A Checkpoint is idempotent by player and creation key for the same source,
ScenarioVersion, and source runtime revision. A retry returns the existing
materialized target. Reusing the key for another source or Version is
rejected.

## 4. Fork

A Fork creates a new playable runtime from an immutable archive:

    ARCHIVED B
        -> independent ACTIVE C
        -> same exact ScenarioVersion
        -> copied runtime state and inherited formal history

The Fork service requires B to be ARCHIVED and stable. C receives a new
GameInstance identity, binds to B's exact ScenarioVersion, materializes B's
runtime state, copies B's formal history, and starts new runtime work
independently.

C records:

* forked_from_game_instance_id = B;
* checkpointed_from_game_instance_id = null;
* checkpoint_source_runtime_revision = null;
* status = ACTIVE;
* runtime_revision = 1;
* inherited_task_count = the number of copied AgentTask rows.

The inherited history includes the copied ConversationSession, AgentTask,
PlanningCycle, PlanningAttempt, AgentPlan, AgentStep, WorldOperation,
ActionDecisionRequest, and PlayerExecutionCheckpoint rows that belong to the
stable source history. Copied rows receive target-scoped identities and do not
share mutable runtime ownership with B. New tasks, plans, steps, and world
operations created in C continue independently after the inherited history.

Fork is idempotent by player and creation key for the same source and exact
ScenarioVersion. A retry returns the existing target; reuse against another
source or Version is rejected.

## 5. Runtime state materialization

The materializer validates that the source runtime matches its exact
ScenarioVersion before copying it. It materializes:

* node status, visibility, and current-node relationships;
* instance Fact truth and visibility;
* resource values, availability, visibility, and Resource Pool metadata;
* region and relation Knowledge;
* Actor dynamic state and locations.

It also copies the stable AgentTask Formal Goal contract fields and the
`PlanningCycle.formal_goal_contract_hash` linkage together with the existing
formal history. Contract JSON/hash/source/version proof stays identical after
materialization; the target receives new instance-scoped row identities.
Derived State values are recomputed from the copied state rather than copied
as an independent authority. The copied GameInstance Knowledge state
determines which previously hidden
authored requirements are visible after the Fork, while the contract itself is
not rewritten.

Reservations are not carried as active reservations into the target. Static
resource and Actor metadata is recomputed from the exact ScenarioVersion, and
the target receives its own instance-scoped rows. Materialization fails closed
when the source runtime, ScenarioVersion, or stable history cannot be copied
without losing contract semantics.

## 6. Formal history and the UI boundary

Fork copies stable formal history so the target's PLAY view can show the
inherited Tasks, Plans, Steps, and WorldOperations before the target's new
work. Task sequence and related history remain coherent in the target.

inherited_task_count is the persistence field that tells the presentation
layer how many leading tasks are inherited. The UI derives the inherited
history boundary or divider from that count. There is no separate
history_divider persistence field.

The boundary is presentation metadata, not a second runtime authority. New
runtime work after the boundary belongs only to the target GameInstance.

Formal Goal is likewise not a presentation hierarchy: MissionRoadmap and
Player responses project its currently visible typed requirements and stable
identities. They do not persist Milestones or turn display stages into child
Goals.
Checkpoint history is visible in the archived snapshot, while the subsequent
Fork marks all copied history as inherited for the new ACTIVE target.

## 7. Nested lifecycle operations

Lifecycle operations can be nested:

* an ACTIVE Fork target can create an ARCHIVED Checkpoint;
* that Checkpoint can be Forked into another independent ACTIVE target;
* the same pattern can continue for later stable revisions.

Each target preserves the exact ScenarioVersion binding and receives its own
provenance field for the immediate source operation. Checkpoint provenance and
Fork provenance are not conflated.

Deleting a source does not delete a successfully materialized target. The
source relationship uses nullable provenance semantics; deletion detaches the
provenance link while leaving the target runtime and history intact.

## 8. Transactions, locking, and API surface

Archive, Checkpoint, and Fork operations establish the GameInstance root lock
before inspecting or mutating dependent runtime rows. Materialization and
history copying occur in one transaction boundary. The lock order is
root-first; no lifecycle path acquires a dependent runtime lock and then
attempts to acquire its root.

The Player API exposes:

* POST /api/v1/games/{game_instance_id}/archive
* POST /api/v1/games/{game_instance_id}/checkpoint
* POST /api/v1/games/{game_instance_id}/fork

Archive and Checkpoint produce ARCHIVED read-only instances. Fork produces an
ACTIVE instance that can continue Formal PLAY with its inherited history and
new independent runtime state.
