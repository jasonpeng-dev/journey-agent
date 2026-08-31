# Agent Planning V2

This is the canonical detailed description of the current Journey Agent
planning, validation, execution, Knowledge, repair, replan, and continuity
architecture. It describes the generic production path that is implemented by
the repository. Scenario data supplies content; it does not add
scenario-specific control flow.

## 1. Overview

The current end-to-end lifecycle is:

    Natural-language Goal
      -> Goal Resolver
      -> frozen ObjectiveScope on AgentTask
      -> bounded Dependency Closure
      -> canonical PlannerInput V2
      -> LLM Provider or deterministic provider substitute
      -> PlanSegment
      -> deterministic Validator
      -> bounded internal REPAIR when rejected
      -> formal AgentPlan and AgentStep rows
      -> Runtime Action execution
      -> Truth mutation
      -> public Knowledge projection
      -> remaining-plan validation
      -> Player acknowledgement
      -> REPLAN when required
      -> objective verification

The runtime is generic because the same source code interprets every
published ScenarioVersion through the declarative ScenarioDefinitionV2 and
declarative-rule-engine contracts. A ScenarioVersion changes data and public
semantics, not the generic planner or runtime implementation.

Formal PLAY uses one synchronous HTTP request for an INITIAL_PLAN or REPLAN.
The backend may perform the configured repair attempts inside that request.
Only the final accepted plan becomes executable; rejected attempts remain
internal audit records.

## 2. Authority boundaries

### 2.1 Truth and Knowledge

Truth is the authoritative mutable world state used by Rule evaluation and
objective verification. Knowledge is the public, visibility-filtered
projection available to the Planner, Validator, and normal Player responses.

Truth is not Knowledge. Hidden Truth never enters PlannerInput, Validator
state, repair diagnostics, or player projections.

The following distinctions are normative:

    UNKNOWN != false
    UNKNOWN != zero
    UNKNOWN != unavailable
    UNKNOWN != blocked

An UNKNOWN value can remain unknown. It must not be converted into a
deterministic contradiction by assumption.

### 2.2 Planner responsibilities

The LLM Planner chooses, from the canonical input:

* Action
* Actor
* Target
* Resource source
* parameters
* route and ordering

The Planner may compose multiple supporting Actions and multiple Actors when
the public state and Action contracts require a causal chain. It may repeat
an Action, including one-hop Travel, when that is part of its own plan.

### 2.3 Backend and Validator responsibilities

The backend provides canonical public input, deterministic contract checks,
Truth mutation, Knowledge projection, persistence, and pacing. The Validator
judges the submitted PlanSegment and explains known contradictions.

The Validator and runtime do not become a second Planner. They do not perform
pathfinding, choose a route, choose an Actor, choose a Resource source,
insert prerequisites, insert Travel or Relay, invent recovery Actions, or
return recovery recommendations. A blocked decision that requires multi-step
planning remains the Planner's responsibility.

## 3. Goal Resolution and frozen ObjectiveScope

The Goal Resolver first matches the submitted natural-language goal against
the exact published Version's Objective keys, names, aliases, and examples.
If configured fallback is enabled, a provider may select only among the
exact objective candidates supplied by the resolver. It cannot invent an
Objective.

An accepted goal creates an AgentTask with:

* the exact ScenarioVersion reference;
* a non-empty frozen ObjectiveScope;
* an ObjectiveScope hash and catalog/version metadata;
* objective completion requirements from the ScenarioVersion.

INITIAL planning, REPAIR, and REPLAN all remain inside that frozen scope.
Neither the model nor the backend may add sibling objectives, broaden the
scope, or replace a completion requirement with a model-declared one.
Completion is determined by the formal Scenario completion requirements and
projected deterministic effects, not by a free-form model assertion.

### 3.1 Objective requirement kinds and gated publication

The current objective evaluator supports both `FACT` and
`RESOURCE_AT_LEAST` completion requirements. A `RESOURCE_AT_LEAST`
requirement names a Resource, a Region, and a minimum quantity. Truth
evaluation aggregates the actual free quantity in matching Runtime Resource
Pools that are `AVAILABLE`; reserved quantity is excluded.

Public planning and player projections apply a stricter Knowledge boundary.
They aggregate only currently Known Resource Knowledge. A hidden Pool,
quantity, or source cannot contribute to public satisfaction. No row in an
incomplete or unsurveyed Region remains UNKNOWN, while a completed visible
inventory with no matching Pool can represent known zero.

