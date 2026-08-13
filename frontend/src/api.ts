import type { Draft, GameHistory, GameSummary, ReferenceIndex, ScenarioExample, ScenarioSummary, ScenarioVersion, ValidationResult } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly details: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  const body = (await response.json().catch(() => ({}))) as {
    error?: { message?: string; code?: string; details?: unknown };
  };
  if (!response.ok) {
    throw new ApiError(
      body.error?.message ?? `Request failed (${response.status})`,
      response.status,
      body.error?.code ?? "REQUEST_FAILED",
      body.error?.details,
    );
  }
  return body as T;
}

export const api = {
  scenarios: () => request<ScenarioSummary[]>("/api/v1/scenarios"),
  scenario: (id: string) => request<ScenarioSummary>(`/api/v1/scenarios/${id}`),
  draft: (id: string) => request<Draft>(`/api/v1/scenarios/${id}/draft`),
  references: (id: string) => request<ReferenceIndex>(`/api/v1/scenarios/${id}/draft/references`),
  examples: () => request<ScenarioExample[]>("/api/v1/scenario-examples"),
  createScenario: (payload: Record<string, unknown>) => request<ScenarioSummary>("/api/v1/scenarios", { method: "POST", body: JSON.stringify(payload) }),
  validateDraft: (id: string, revision: number) => request<ValidationResult>(`/api/v1/scenarios/${id}/draft/validate`, { method: "POST", body: JSON.stringify({ expected_revision: revision }) }),
  publishDraft: (id: string, revision: number, contentHash: string | null) => request<{ scenario: ScenarioSummary; version: ScenarioVersion }>(`/api/v1/scenarios/${id}/draft/publish`, { method: "POST", body: JSON.stringify({ expected_revision: revision, expected_content_hash: contentHash }) }),
  versions: (id: string) => request<ScenarioVersion[]>(`/api/v1/scenarios/${id}/versions`),
  restoreVersion: (id: string, revision: number, versionId: string) => request<Draft>(`/api/v1/scenarios/${id}/draft/restore`, { method: "POST", body: JSON.stringify({ expected_revision: revision, version_id: versionId }) }),
  saveDraft: (id: string, revision: number, document: Record<string, unknown>) =>
    request<Draft>(`/api/v1/scenarios/${id}/draft`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: revision, definition_document: document }),
    }),
  renameKey: (id: string, revision: number, objectKind: string, oldKey: string, newKey: string) =>
    request<Draft>(`/api/v1/scenarios/${id}/draft/rename-key`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: revision,
        object_kind: objectKind,
        old_key: oldKey,
        new_key: newKey,
      }),
    }),
  deleteObject: (id: string, revision: number, objectKind: string, objectKey: string) =>
    request<Draft>(`/api/v1/scenarios/${id}/draft/delete-object`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: revision,
        object_kind: objectKind,
        object_key: objectKey,
      }),
    }),
  games: (archived = false) => request<GameSummary[]>(`/api/v1/games?archived=${archived}`),
  game: (id: string) => request<GameSummary>(`/api/v1/games/${id}`),
  gameHistory: (id: string) => request<GameHistory>(`/api/v1/games/${id}/history`),
  createGame: (versionId: string, idempotencyKey: string) => request<GameSummary>("/api/v1/games", {
    method: "POST", body: JSON.stringify({ scenario_version_id: versionId, idempotency_key: idempotencyKey }),
  }),
  archiveGame: (id: string) => request<GameSummary>(`/api/v1/games/${id}/archive`, { method: "POST" }),
};
