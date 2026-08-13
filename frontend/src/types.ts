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

export type ScenarioExample = { key: string; name: string; description: string; maturity: string };

export type GameSummary = {
  id: string; scenario_id: string; scenario_version_id: string; scenario_version_number: number;
  scenario_content_hash: string; status: "ACTIVE" | "SUSPENDED" | "ARCHIVED" | "FAILED" | "COMPLETED";
  active_task_id: string | null; created_at: string; updated_at: string;
};

export type GameHistory = {
  tasks: Array<{ id: string; goal: string; status: string }>;
  operations: Array<{ id: string; action_key: string; status: string; outcome: unknown }>;
  decisions: Array<{ id: string; action_key: string; status: string }>;
};

export type PublicPlanStep = { id: string; sequence: number; description: string; assigned_actor_name: string; status: "PENDING" | "CURRENT" | "COMPLETED" | "FAILED" | "BLOCKED"; result_summary: string | null };
export type PublicTask = { id: string; version: number; goal: string; status: string; objective_names: string[]; plan: { version: number; strategy_summary: string; updated: boolean; steps: PublicPlanStep[] } | null; explanation: string | null };
export type PlayerGameState = { game: GameSummary; visible_nodes: Array<{ key: string; name: string; accessible: boolean }>; known_facts: Array<{ node_key: string; fact_key: string; name: string; value: string | number | boolean }>; resources: Array<{ key: string; name: string; value: number; reserved_value: number }>; current_task: PublicTask | null; pending_approval_id: string | null };
export type GoalSubmission = { status: "ACCEPTED" | "NEEDS_CLARIFICATION" | "UNSUPPORTED"; task: PublicTask | null; clarification_prompt: string | null; candidate_objective_names: string[]; explanation: string | null };
export type DeveloperSnapshot = { game: GameSummary; truth: Record<string, unknown>; knowledge: Record<string, unknown>; actors: Array<Record<string, unknown>>; tasks: Array<Record<string, unknown>>; plans: Array<Record<string, unknown>>; operations: Array<Record<string, unknown>>; rule_outcomes: Array<Record<string, unknown>>; decisions: Array<Record<string, unknown>>; memory: Array<Record<string, unknown>>; history: Array<Record<string, unknown>> };
