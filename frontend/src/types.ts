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