A completion requirement may have a `knowledge_gate` containing
`node_key`, `fact_key`, and `accepted_values`. The requirement already belongs
to the frozen ObjectiveScope when the AgentTask is created, but it enters the
public Agent/Player projection only after that gate is Known and satisfied.
This reveal does not broaden ObjectiveScope, create a later Objective, or
delegate completion to the Provider. The deterministic evaluator remains the
completion authority.

## 4. Dependency Closure

Dependency Closure is bounded relevance filtering for the current frozen
ObjectiveScope. It starts from objective requirements and public producer
semantics, then performs bounded fixed-point expansion to retain the public
Actors, Actions, Targets, Resources, Knowledge dependencies, and transport
context that the Planner may need.

Closure is not:

* plan generation;
* pathfinding or route selection;
* source selection;
* Actor selection;
* recovery planning;
* prerequisite insertion;
* an Actor by Action by Target candidate catalog.

The closure may retain high-recall frontier context when a public dependency
is unresolved, including an UNKNOWN Resource source or transport context.
That is relevance data, not a recommendation. Closure does not use Hidden
Truth to decide which branch is correct.

Action-level deterministic effects and sparse target-specific bindings are
both valid public producers. A producer is retained only when its public
contract matches the dependency's effect, fact, value, and target semantics.
Target-specific rules are not expanded into a Cartesian catalogue.

## 5. Canonical PlannerInput V2

PlannerInput V2 is the only authoritative semantic input for current INITIAL,
REPAIR, and REPLAN provider calls. The provider serializer emits this
canonical shape when it is available:

    schema_version
    objective
    actors[]
    action_contracts[]
    target_bindings[]
    known_world
    execution_context

The canonical actor projection includes:

* actor_key and role_key;
* capabilities;
* allowed_action_keys scoped to the exposed action_contracts;
* availability;
* current_region;
* command_reachability;
* public execution_state and known blockers.

The canonical ActionContract includes:

* action_key;
* executor requirements;
* target contract;
* locality;
* parameter schema;
* known preconditions;
* deterministic public effects;
* Knowledge semantics.

Target bindings are sparse and carry only target-conditioned requirements and
deterministic effects for an Action/Target pair. They are not a precomputed
candidate list.

known_world contains the bounded public slice:

* nodes;
* known facts;
* visible relations;
* resource projections;
* region/resource Knowledge;
* unique UNKNOWN dependencies.

Resource Knowledge is public Knowledge. A visible, surveyed inventory with
no pool row is an explicit known zero. An incomplete or hidden inventory with
no row remains UNKNOWN. Reserved quantity is not available quantity.

Resource Knowledge contract:

* survey_resources can expose the identity, quantity, availability, and static
  unlock requirement of a discovered Resource Pool;
* survey_resources does not directly expose hidden Facility Truth referenced by
  an unlock requirement;
* inspect reveals the corresponding Target or Facility facts, but does not
  also discover an undiscovered Resource Pool;
* survey and inspect order must not change the final legal Knowledge state;
* Agent and Player use the same Knowledge permission boundary;
* the Agent reads relevant Known Knowledge through canonical structured
  PlannerInput; hidden Truth is never included in PlannerInput.

An authored Resource Pool `availability_requirement` is static dependency
metadata, not a reactive derived-state engine. It can expose a public unlock
condition to Closure, Planner, and Validator, but satisfying the referenced
Fact does not by itself mutate a Pool from `UNAVAILABLE` to `AVAILABLE`.
Runtime availability changes require an explicit successful Rule Effect such
as `SET_RESOURCE_POOL_AVAILABILITY`. Facility repair and linked-Pool unlock
are therefore equivalent only when the selected Rule explicitly performs
that mutation.

PlanningContext and PlanningActionCatalogBuilder remain current in-process and
binding projections used by the runtime. PlanningContextV1 has been removed.
The planning-context-only provider_payload branch has also been removed.
PlannerInput V2 is the canonical provider payload authority. candidate_id
remains supported as a compatibility response field and is resolved against
the current catalog and binding; it is not a second planning authority.

For REPLAN, planning_continuity is an additional historical sibling field.
It is not part of the canonical PlannerInput and never overrides it.

## 6. Planner reasoning guidance

The current planning prompt asks the Planner to reason from the terminal
Objective completion requirement backwards through public prerequisites. The
Planner must identify public executor, locality, Resource, Target, parameter,
command-reachability, and precondition requirements and compose legal
supporting Actions itself.

The current implementation has implicit backward prerequisite reasoning. It
does not persist a formal working-goal, milestone, or goal-dependency-graph
object. Those are future design possibilities, not current runtime fields.

The backend will not insert prerequisites, Travel, Relay, transport, recovery
Actions, or routes. When they are required, the Planner must include them in
the returned PlanSegment.

