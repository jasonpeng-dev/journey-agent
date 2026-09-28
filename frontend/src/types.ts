export type ScenarioSummary = {
  id: string;
  key: string;
  name: string;
  status: "DRAFT" | "PUBLISHED" | "ARCHIVED";
  draft_revision: number;
  current_published_version_id: string | null;
  current_published_version_number: number | null;
  created_at: string;
  updated_at: string;
  version_count?: number;
};

export type Draft = {
  scenario_id: string;
  revision: number;
  definition_document: Record<string, unknown>;
  validation_status: string;
  validation_issues: Array<{ severity: string; code: string; path: string; message: string; locator?: Locator | null }>;
  content_hash: string | null;
  base_scenario_version_id: string | null;
  updated_at: string;
};

export type Locator = { object_kind: string; object_key: string | null; field_path: string | null };
export type ReferenceEdge = { source: Locator; target: Locator };
export type ReferenceIndex = { scenario_id: string; revision: number; references: ReferenceEdge[] };
export type DraftTransformOperation =
  | { kind: "RENAME_KEY"; object_kind: string; old_key: string; new_key: string }
  | { kind: "DELETE_OBJECT"; object_kind: string; object_key: string }
  | { kind: "DELETE_FACT"; object_kind: "node"; node_key: string; fact_key: string }
  | { kind: "DELETE_ROOT_COLLECTION_ITEM"; object_kind: string; collection: string; identity: string }
  | { kind: "DELETE_NESTED"; object_kind: string; parent_kind: string; parent_key: string; collection: string; nested_key: string };
export type WorkingCopyReferenceAnalysis = { scenario_id: string; base_revision: number; source: "WORKING_COPY"; references: ReferenceEdge[] };
export type WorkingCopyTransformResult = WorkingCopyReferenceAnalysis & { definition_document: Record<string, unknown> };
export type CompletenessItem = {
  key: string;
  title: string;
  level: "COMPLETE" | "INCOMPLETE_REQUIRED" | "VALID_BUT_UNCONFIGURED" | "OPTIONAL_ENHANCEMENT" | "LEGACY_FALLBACK";
  dependency_kind: "HARD_REQUIRED" | "PUBLISH_REQUIRED" | "RUNTIME_REQUIRED" | "SEMANTIC_REQUIRED" | "RECOMMENDED" | "OPTIONAL" | "DERIVED" | "LEGACY" | "NONE";
  message: string;
  path: string;
  locator?: Locator | null;
  action: "OPEN" | "CREATE" | "CONFIGURE" | "NONE";
  reference_locator?: Locator | null;
  reference_owner?: string | null;
};
export type ValidationIssue = { severity: "ERROR" | "WARNING"; code: string; path: string; message: string; locator?: Locator | null; type?: string | null };
export type CompletenessResult = {
  scenario_id: string;
  base_revision: number;
  items: CompletenessItem[];
  validation_issues?: ValidationIssue[];
  required_missing: number;
  recommended_missing: number;
  validation_issue_count: number;
  reference_edge_count: number;
};

export type ValidationResult = {
  scenario_id: string; revision: number; content_hash: string | null; publish_ready: boolean;
  issues: ValidationIssue[];
  readiness: Array<{ level: string; passed: boolean; issue_codes: string[] }>;
};

export type ScenarioVersion = {
  id: string; scenario_id: string; version_number: number; schema_version: 2 | 3;
  content_hash: string; published_at: string; definition_document?: Record<string, unknown>;
};

export type ScenarioVersionDetail = ScenarioVersion & {
  definition_document: Record<string, unknown> & {
    objectives?: Array<{ key?: unknown; name?: unknown }>;
  };
};

export type ScenarioExample = { key: string; name: string; description: string; maturity: string };

