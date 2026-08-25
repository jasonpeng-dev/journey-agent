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
  validation_issues: Array<{ severity: string; code: string; path: string; message: string }>;
  content_hash: string | null;
  base_scenario_version_id: string | null;
  updated_at: string;
};

export type Locator = { object_kind: string; object_key: string | null; field_path: string | null };
export type ReferenceEdge = { source: Locator; target: Locator };
export type ReferenceIndex = { scenario_id: string; revision: number; references: ReferenceEdge[] };

export type ValidationResult = {
  scenario_id: string; revision: number; content_hash: string | null; publish_ready: boolean;
  issues: Array<{ severity: "ERROR" | "WARNING"; code: string; path: string; message: string }>;
  readiness: Array<{ level: string; passed: boolean; issue_codes: string[] }>;
};

export type ScenarioVersion = {
  id: string; scenario_id: string; version_number: number; schema_version: 2;
  content_hash: string; published_at: string; definition_document?: Record<string, unknown>;
};

export type ScenarioVersionDetail = ScenarioVersion & {
  definition_document: Record<string, unknown> & {
    objectives?: Array<{ key?: unknown; name?: unknown }>;
  };
};

export type ScenarioExample = { key: string; name: string; description: string; maturity: string };

export type GameSummary = {
  id: string; scenario_id: string; scenario_version_id: string; scenario_version_number: number;
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
export type PublicRelation = { relation_key?: string | null; source_node_key: string; relation_type_key: string; target_node_key: string; source_node_name?: string | null; target_node_name?: string | null };
export type PublicActionRequirement = { action_key: string; action_name: string; required_actor_role_key?: string | null; required_actor_role_name?: string | null; source_relation_type_key?: string | null; known_preconditions: Array<{ node_key: string; fact_key: string; selector: string; current_value: string | number | boolean; failure_condition?: Record<string, unknown> }>; cost?: Record<string, number>; resource_costs?: Record<string, number> };
export type PublicTargetActionContract = { target_key: string; action_key: string; action_name: string; required_actor_role_key?: string | null; required_actor_role_name?: string | null; source_relation_type_key?: string | null; cost?: Record<string, number>; special_requirements?: Array<Record<string, unknown>>; effects?: Array<Record<string, unknown>> };
export type PublicPlanStep = { id: string; sequence: number; description: string; assigned_actor_name: string; subtitle?: string | null; status: "PENDING" | "CURRENT" | "COMPLETED" | "FAILED" | "BLOCKED"; result_summary: string | null; location?: ActionLocation | null };
export type PublicPlan = { strategy_summary: string; updated: boolean; steps: PublicPlanStep[] };
export type PublicPlanHistoryStep = { id: string; sequence: number; action_name: string; assigned_actor_name: string; subtitle?: string | null; status: "PLANNED" | "CURRENT" | "COMPLETED" | "FAILED" | "CANCELLED"; result_summary: string | null; location?: ActionLocation | null };
export type PlanInterruption = { kind: "FAILURE" | "KNOWLEDGE_CONFLICT"; step_id: string; sequence: number; step_name: string };
export type PublicPlanHistory = { id: string; ordinal: number; status: "EXECUTING" | "ADJUSTED" | "COMPLETED" | "BLOCKED"; completed_steps: number; total_steps: number; failed_step_name: string | null; interruption?: PlanInterruption | null; steps: PublicPlanHistoryStep[] };
export type MissionRoadmapStage = { key: string; name: string; description: string; status: "COMPLETED" | "CURRENT" | "PENDING"; objective_key: string | null };
export type TimelineEventKind = "GOAL_ACCEPTED" | "PLAN_CREATED" | "TASK_STARTED" | "ACTION_BRIEFING" | "ACTION_RESULT" | "PLAN_UPDATED" | "APPROVAL_REQUIRED" | "APPROVAL_APPROVED" | "APPROVAL_REJECTED" | "TASK_COMPLETED" | "TASK_BLOCKED" | "TASK_ABORTED";
export type KnowledgeChange = { kind: "NODE_REVEALED" | "FACT_REVEALED" | "RESOURCE_DISCOVERED" | "RESOURCE_INVENTORY_REVEALED" | "RESOURCE_SURVEY_COMPLETED" | "RELATION_REVEALED"; key: string; name: string; value: string | number | boolean | null };
export type PublicTimelineEvent = { id: string; kind: TimelineEventKind; title: string; detail: string | null; actor_name: string | null; result_summary: string | null; success: boolean | null; knowledge_changes: KnowledgeChange[]; occurred_at: string | null; duration_ms?: number | null; location?: ActionLocation | null };
export type ExecutionPhase = "AWAITING_PLAN_START" | "AWAITING_ACTION_ACK" | "AWAITING_DEBRIEF_ACK" | "AWAITING_REPLAN_ACK" | "APPROVAL_REQUIRED" | "COMPLETED" | "BLOCKED" | "ABORTED";
export type ActionBriefing = { step_id: string; action_name: string; actor_name: string; target_name: string; purpose: string; location?: ActionLocation | null };
export type ActionDebrief = { step_id: string; action_name: string; success: boolean; result_summary: string; knowledge_changes: KnowledgeChange[]; plan_adjusted: boolean; plan_adjustment_summary: string | null; plan_invalidated?: boolean; plan_invalidation_reason?: string | null; location?: ActionLocation | null };
export type PublicTask = { id: string; version: number; goal: string; status: string; execution_phase: ExecutionPhase; pacing_version: number; objective_names: string[]; roadmap: { stages: MissionRoadmapStage[] }; plan: PublicPlan | null; plan_history: PublicPlanHistory[]; timeline: PublicTimelineEvent[]; briefing: ActionBriefing | null; debrief: ActionDebrief | null; explanation: string | null };
export type PublicTaskSummary = { id: string; sequence: number; goal: string; objective_names: string[]; status: string; execution_phase: ExecutionPhase; created_at: string; completed_at: string | null };
export type ResourceIntelligence = {
  total_regions: number;
  visible_region_count: number;
  regions: Record<string, {
    region_name?: string;
    resource_inventory_visibility: "HIDDEN" | "VISIBLE";
    resource_survey_completed: boolean;
    resources: Record<string, {
      resource_name: string;
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
export type PlayerGameState = { game: GameSummary; visible_nodes: Array<{ key: string; name: string; accessible: boolean; node_type_key?: string | null; region_key?: string | null; region_name?: string | null; endpoint_region_keys?: string[]; endpoint_region_names?: string[]; associated_known_resources?: Array<Record<string, unknown>> }>; known_facts: Array<{ node_key: string; fact_key: string; name: string; value: string | number | boolean; node_name?: string | null; node_type_key?: string | null; region_key?: string | null; region_name?: string | null; endpoint_region_keys?: string[]; endpoint_region_names?: string[] }>; known_relations?: PublicRelation[]; known_action_requirements?: PublicActionRequirement[]; known_target_action_contracts?: PublicTargetActionContract[]; resources: Array<{ key: string; name: string; value: number; reserved_value: number; pool_key?: string; facility_key?: string | null; availability?: "AVAILABLE" | "UNAVAILABLE"; scope_node_key?: string | null; scope_node_name?: string | null; scope_region_key?: string | null; scope_region_name?: string | null }>; resource_intelligence?: ResourceIntelligence; actors: Array<{ key: string; name: string; role_name: string; current_node_name: string; command_reachability: "ONLINE" | "DISCONNECTED" }>; current_task: PublicTask | null; task_history: PublicTaskSummary[]; pending_approval_id: string | null };
export type GoalSubmission = { status: "ACCEPTED" | "NEEDS_CLARIFICATION" | "UNSUPPORTED"; task: PublicTask | null; clarification_prompt: string | null; candidate_objective_names: string[]; explanation: string | null };
export type DeveloperSnapshot = { game: GameSummary; truth: Record<string, unknown>; knowledge: Record<string, unknown>; actors: Array<Record<string, unknown>>; tasks: Array<Record<string, unknown>>; plans: Array<Record<string, unknown>>; operations: Array<Record<string, unknown>>; rule_outcomes: Array<Record<string, unknown>>; decisions: Array<Record<string, unknown>>; memory: Array<Record<string, unknown>>; history: Array<Record<string, unknown>> };
export type DraftSandboxResult = { scenario_id: string; revision: number; sandbox_started: boolean; issues: ValidationResult["issues"]; goal_status: string | null; task: PublicTask | null; visible_nodes: PlayerGameState["visible_nodes"]; known_facts: PlayerGameState["known_facts"]; resources: PlayerGameState["resources"] };