## 7. PlanSegment contract

A provider response is a PlanSegment with:

* plan_summary;
* stop_reason;
* boundary_dependency_id when the stop reason is INFORMATION_BOUNDARY;
* ordered steps.

Each step contains:

* step_id;
* purpose;
* action_key;
* actor_key;
* target_key;
* parameters;
* short_actor_reason.

purpose must describe causal progress, a public prerequisite established by
the step, Knowledge acquisition, or an explicitly necessary supporting
Action. Repeating an Action name or destination is not a sufficient purpose.

The provider proposal does not execute anything or mutate runtime state.
Only a Validator-passing proposal is converted into formal AgentPlan and
AgentStep rows.

## 8. Stop reason semantics

### OBJECTIVE_COMPLETION

Use this only when the current Known state plus the segment's projected
deterministic effects satisfy every frozen Objective completion requirement.
Partial progress is not completion. A model cannot declare completion merely
because the final Action was attempted.

### INFORMATION_BOUNDARY

Use this only when a genuinely blocking UNKNOWN dependency prevents the next
legal choice of Target, Resource source, parameter, or public precondition.
The segment must include a legal Knowledge-acquisition Action that matches
that dependency as its final step. boundary_dependency_id must identify the
same UNKNOWN dependency.

MAY_ATTEMPT transport or route uncertainty is not an information boundary.
General complexity, lack of confidence, or inability to think of a causal
chain is not an information boundary.

### BLOCKED

Use this only when the Objective is incomplete and there is neither a legal
one-step progress Action nor a legal one-step Knowledge-acquisition Action
provable from current public Known state. BLOCKED must have an empty steps
array. If any legal progress or acquisition Action exists, the segment is not
BLOCKED.

### Ordinary FACT UNKNOWN and the traversal exception

Ordinary public Fact prerequisites use tri-state semantics:

* a Known matching value satisfies the prerequisite;
* a Known conflicting value is a deterministic contradiction;
* UNKNOWN satisfies neither branch and remains a blocking public Knowledge
  dependency.

Closure may retain the relevant legal Knowledge-acquisition producers for
that dependency. The Validator must not assume that an UNKNOWN ordinary Fact
is satisfied or convert it to false.

Traversal passability is an explicit exception. Known passable is legal,
Known blocked is a contradiction, and UNKNOWN passability is `MAY_ATTEMPT`.
The attempt can fail at Runtime and reveal the route Truth. This route
uncertainty is not, by itself, an `INFORMATION_BOUNDARY`.

## 9. Sequential projected validation

The deterministic Validator validates each submitted step in order while
maintaining a lightweight projected Known state. Earlier deterministic
effects are applied before later steps are checked.

The projected state covers, as applicable:

* Actor location;
* command reachability;
* Actor availability and authority;
* Resource balance and reserved/free quantity;
* locality and target contracts;
* parameters;
* public preconditions;
* known route/passability state;
* public facts, visibility, and Knowledge effects.

Projected resolution-rule selection is performed once per step using the
selected Action and Target. Fact, Resource, Knowledge, passability, and Actor
reachability effects use that same selection. Other target-conditioned rules
for the same Action must not leak into the selected step.

The Validator rejects only a Known deterministic contradiction. UNKNOWN is
not itself a contradiction. Ambiguous possible winners may project only
effects common to every possible winner; conflicting effects remain unknown.

## 10. REPAIR

Validator rejection happens before execution and stays inside the same
planning cycle. Formal PLAY performs the repair loop synchronously inside the
single planning request:

    INITIAL_PLAN or REPLAN
      -> Validator
      -> REPAIR when rejected
      -> ... bounded attempts ...
      -> accepted AgentPlan, terminal model rejection, or provider error

The REPAIR payload contains:

* the same canonical PlannerInput;
* the rejected segment;
* the current typed validator violations;
* repair_attempt;
* same-cycle anti_regression_memory;
* frozen planning_continuity when this is a REPLAN cycle.

Current repair limits are configuration/runtime bounds. The exact value is
recorded by provider audit metadata and is not a scenario rule.

Anti-regression memory contains only earlier Validator-proven contradiction
evidence from this planning cycle. It is historical evidence, not an active
violation and not a recovery plan. It must not contain recommended Action,
Actor, Target, source, route, next step, or an old complete plan. The new
proposal is validated again from current canonical PlannerInput and projected
state.

Rejected attempts are persisted as PlanningAttempt audit rows. They do not
create AgentPlan, AgentStep, or WorldOperation and do not mutate Truth or
Knowledge.

## 11. Formal AgentPlan persistence and audit

After Validator PASS, GenericAgentService persists:

