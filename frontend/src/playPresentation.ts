export type PlayOperationKind = "goal" | "planning" | "replanning";

export type ActivePlayOperation = {
  kind: PlayOperationKind;
  taskId: string | null;
  startedAt: number;
};

export function formatDuration(durationMs: number | null | undefined): string | null {
  if (durationMs === null || durationMs === undefined || durationMs < 0) return null;
  return `${Math.max(1, Math.round(durationMs / 1000))}s`;
}

export function operationBelongsToTask(
  operation: ActivePlayOperation | null,
  taskId: string | null,
): boolean {
  return operation !== null && taskId !== null && operation.taskId === taskId;
}
