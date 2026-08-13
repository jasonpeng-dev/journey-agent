import type { Draft, ReferenceIndex, ScenarioSummary } from "./types";

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
};