export type ScenarioArtifactMetadata = { key: string; name: string; description: string };
export type ScenarioArtifactSummary = {
  artifact_type: string;
  artifact_version: number;
  content_type: "draft" | "release";
  schema_version: number;
  scenario: ScenarioArtifactMetadata;
  content_hash: string;
};
export type PortabilityValidationIssue = {
  code: string;
  path: string;
  message: string;
  severity: string;
  type: string;
};
export type PortabilityValidation = {
  structurally_valid: boolean;
  publish_ready: boolean;
  issue_count: number;
  issues: PortabilityValidationIssue[];
};
export type NewScenarioArtifactPreview = {
  artifact: ScenarioArtifactSummary;
  candidate_target_key: string;
  key_conflict: boolean;
  validation: PortabilityValidation;
  content_hash: string;
  what_import_will_create: {
    records: string[];
    published_version_number: number | null;
    game_created: boolean;
  };
};
export type ScenarioArtifactImportResult = {
  status: "IMPORTED";
  artifact: Omit<ScenarioArtifactSummary, "scenario">;
  scenario: Pick<ScenarioSummary, "id" | "key" | "name" | "status">;
  draft: { revision: number; validation_status: string };
  published_version: { id: string; version_number: number; schema_version: number; content_hash: string; published_at: string } | null;
  game_created: false;
};

export type RestorePreview = {
  scenario_id: string;
  version: ScenarioVersion;
  current_draft_revision: number;
  candidate_working_document: Record<string, unknown>;
  semantic_diff: SemanticDiff;
  unchanged: boolean;
  restore_supported: boolean;
  restore_note?: string | null;
};

export type ScenarioDependentGame = {
  game_id: string;
  identifier: string;
  status: string;
  scenario_version_id: string;
  scenario_version_number: number;
  created_at: string;
  updated_at: string;
};

export type ScenarioDeletionImpact = {
  scenario_id: string;
  scenario_name: string;
  scenario_key: string;
  draft_revision: number;
  published_version_count: number;
  dependent_games: ScenarioDependentGame[];
  can_delete: boolean;
};

export type PresentationTemplate = "compact" | "standard" | "detailed";
export type PresentationDensity = "COMPACT" | "STANDARD" | "DETAILED";
export type PresentationDefaultOpen = "COLLAPSED" | "COMPACT" | "FULL";
export type PresentationSummarySlot = "HEADER" | "BODY" | "BOTH";
export type PresentationEntityDetail = "SUMMARY" | "DETAIL" | "CAUSALITY";
export type PresentationKnowledgeLevel = "A" | "A+B" | "A+B+C";
export type PresentationPlanDetail = "COLLAPSED" | "COMPACT" | "FULL";
export type PresentationTimelineDensity = "COMPACT" | "STANDARD" | "DETAILED";
export type PresentationNodeFamily = "GENERIC" | "REGION" | "FACILITY" | "TRANSPORT";
export type PresentationSemanticSlot =
  | "NAME"
  | "NODE_TYPE"
  | "VISIBILITY"
  | "ACCESS"
  | "FACTS"
  | "RELATIONS"
  | "RESOURCES"
  | "STATUS";
export type PresentationResourceSlot = "NAME" | "AMOUNT" | "STATUS" | "UNIT";
export type PresentationRelationSlot = "TYPE" | "TARGET" | "VISIBILITY";
export type PresentationActorField =
  | "NAME"
  | "ROLE"
  | "LOCATION"
  | "STATUS"
  | "TASK"
  | "CAPABILITIES"
  | "COMMAND_REACHABILITY";
export type FactPresentationRole =
  | "HEADER_PRIMARY"
  | "HEADER_SECONDARY"
  | "BODY_MAIN"
  | "SUPPORTING"
  | "REQUIREMENT_ONLY";

export type GlobalPresentationOverrides = {
  density?: PresentationDensity;
  default_open?: PresentationDefaultOpen;
  summary_slot?: PresentationSummarySlot;
  semantic_order?: PresentationSemanticSlot[];
};

export type InitializationFinding = {
  identity: string;
  label: string;
  canonical_path: string;
  owner: "DESIGN_ONLY" | "INITIALIZATION_ONLY" | "SHARED_CONTEXT_READONLY" | "SYSTEM" | "LEGACY" | "DERIVED_READONLY";
  source: "EXPLICIT" | "DEFAULT" | "LEGACY_FALLBACK" | "ENGINE" | "DERIVED" | "MISSING" | "INVALID";
  severity: "INFO" | "WARNING" | "BLOCKING";
  value: unknown;
  locator: { section: string; object_kind: string | null; object_key: string | null; field_path: string | null };
  message: string;
};

