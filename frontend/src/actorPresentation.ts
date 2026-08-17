import type { PlayerGameState, PublicTask } from "./types";

export type ActorDisplayGroupKey = "active" | "planned" | "idle";
export type DisplayActor = PlayerGameState["actors"][number];
export type ActorDisplayGroup = {
  key: ActorDisplayGroupKey;
  label: string;
  actors: DisplayActor[];
};

const groupDefinitions: Array<Pick<ActorDisplayGroup, "key" | "label">> = [
  { key: "active", label: "行动中" },
  { key: "planned", label: "计划中" },
  { key: "idle", label: "待命中" },
];

const terminalPhases = new Set(["COMPLETED", "BLOCKED", "ABORTED"]);
const remainingStepStatuses = new Set(["PENDING", "CURRENT"]);
const actionPhases = new Set(["AWAITING_ACTION_ACK", "APPROVAL_REQUIRED"]);

export function groupActorsByTask(
  actors: DisplayActor[],
  task: PublicTask | null | undefined,
): ActorDisplayGroup[] {
  const activeActorName = currentActionActorName(task);
  const plannedActorNames = new Set(
    task?.plan?.steps
      .filter((step) => remainingStepStatuses.has(step.status))
      .map((step) => step.assigned_actor_name),
  );

  const groups = new Map<ActorDisplayGroupKey, DisplayActor[]>([
    ["active", []],
    ["planned", []],
    ["idle", []],
  ]);
  for (const actor of actors) {
    const key: ActorDisplayGroupKey =
      activeActorName === actor.name
        ? "active"
        : plannedActorNames.has(actor.name)
          ? "planned"
          : "idle";
    groups.get(key)?.push(actor);
  }

  return groupDefinitions
    .map((definition) => ({ ...definition, actors: groups.get(definition.key) ?? [] }))
    .filter((group) => group.actors.length > 0);
}

function currentActionActorName(task: PublicTask | null | undefined): string | null {
  if (!task || terminalPhases.has(task.execution_phase) || !task.plan) return null;
  if (actionPhases.has(task.execution_phase) && task.briefing?.actor_name) {
    return task.briefing.actor_name;
  }
  if (!actionPhases.has(task.execution_phase)) return null;
  return task.plan.steps.find((step) => step.status === "CURRENT")?.assigned_actor_name ?? null;
}
