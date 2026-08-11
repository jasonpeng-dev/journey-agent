"use strict";

export class ApiError extends Error {
  constructor(message, { status = 0, code = "", details = null, requestId = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

export async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = body.error || {};
    let message = error.message || `请求失败（HTTP ${response.status}）`;
    const validationErrors = error.details?.errors;
    if (response.status === 422 && Array.isArray(validationErrors) && validationErrors.length) {
      const first = validationErrors[0];
      message = `${message}：${(first.loc || []).join(".")} ${first.msg || ""}`.trim();
    }
    if (response.status === 409) {
      message = `状态冲突：${message}。页面将重新同步服务端状态。`;
    }
    throw new ApiError(message, {
      status: response.status,
      code: error.code || "",
      details: error.details || null,
      requestId: error.request_id || response.headers.get("x-request-id") || "",
    });
  }
  return body;
}

export function snapshotUrl(sessionId, { trace = false, hidden = false } = {}) {
  const params = new URLSearchParams({
    session_id: sessionId,
    include_trace: String(trace),
    include_hidden_truth: String(hidden),
  });
  return `/api/v1/debug/strategic/snapshot?${params.toString()}`;
}

export const strategicApi = {
  health: () => request("/health"),
  reset: () =>
    request("/api/v1/debug/strategic/reset", {
      method: "POST",
      body: "{}",
    }),
  snapshot: (sessionId, flags) => request(snapshotUrl(sessionId, flags)),
  command: (sessionId, command) =>
    request("/api/v1/debug/strategic/commands", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        command,
        idempotency_key: `browser-command-${crypto.randomUUID()}`,
      }),
    }),
  decision: (taskId, decisionId, sessionId, optionId) =>
    request(
      `/api/v1/debug/strategic/tasks/${taskId}/decisions/${decisionId}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, option_id: optionId }),
      }
    ),
  worldEvent: (taskId, operationId, sessionId) =>
    request(
      `/api/v1/debug/strategic/tasks/${taskId}/world-events/${operationId}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({
          session_id: sessionId,
          idempotency_key: `browser-world-${operationId}`,
        }),
      }
    ),
};