export type InitializationProjectionItem = {
  id: string;
  label: string;
  locator: InitializationFinding["locator"];
  field_ids: string[];
  readonly: boolean;
  context: Record<string, unknown>;
};

export type InitializationProjection = {
  domains: Array<{
    id: string;
    label: string;
    groups: Array<{ id: string; label: string; items: InitializationProjectionItem[] }>;
  }>;
  findings: InitializationFinding[];
  summary: { nodes: number; actors: number; resource_pools: number; relations: number; derived_states: number; warnings: number };
};

export type InitializationPreview = {
  revision: number;
  projection: InitializationProjection;
  parity: { published: boolean; initialization_changes: string[]; design_changes: string[] };
  partial?: boolean;
  omitted_issue_count?: number;
  issues?: InitializationPreviewIssue[];
};
export type InitializationPreviewIssue = {
  identity?: string | null;
  canonical_owner?: string | null;
  reference_owner?: string | null;
  field_path?: string | null;
  locator?: InitializationFinding["locator"];
  loc?: Array<string | number>;
  type?: string;
  msg?: string;
};
export type InitializationPreviewFocus = { object_kind: "node" | "actor" | "relation" | "resource"; object_key: string };
export type SemanticDiffEntry = {
  scope: "DESIGN" | "INITIALIZATION";
  editor_section: string;
  editor_subsection: string;
  object_kind: string;
  object_key: string | null;
  object_display_name: string;
  change_kind: "ADDED" | "REMOVED" | "MODIFIED" | "REORDERED";
  locator: Locator;
  field_path: string | null;
  before?: unknown;
  after?: unknown;
};
export type SemanticDiffSectionSummary = {
  section: string;
  count: number;
  subsections: Array<{ subsection: string; count: number }>;
};
export type SemanticDiff = {
  published: boolean;
  compared_version: { id: string; version_number: number; schema_version: 2 | 3 } | null;
  published_version_id: string | null;
  published_version_number: number | null;
  published_schema_version: 2 | 3 | null;
  is_equal: boolean | null;
  comparable: boolean;
  total_changed_objects: number;
  total_changed_items: number;
  section_summaries: SemanticDiffSectionSummary[];
  entries: SemanticDiffEntry[];
  error_message?: string | null;
};
export type WorldPresentationOverrides = {
  entity_detail?: PresentationEntityDetail;
  knowledge_level?: PresentationKnowledgeLevel;
  resource_order?: PresentationResourceSlot[];
  relation_order?: PresentationRelationSlot[];
};
export type ActorTeamPresentationOverrides = {
  visible_fields?: PresentationActorField[];
  field_order?: PresentationActorField[];
};
export type GoalExecutionPresentationOverrides = {
  roadmap_detail?: PresentationEntityDetail;
  plan_default?: PresentationPlanDetail;
  timeline_density?: PresentationTimelineDensity;
};
export type NodeFamilyPresentationOverride = {
  node_family: PresentationNodeFamily;
  entity_detail?: PresentationEntityDetail;
  default_open?: PresentationDefaultOpen;
  semantic_order?: PresentationSemanticSlot[];
};
export type SemanticPresentationOverride = {
  semantic_key: string;
  summary_slot?: PresentationSummarySlot;
  entity_detail?: PresentationEntityDetail;
  default_open?: PresentationDefaultOpen;
};
export type PresentationProfileDocument = {
  schema_version: 1;
  template: PresentationTemplate;
  global_display?: GlobalPresentationOverrides;
  world_entities?: WorldPresentationOverrides;
  actor_team?: ActorTeamPresentationOverrides;
  goal_execution?: GoalExecutionPresentationOverrides;
  family_overrides?: NodeFamilyPresentationOverride[];
  semantic_overrides?: SemanticPresentationOverride[];
};
export type PresentationProfileResponse = {
  scenario_id: string;
  revision: number;
  profile: PresentationProfileDocument;
  updated_at: string;
};
export type PresentationProfileRevisionCheck = { scenario_id: string; revision: number };
export type PresentationProfileRevision = {
  scenario_id: string;
  revision: number;
  profile: PresentationProfileDocument;
  created_at: string;
};
export type PresentationProfileHistoryResponse = {
  scenario_id: string;
  revisions: PresentationProfileRevision[];
};