* one AgentPlan;
* ordered AgentStep rows;
* plan version, stop reason, and replan reason;
* validation status and execution metadata.

PlanningCycle and PlanningAttempt persist the planning lifecycle, exact
canonical PlannerInput/provider payload, proposal, typed violations,
anti-regression memory, usage, latency, finish reason, and outcome for
developer audit. They are not player-visible planning alternatives.

Provider raw chain-of-thought and credentials are not persisted or returned
to players. Player projections expose only accepted formal plans, safe action
results, Knowledge changes, and pacing state.

## 12. Runtime semantics

Runtime executes formal AgentStep rows through the generic Action service and
declarative Rule engine. It may:

* validate Action/Actor/Target authority and parameters;
* mutate Truth through deterministic Effects;
* create and settle a WorldOperation;
* publish Knowledge changes;
* fail deterministically;
* reveal previously hidden state through an explicit public outcome.

After every execution, the service updates Truth and public Knowledge before
remaining-plan validation. A failure or a new Knowledge change can invalidate
the remaining plan and require a player-confirmed REPLAN.

Runtime failure is distinct from Planner/Validator rejection. A hidden route
failure discovered during execution is a Runtime outcome that may add public
Knowledge and become a later REPLAN reason.

## 13. Current Action semantics

### Travel

Travel is one hop:

* source is the projected executing Actor Region;
* target is the destination Region;
* the Actor location changes;
* Resources do not move;
* an UNKNOWN route may be attempted;
* a hidden block may fail at Runtime and reveal public route Knowledge.

Multiple Travel steps may be composed by the Planner. The backend does not
perform pathfinding or insert a route.

### transport_resource

transport_resource is the Region-to-Region Resource transfer Action:

* source is the projected executing Actor Region;
* target is the destination Region;
* canonical parameters contain a non-empty `resources` cargo list whose
  entries have a unique Resource key and positive integer amount;
* legacy single-cargo `resource_key` and `amount` input is normalized to that
  canonical list;
* all requested cargo is consumed from Known, visible, `AVAILABLE`,
  unreserved source-Pool quantity and added to the destination only when the
  one-hop Action resolves successfully;
* multi-Resource cargo is atomic: a blocked route, unknown/insufficient
  source, or invalid entry moves neither cargo nor Actor;
* on success, the executing Actor location also changes to the destination
  Region, and sequential validation uses that projected location for the next
  step.

The deterministic destination inflow is Known without implying that the
destination Region's hidden base inventory has been surveyed.

### relay_message

The executor must satisfy the Action's command-reachability contract. A
relay can connect a target Actor when the public locality and interaction
requirements are met. The Validator projects the target's reachability for
later steps.

### Knowledge acquisition domains

The Knowledge effects described here change public visibility, not their
underlying Truth values; an Action's selected declarative Rule may separately
mutate Truth. The current generic domains are deliberately separate:

* `survey_resources` reveals Region Resource Knowledge and discoverable Pool
  metadata; it does not reveal hidden Facility Truth;
* `inspect` reveals the selected Facility or Transport target's non-Resource
  facts; it does not survey the Region inventory;
* successful `repair_communications` derives the target Region through the
  generic locality contract and `located_in` relations, then reveals that
  Region's Facility nodes and their current Runtime facts.

Communication recovery reads current Runtime Truth when visibility is
changed; it does not replay initial cached values. Agent and Player share the
same public Knowledge boundary, and communication recovery does not reveal
Region Resource inventory. Linjiang is one data-defined instance of this
generic behavior, not a Runtime scenario-key branch or a hard-coded Region
reveal table.

A Knowledge-acquisition step must match the declared dependency. When it is
the final step of an `INFORMATION_BOUNDARY` segment, the lifecycle pauses for
execution and Player acknowledgement before REPLAN. The existence of a
Knowledge-producing Action alone does not make a segment an information
boundary.

### clear_transport and repair Actions

clear_transport and repair Actions use the current generic ActionContract,
TargetBinding, locality, resource, and Rule semantics. Their meaning is not
hard-coded to Linjiang or any other Scenario.

## 14. Truth and Knowledge projection

Runtime owns Truth. Planner and Validator consume the SharedKnowledgeProjection
and the canonical PlannerInput only.

Public Knowledge can be produced by:

* survey;
* inspect;
* a deterministic Runtime failure that reveals a public fact;
* a public Action effect;
* public resource and relation state.

Node visibility and Fact visibility are independent public effects. An
explicit reveal publishes the current Runtime value; it neither arises from
inference alone nor resets the value to the Scenario's initial value. Facility/Transport facts,
Region Resource inventory, and individual Pool visibility remain separate
Knowledge domains unless an explicit generic behavior or Rule Effect updates
each one.

