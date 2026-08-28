import type { PlayerGameState } from "./types";

export type PlayOperationKind = "goal" | "planning" | "replanning";

export type ActivePlayOperation = {
  kind: PlayOperationKind;
  taskId: string | null;
  startedAt: number;
};

export function formatDuration(durationMs: number | null | undefined): string | null {
  if (durationMs === null || durationMs === undefined || durationMs < 0) return null;
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

export function operationBelongsToTask(
  operation: ActivePlayOperation | null,
  taskId: string | null,
): boolean {
  return operation !== null && taskId !== null && operation.taskId === taskId;
}

export function syncPlayStateCaches(
  setQueryData: (queryKey: readonly unknown[], state: PlayerGameState) => void,
  gameId: string,
  selectedTaskId: string | null,
  state: PlayerGameState,
): void {
  setQueryData(["play", gameId, "live"], state);
  const responseTaskId = state.current_task?.id ?? state.game.active_task_id;
  if (selectedTaskId === null || responseTaskId === selectedTaskId) {
    setQueryData(["play", gameId, selectedTaskId], state);
  }
}

export function segmentCompletionMessage(
  segmentCompleteDebrief: boolean,
  planInvalidated = false,
): { title: string; detail: string } | null {
  if (planInvalidated) {
    return {
      title: "最新信息使当前方案后续步骤不再有效，需要重新规划。",
      detail: "当前步骤已完成，请根据新获知识重新规划。",
    };
  }
  return segmentCompleteDebrief
    ? {
        title: "当前方案已完成，任务目标尚未达成。",
        detail: "可以根据最新信息继续规划。",
      }
    : null;
}

export function debriefButtonLabel({
  failureDebrief,
  segmentCompleteDebrief,
  planInvalidated,
  replanning,
}: {
  failureDebrief: boolean;
  segmentCompleteDebrief: boolean;
  planInvalidated: boolean;
  replanning: boolean;
}): string {
  if (replanning) return "正在重新规划……";
  if (!failureDebrief) return "收到，继续任务";
  if (planInvalidated) return "收到，继续规划任务";
  return segmentCompleteDebrief ? "收到，继续规划任务" : "没事，重新规划";
}