export type GameSummary = {
  id: string; scenario_id: string; scenario_name: string; scenario_version_id: string; scenario_version_number: number;
  scenario_content_hash: string; status: "ACTIVE" | "SUSPENDED" | "ARCHIVED" | "FAILED" | "COMPLETED";
  runtime_revision: number;
  is_checkpoint: boolean;
  checkpointed_from_game_instance_id: string | null;
  checkpoint_source_runtime_revision: number | null;
  inherited_task_count: number;
  active_task_id: string | null; created_at: string; updated_at: string;
};

export type GameHistory = {
  tasks: Array<{ id: string; goal: string; status: string }>;
  operations: Array<{ id: string; action_key: string; status: string; outcome: unknown }>;
  decisions: Array<{ id: string; action_key: string; status: string }>;
};

export type ActionLocation = { kind: string; summary: string; detail: string | null };
export type PublicRelation = { relation_key?: string | null; source_node_key: string; relation_type_key: string; target_node_key: string; source_node_name?: string | null; target_node_name?: string | null; relation_type_name?: string | null; relation_type_description?: string | null; is_structural?: boolean };
export type PublicActionResourceRequirement = { resource_key: string; scope?: Record<string, unknown>; minimum: number; known_status?: "KNOWN" | "KNOWN_ZERO" | "UNKNOWN"; known_available?: number | null };
export type PublicSourceRequirement = {
  source_node_key: string;
  kind: "SOURCE_REQUIREMENTS" | "POWER_SOURCE_READINESS";
  status: "SATISFIED" | "UNSATISFIED";
  conditions: Array<{
    fact_key: string;
    operator: "EQ" | "NE" | "IN" | "NOT_IN" | "GT" | "GTE" | "LT" | "LTE";
    value?: string | number | boolean;
    values?: Array<string | number | boolean>;
  }>;
};
export type PublicActionRequirement = { action_key: string; action_name: string; required_actor_role_key?: string | null; required_actor_role_name?: string | null; target_actor_roles?: Array<Record<string, unknown>>; source_relation_type_key?: string | null; source_requirements?: PublicSourceRequirement[]; known_preconditions: Array<{ node_key: string; fact_key: string; selector: string; current_value: string | number | boolean; failure_condition?: Record<string, unknown> }>; cost?: Record<string, number>; resource_costs?: Record<string, number>; resource_requirements?: PublicActionResourceRequirement[] };
export type PublicTargetActionContract = { target_key: string; action_key: string; action_name: string; required_actor_role_key?: string | null; required_actor_role_name?: string | null; source_relation_type_key?: string | null; source_node_key?: string | null; source_binding_key?: string | null; cost?: Record<string, number>; resource_requirements?: PublicActionResourceRequirement[]; special_requirements?: Array<Record<string, unknown>>; effects?: Array<Record<string, unknown>> };
export type PublicProducerStatus = "SATISFIED" | "UNSATISFIED" | "UNKNOWN";
export type PublicProducerOutput = {
  semantic_key: string;
  target_key: string;
  fact_key?: string | null;
  resource_key?: string | null;
  desired_value?: string | number | boolean | null;
  status: PublicProducerStatus;
};
export type PublicProducerRequirement = {
  key: string;
  kind: "RESOURCE" | "ROLE" | "INTERACTION" | "FACT" | "STATE" | "SOURCE" | "SPECIAL";
  status?: PublicProducerStatus | null;
  resource_key?: string | null;
  minimum?: number | null;
  scope?: Record<string, unknown> | null;
  known_status?: "KNOWN" | "KNOWN_ZERO" | "UNKNOWN" | null;
  known_available?: number | null;
  role_key?: string | null;
  display_name?: string | null;
  node_key?: string | null;
  fact_key?: string | null;
  operator?: string | null;
  value?: string | number | boolean | null;
  values?: Array<string | number | boolean>;
  source_node_key?: string | null;
  source_kind?: string | null;
  conditions?: Array<Record<string, unknown>>;
  condition?: Record<string, unknown> | null;
};
export type PublicProducerBinding = {
  binding_key: string;
  action_key: string;
  action_name: string;
  target_key: string;
  source_node_key?: string | null;
  source_binding_key?: string | null;
  producer_kind: "ACTION_PRODUCED_STATE" | "CONDITION" | "RESOURCE_AVAILABILITY";
  outputs: PublicProducerOutput[];
  requirements: PublicProducerRequirement[];
};
export type PublicPlanStep = { id: string; sequence: number; description: string; assigned_actor_name: string; subtitle?: string | null; status: "PENDING" | "CURRENT" | "COMPLETED" | "FAILED" | "BLOCKED"; result_summary: string | null; location?: ActionLocation | null };
export type PublicPlan = { strategy_summary: string; updated: boolean; steps: PublicPlanStep[] };
export type PublicResourceUsage = { resource_key: string; resource_name: string; amount: number };
export type PublicResourceUsageKind = "CONSUME" | "TRANSPORT";
export type PublicPlanHistoryStep = { id: string; sequence: number; action_name: string; assigned_actor_name: string; subtitle?: string | null; status: "PLANNED" | "CURRENT" | "COMPLETED" | "FAILED" | "CANCELLED"; result_summary: string | null; location?: ActionLocation | null; resource_usage?: PublicResourceUsage[]; resource_usage_kind?: PublicResourceUsageKind | null };
export type PlanInterruption = { kind: "FAILURE" | "KNOWLEDGE_CONFLICT"; step_id: string; sequence: number; step_name: string };
export type PublicPlanDisplayStatus = "EXECUTING" | "ADJUSTED" | "STAGE_COMPLETED" | "OBJECTIVE_COMPLETED" | "BLOCKED";
export type PublicPlanHistory = { id: string; ordinal: number; status: "EXECUTING" | "ADJUSTED" | "COMPLETED" | "BLOCKED"; display_status?: PublicPlanDisplayStatus | null; display_reason?: string | null; duration_ms?: number | null; planning_cycle_id?: string | null; completed_steps: number; total_steps: number; failed_step_name: string | null; interruption?: PlanInterruption | null; steps: PublicPlanHistoryStep[] };
export type PublicPlanningAttempt = { attempt_index: number; call_type: "INITIAL_PLAN" | "REPLAN" | "REPAIR"; status: "RUNNING" | "ACCEPTED" | "REJECTED" | "ERROR" | "TIMEOUT"; started_at: string | null; finished_at: string | null; duration_ms: number | null; provider_outcome: string | null; provider_latency_ms: number | null; validator_summary: Array<Record<string, unknown>>; provider_error_category: string | null; provider_error_code: string | null; accepted_step_count: number };
export type PublicPlanningCycle = { id: string; cycle_type: "INITIAL" | "REPLAN"; status: string; started_at: string | null; finished_at: string | null; wall_clock_duration_ms: number | null; attempt_count: number; final_outcome: string; attempts: PublicPlanningAttempt[] };
export type MissionRoadmapRequirement = {
  identity?: string;
  key: string;
  kind?: "FACT" | "RESOURCE_AT_LEAST" | "DERIVED_STATE" | "ACTION_COMPLETED";
  description: string;
  node_key?: string;
  fact_key?: string;
  accepted_values?: Array<string | number | boolean>;
  region_key?: string;
  resource_key?: string;
  minimum?: number;
  derived_key?: string;
  current_known_value?: string | number | boolean | null;
  current_known_available?: number | null;
  knowledge_status?: "KNOWN" | "KNOWN_ZERO" | "UNKNOWN";
  action_key?: string;
  action_name?: string;
  actor_key?: string | null;
  actor_name?: string | null;
  target_key?: string | null;
  target_name?: string | null;
  binding_constraints?: Array<Record<string, unknown>>;
  parameter_constraints?: Record<string, unknown> | null;
  match_mode?: "ONE_SUCCESSFUL_INVOCATION";
  boundary?: "TASK_OWNED_OPERATION";
  operation_status?: "PENDING" | "COMPLETED";
};
export type MissionRoadmapStage = { key: string; name: string; description: string; status: "COMPLETED" | "CURRENT" | "PENDING"; objective_key: string | null; requirements: MissionRoadmapRequirement[] };
export type TimelineEventKind = "GOAL_ACCEPTED" | "PLAN_CREATED" | "TASK_STARTED" | "ACTION_BRIEFING" | "ACTION_RESULT" | "PLAN_UPDATED" | "APPROVAL_REQUIRED" | "APPROVAL_APPROVED" | "APPROVAL_REJECTED" | "TASK_COMPLETED" | "TASK_BLOCKED" | "TASK_ABORTED";
export type KnowledgeChange = { kind: "NODE_REVEALED" | "FACT_REVEALED" | "RESOURCE_DISCOVERED" | "RESOURCE_INVENTORY_REVEALED" | "RESOURCE_SURVEY_COMPLETED" | "RELATION_REVEALED"; key: string; name: string; value: string | number | boolean | null };
export type PublicTimelineEvent = { id: string; kind: TimelineEventKind; planning_cycle_id?: string | null; title: string; detail: string | null; actor_name: string | null; result_summary: string | null; success: boolean | null; knowledge_changes: KnowledgeChange[]; occurred_at: string | null; duration_ms?: number | null; location?: ActionLocation | null; resource_usage?: PublicResourceUsage[]; resource_usage_kind?: PublicResourceUsageKind | null };
export type ExecutionPhase = "AWAITING_PLAN_START" | "AWAITING_PLAN_ATTEMPT" | "AWAITING_ACTION_ACK" | "AWAITING_DEBRIEF_ACK" | "AWAITING_REPLAN_ACK" | "APPROVAL_REQUIRED" | "COMPLETED" | "BLOCKED" | "ABORTED";
export type ActionBriefing = { step_id: string; action_name: string; actor_name: string; target_name: string; purpose: string; location?: ActionLocation | null };
export type ActionDebrief = { step_id: string; action_name: string; success: boolean; result_summary: string; knowledge_changes: KnowledgeChange[]; plan_adjusted: boolean; plan_adjustment_summary: string | null; plan_invalidated?: boolean; plan_invalidation_reason?: string | null; location?: ActionLocation | null };
export type PublicTask = { id: string; version: number; goal: string; status: string; execution_phase: ExecutionPhase; pacing_version: number; goal_source_kind?: "PREDEFINED" | "PARAMETERIZED" | "AD_HOC_DYNAMIC"; goal_requirements?: MissionRoadmapRequirement[]; objective_names: string[]; roadmap: { stages: MissionRoadmapStage[] }; plan: PublicPlan | null; plan_history: PublicPlanHistory[]; planning_process?: PublicPlanningCycle[]; timeline: PublicTimelineEvent[]; briefing: ActionBriefing | null; debrief: ActionDebrief | null; explanation: string | null };
export type PublicTaskSummary = { id: string; sequence: number; goal: string; goal_source_kind?: "PREDEFINED" | "PARAMETERIZED" | "AD_HOC_DYNAMIC"; objective_names: string[]; status: string; execution_phase: ExecutionPhase; created_at: string; completed_at: string | null };
export type ResourceIntelligence = {
  total_regions: number;
  visible_region_count: number;
  regions: Record<string, {
    region_name?: string;
    resource_inventory_visibility: "HIDDEN" | "VISIBLE";
    resource_survey_completed: boolean;
    resources: Record<string, {
      resource_name: string;
      unit?: string | null;
      display_unit?: string | null;
      known_total: number | null;
      known_available: number;
      pools: Array<{
        pool_key: string;
        quantity: number;
        facility_key: string | null;
        facility_name?: string | null;
        availability: "AVAILABLE" | "UNAVAILABLE";
        availability_requirement?: Record<string, unknown>;
        availability_requirement_status?: "KNOWN" | "UNKNOWN";
      }>;
    }>;
  }>;
  global_resources: Record<string, {
    resource_name: string;
    unit?: string | null;
    display_unit?: string | null;
    known_total: number | null;
    known_available: number;
    pools: Array<{
        pool_key: string;
        quantity: number;
        facility_key: string | null;
        facility_name?: string | null;
        availability: "AVAILABLE" | "UNAVAILABLE";
      availability_requirement?: Record<string, unknown>;
      availability_requirement_status?: "KNOWN" | "UNKNOWN";
    }>;
  }>;
};
export type PublicResolvedGoalDraft = { draft_id: string; submitted_goal: string; presentation_text: string; status: "READY"; created_at: string };
export type PublicGoalPreset = { key: string; name: string };
export type PublicScenarioMetadata = { quick_inputs?: string[]; goal_presets?: PublicGoalPreset[] };
export type PublicEntityPresentation = { summary_slot: "HEADER" | "BODY" | "BOTH"; detail_level: "SUMMARY" | "DETAIL" | "CAUSALITY"; default_open: "COLLAPSED" | "COMPACT" | "FULL"; knowledge_level: "A" | "A+B" | "A+B+C"; semantic_order: string[] };
export type PublicPresentation = { revision: number; template: "compact" | "standard" | "detailed"; density: "COMPACT" | "STANDARD" | "DETAILED"; default_open: "COLLAPSED" | "COMPACT" | "FULL"; summary_slot: "HEADER" | "BODY" | "BOTH"; entity_detail: "SUMMARY" | "DETAIL" | "CAUSALITY"; knowledge_level: "A" | "A+B" | "A+B+C"; semantic_order: string[]; resource_order: string[]; relation_order: string[]; actor_fields: string[]; roadmap_detail: "SUMMARY" | "DETAIL" | "CAUSALITY"; plan_default: "COLLAPSED" | "COMPACT" | "FULL"; timeline_density: "COMPACT" | "STANDARD" | "DETAILED" };
export type PlayerGameState = { game: GameSummary; scenario_metadata: PublicScenarioMetadata; presentation?: PublicPresentation; visible_nodes: Array<{ key: string; name: string; accessible: boolean; node_type_key?: string | null; node_family?: "GENERIC" | "REGION" | "FACILITY" | "TRANSPORT"; region_key?: string | null; region_name?: string | null; endpoint_region_keys?: string[]; endpoint_region_names?: string[]; associated_known_resources?: Array<Record<string, unknown>>; presentation?: PublicEntityPresentation }>; known_facts: Array<{ node_key: string; fact_key: string; name: string; value: string | number | boolean; value_label?: string | null; summary_value_label?: string | null; detail_value_label?: string | null; presentation_role?: FactPresentationRole | null; presentation_slot?: "HEADER_PRIMARY" | "HEADER_SECONDARY" | "SEMANTIC"; node_name?: string | null; node_type_key?: string | null; node_family?: "GENERIC" | "REGION" | "FACILITY" | "TRANSPORT"; region_key?: string | null; region_name?: string | null; endpoint_region_keys?: string[]; endpoint_region_names?: string[] }>; known_relations?: PublicRelation[]; known_action_requirements?: PublicActionRequirement[]; known_target_action_contracts?: PublicTargetActionContract[]; known_producer_bindings?: PublicProducerBinding[]; resources: Array<{ key: string; name: string; value: number; reserved_value: number; pool_key?: string; facility_key?: string | null; availability?: "AVAILABLE" | "UNAVAILABLE"; scope_node_key?: string | null; scope_node_name?: string | null; scope_region_key?: string | null; scope_region_name?: string | null; unit?: string | null; display_unit?: string | null }>; resource_intelligence?: ResourceIntelligence; actors: Array<{ key: string; name: string; role_name: string; current_node_name: string; status?: "ACTIVE" | "PLANNED" | "IDLE"; task_name?: string | null; command_reachability: "ONLINE" | "DISCONNECTED" }>; current_task: PublicTask | null; current_goal_draft?: PublicResolvedGoalDraft | null; task_history: PublicTaskSummary[]; pending_approval_id: string | null };
export type GoalSubmission = { resolution_id: string; submitted_goal: string; status: "READY_FOR_CONFIRMATION" | "NEEDS_CLARIFICATION" | "UNSUPPORTED"; presentation_text: string; draft_id: string | null };
export type DeveloperSnapshot = { game: GameSummary; truth: Record<string, unknown>; knowledge: Record<string, unknown>; actors: Array<Record<string, unknown>>; tasks: Array<Record<string, unknown>>; plans: Array<Record<string, unknown>>; operations: Array<Record<string, unknown>>; rule_outcomes: Array<Record<string, unknown>>; decisions: Array<Record<string, unknown>>; memory: Array<Record<string, unknown>>; history: Array<Record<string, unknown>> };
export type DraftSandboxResult = { scenario_id: string; revision: number; sandbox_started: boolean; issues: ValidationResult["issues"]; goal_status: string | null; task: PublicTask | null; visible_nodes: PlayerGameState["visible_nodes"]; known_facts: PlayerGameState["known_facts"]; resources: PlayerGameState["resources"] };