Inference does not turn UNKNOWN into Known state. Hidden facts, hidden
resource pools, and hidden Rule branches are never selected to help a plan.

Resource projections preserve total quantity and free/available quantity
separately. Reserved quantity is excluded from consumption and transport.

## 15. REPLAN

REPLAN occurs after Runtime execution when:

* a Runtime failure or public Knowledge change invalidates the remaining
  plan;
* an INFORMATION_BOUNDARY segment finishes and the Objective is incomplete;
* a formal segment is exhausted while the Objective is still incomplete.

Formal PLAY requires the player acknowledgement phase before it calls the
next planning request. The new cycle rebuilds the current canonical
PlannerInput from current public Knowledge. REPLAN is not REPAIR: REPAIR
corrects a rejected proposal before execution, while REPLAN plans from a new
runtime/Knowledge state.

The ObjectiveScope remains frozen. The new cycle may discard obsolete
Actions, Actors, Targets, sources, routes, and ordering.

## 16. Planning Continuity

PlanningContinuity is compact historical context for a REPLAN. The builder
retains up to the latest three accepted formal AgentPlans in oldest-to-newest
order. Each retained plan contains:

* plan_summary;
* stop_reason;
* per-step Action, Actor, Target, purpose, and short actor reason;
* execution status;
* outcome and failure codes;
* public Knowledge changes.

It also contains latest_replan_trigger and latest_new_knowledge. The latter
comes only from the public Knowledge delta of the execution that triggered the
REPLAN; it is not reconstructed by searching all historical Knowledge.

Continuity is historical context only. Canonical PlannerInput always overrides
it. It preserves still-relevant causal intent but does not force retention of
an old Action, Actor, Target, Resource source, route, or ordering.

Within one REPLAN cycle, R0 and its REPAIR attempts use the same frozen
continuity snapshot. Rejected attempts do not modify continuity.

## 17. Formal PLAY lifecycle

The player-facing lifecycle is:

    accepted Goal
      -> Player starts planning
      -> one synchronous INITIAL planning request
      -> accepted formal Plan
      -> Player confirms each Action
      -> Runtime execution
      -> action report and acknowledgement
      -> next Action, segment completion, or REPLAN

The same pattern applies to a Replan request. Intermediate rejected Provider
attempts are not rendered as player-facing draft plans. The UI shows the
current accepted Plan History, action briefing/debrief, safe Knowledge, and
the final blocked/completed state.

If the Objective is complete, the Task enters COMPLETED and does not replan.
If no valid plan can be produced within the configured bounds, the Task enters
a terminal blocked/model-rejected state with the persisted error code.

## 18. Observability

The durable lifecycle entities are:

* PlanningCycle: one INITIAL or REPLAN cycle and its frozen canonical input;
* PlanningAttempt: one Provider proposal attempt and its audit metadata;
* AgentPlan: one Validator-passing formal plan;
* AgentStep: one executable ordered step;
* WorldOperation: one Runtime Action operation and public outcome;
* PlayerExecutionCheckpoint: player pacing state, not gameplay authority.

Provider audit records are secret-safe and may include call type, model
configuration, request/payload sizes, timestamps, latency, usage,
finish_reason, parsed proposal, and Validator diagnostics. Raw credentials and
hidden reasoning are excluded.

## 19. Known future work

Only the following directions are recorded as not-yet-implemented design
work:

1. PlannerInput semantic compression: reduce repeated ActionContract and
   KnownWorld serialization without changing semantics or closure bounds.
2. Explicit working goals and goal decomposition: introduce a separate
   Objective -> Necessary Conditions -> Working Goals -> dependencies ->
   PlanSegment model only after it is designed and implemented.

The current runtime uses implicit backward prerequisite reasoning. Working
goals, milestones, and a persistent goal dependency graph are not current
implementation features.

## 20. Source map

| Area | Current implementation |
| --- | --- |
| Provider models and prompt/payload | app/agent/provider.py |
| Canonical PlannerInput construction | app/agent/planning_context.py |
| Dependency relevance closure | app/agent/dependency_closure.py |
| Plan validation and repair loop | app/agent/generic.py |
| Declarative Action/Rule execution | app/services/generic_game.py |
| Shared public Knowledge projection | app/services/knowledge_projection.py |
| Formal PLAY orchestration | app/services/play.py |
| Player-safe response projection | app/services/player_projection.py |
| Player pacing checkpoint | app/services/player_pacing.py |
| ScenarioDefinitionV2 | app/domain/scenario_v2.py |
