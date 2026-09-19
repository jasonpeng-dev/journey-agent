import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { groupActorsByTask } from "../actorPresentation";
import { PresentationSettingsPanel } from "../components/PresentationSettingsPanel";
import { useForkGame } from "../hooks/useForkGame";
import {
  factDisplayLabel,
  factDisplayValue,
  facilityStatusDisplayValue,
  resourceDisplayName,
  resourceAvailabilityRequirementText,
  knownRelationDescription,
  meaningfulKnownRelations,
  publicFactIdentity,
  relationDisplayKey,
} from "../knowledgePresentation";
import type {
  ActionLocation,
  GoalSubmission,
  PublicPlanHistory,
  PublicPlanHistoryStep,
  PlayerGameState,
  PublicPlanDisplayStatus,
  PublicPlanningAttempt,
  PublicPlanningCycle,
  PublicResourceUsage,
  MissionRoadmapRequirement,
  MissionRoadmapStage,
  PublicProducerBinding,
  PublicTargetActionContract,
  ResourceIntelligence,
  PublicTask,
  PublicResolvedGoalDraft,
  PublicTimelineEvent,
  PresentationProfileDocument,
  PresentationProfileResponse,
} from "../types";
import {
  confirmGoalErrorText,
  errorText,
  goalSubmissionErrorText,
  resultLabel,
  stepDescription,
  taskExplanationLabel,
  uiLabel,
} from "../ui";
import {
  debriefButtonLabel,
  formatDuration,
  planningRefetchInterval,
  segmentCompletionMessage,
  syncPlayStateCaches,
  type ActivePlayOperation,
} from "../playPresentation";
import {
  buildFacilityDetailRows,
  buildFacilityResourceRows,
  buildTargetActionRequirementRows,
  mergeTargetActionContracts,
  publicResourceRequirementIdentity,
  publicResourceRequirementParts,
  resolveFacilityFactRole,
  targetKeyForPublicActionRequirement,
  uniquePublicResourceRequirements,
} from "../playFacilityPresentation";
import {
  actionLocationText,
  groupFactsByRegion,
  groupNodesByRegion,
  groupResourcesByRegion,
  meaningfulResult,
} from "../spatialPresentation";
import { legacyFactStateDisplayText } from "../legacyPresentationCompatibility";
import {
  clonePresentationProfile,
  PRESENTATION_PROFILE_REFRESH_INTERVAL_MS,
  resolvePresentationProfilePreview,
} from "../presentationPolicy";

const taskTone: Record<string, string> = {
  COMPLETED: "success",
  ACTIVE: "warning",
  NEEDS_PLAYER_INPUT: "warning",
  BLOCKED_BY_PLAYER_DECISION: "danger",
  UNREACHABLE_IN_CURRENT_STATE: "danger",
  ACTION_EXECUTION_FAILED: "danger",
  MODEL_PLAN_REJECTED: "danger",
  MODEL_PROVIDER_TIMEOUT: "danger",
  MODEL_PROVIDER_FAILURE: "danger",
  ABORTED: "neutral",
};

const planDisplayStatusLabel: Record<PublicPlanDisplayStatus, string> = {
  EXECUTING: "执行中",
  ADJUSTED: "已调整",
  STAGE_COMPLETED: "阶段完成",
  OBJECTIVE_COMPLETED: "目标完成",
  BLOCKED: "已阻塞",
};
const planStepMark: Record<PublicPlanHistoryStep["status"], string> = {
  PLANNED: "○",
  CURRENT: "→",
  COMPLETED: "✓",
  FAILED: "✕",
  CANCELLED: "○",
};
const timelinePresentation: Record<
  PublicTimelineEvent["kind"],
  { label: string; mark: string; tone: string }
> = {
  GOAL_ACCEPTED: { label: "目标已接受", mark: "🚩", tone: "goal-received" },
  PLAN_CREATED: { label: "计划已完成", mark: "✓", tone: "plan-event" },
  TASK_STARTED: { label: "目标已接受", mark: "🚩", tone: "goal-received" },
  ACTION_BRIEFING: { label: "下一步行动", mark: "令", tone: "current" },
  ACTION_RESULT: { label: "行动汇报", mark: "✓", tone: "completed" },
  PLAN_UPDATED: { label: "计划调整", mark: "↻", tone: "plan-event updated" },
  APPROVAL_REQUIRED: { label: "需要玩家决定", mark: "?", tone: "current" },
  APPROVAL_APPROVED: { label: "玩家已批准", mark: "✓", tone: "completed" },
  APPROVAL_REJECTED: { label: "玩家已拒绝", mark: "✕", tone: "failed" },
  TASK_COMPLETED: { label: "目标已完成", mark: "🚩", tone: "success" },
  TASK_BLOCKED: { label: "目标暂时无法推进", mark: "!", tone: "danger" },
  TASK_ABORTED: { label: "目标已放弃", mark: "·", tone: "neutral" },
};

export function ActionLocationLine({ location }: { location?: ActionLocation | null }) {
  const text = actionLocationText(location);
  if (!text) return null;
  return (
    <div className="action-location-line" data-testid="action-location">
      <strong>{text}</strong>
    </div>
  );
}

export function TaskGoalHeading({ goal, status }: { goal: string; status: string }) {
  return (
    <div className="task-goal-heading" data-testid="task-goal-heading">
      <strong>{goal}</strong>
      <span className={`console-pill ${taskTone[status] ?? "neutral"}`}>{uiLabel(status)}</span>
    </div>
  );
}

function planBeforeReplan(task: PublicTask, event: PublicTimelineEvent): PublicPlanHistory | null {
  if (event.kind !== "PLAN_UPDATED") return null;
  // New timeline events carry their PlanningCycle identity.  They must not
  // infer a source Plan by ordinal when the cycle has no accepted Plan.
  if (event.planning_cycle_id) return null;
  const updatedPlanId = event.id.startsWith("plan:") ? event.id.slice("plan:".length) : null;
  const updatedPlan = updatedPlanId
    ? task.plan_history.find((plan) => plan.id === updatedPlanId)
    : undefined;
  const updatedOrdinal = updatedPlan?.ordinal ?? (
    task.timeline
      .filter((item) => item.kind === "PLAN_UPDATED")
      .findIndex((item) => item.id === event.id) + 2
  );
  if (updatedOrdinal < 2) return null;
  return task.plan_history.find((plan) => plan.ordinal === updatedOrdinal - 1) ?? null;
}

function replanReason(task: PublicTask, event: PublicTimelineEvent): string | null {
  const sourcePlan = planBeforeReplan(task, event);
  if (!sourcePlan) return null;
  const interruption = sourcePlan.interruption;
  const stepName = interruption?.step_name ?? sourcePlan.failed_step_name;
  if (!stepName) return null;
  const reasonType = interruption?.kind === "KNOWLEDGE_CONFLICT" ? "冲突" : "失败";
  return `原因：${stepName} ${reasonType}`;
}

function interruptionMarkerSequence(plan: PublicPlanHistory): number | null {
  const interruption = plan.interruption;
  if (!interruption) return null;
  if (interruption.kind === "FAILURE") return interruption.sequence;
  const trigger = plan.steps
    .filter((step) => step.sequence < interruption.sequence && step.status === "COMPLETED")
    .sort((left, right) => left.sequence - right.sequence)
    .at(-1);
  return trigger?.sequence ?? null;
}

type MissionRoadmapNames = {
  regionNames?: Record<string, string>;
  resourceNames?: Record<string, string>;
  nodeNames?: Record<string, string>;
  factNames?: Record<string, string>;
  factValues?: Record<string, string | number | boolean>;
  factSafeMetadata?: Record<string, boolean>;
  factValueLabels?: Record<string, string>;
};

function taskObjectiveLabel(goal: string, objectiveNames: string[]): string {
  return objectiveNames.length > 0 ? objectiveNames.join(" · ") : goal;
}

function derivedStateDisplayValue(requirement: MissionRoadmapRequirement): string {
  if (requirement.knowledge_status === "UNKNOWN" || requirement.current_known_value == null) {
    return "未知";
  }
  if (requirement.current_known_value === "AVAILABLE" || requirement.current_known_value === true) {
    return "可用";
  }
  if (requirement.current_known_value === "UNAVAILABLE" || requirement.current_known_value === false) {
    return "不可用";
  }
  return String(requirement.current_known_value);
}

function factStateDisplayText(
  factName: string,
  currentValue: string | number | boolean | undefined,
  acceptedValues: Array<string | number | boolean>,
  safeMetadata = false,
  currentValueLabel?: string,
): string {
  const stateName = factName === "目标状态" ? "状态" : factName;
  if (!safeMetadata) {
    const legacy = legacyFactStateDisplayText(factName, currentValue, acceptedValues);
    if (legacy) return legacy;
  }
  if (currentValueLabel) return currentValueLabel;
  if (currentValue === true) {
    return `${stateName}已达到目标状态`;
  }
  if (currentValue === false) {
    return `${stateName}尚未达到目标状态`;
  }
  if (currentValue !== undefined && currentValue !== null) {
    return `${stateName}：${uiLabel(String(currentValue))}`;
  }
  if (acceptedValues.some((value) => value === currentValue)) return `${stateName}已达到目标状态`;
  return `${stateName}待确认`;
}

function publicActionParameterResources(
  requirement: MissionRoadmapRequirement,
): Array<{ resourceKey: string; amount: number }> {
  const parameters = requirement.parameter_constraints;
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) return [];
  const rawResources = parameters.resources;
  if (Array.isArray(rawResources)) {
    return rawResources.flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
      const resource = item as Record<string, unknown>;
      return typeof resource.resource_key === "string" && typeof resource.amount === "number"
        ? [{ resourceKey: resource.resource_key, amount: resource.amount }]
        : [];
    });
  }
  return typeof parameters.resource_key === "string" && typeof parameters.amount === "number"
    ? [{ resourceKey: parameters.resource_key, amount: parameters.amount }]
    : [];
}

function publicActionCompletedRequirementText(
  requirement: MissionRoadmapRequirement,
  names: MissionRoadmapNames,
): string | null {
  if (requirement.kind !== "ACTION_COMPLETED" && !requirement.action_key) return null;
  const actionName = requirement.action_name ?? "执行操作";
  const targetName = requirement.target_key
    ? requirement.target_name
      ?? names.nodeNames?.[requirement.target_key]
      ?? names.regionNames?.[requirement.target_key]
    : undefined;
  const sourceBinding = (requirement.binding_constraints ?? []).find(
    (binding) => binding.role === "source_region" && typeof binding.value === "string",
  );
  const sourceKey = typeof sourceBinding?.value === "string" ? sourceBinding.value : undefined;
  const sourceName = sourceKey
    ? names.regionNames?.[sourceKey] ?? names.nodeNames?.[sourceKey]
    : undefined;
  const resourceLines = publicActionParameterResources(requirement).map(({ resourceKey, amount }) => (
    `${resourceDisplayName(resourceKey, names.resourceNames?.[resourceKey])} ×${amount}`
  ));
  const actorPrefix = requirement.actor_name ? `由${requirement.actor_name} ` : "";
  if (sourceName && targetName && resourceLines.length > 0) {
    return `要求操作：${actorPrefix}${actionName}：从${sourceName}到${targetName}，${resourceLines.join("、")}`;
  }
  const details = [
    sourceName ? `起点：${sourceName}` : null,
    targetName ? `目标：${targetName}` : null,
    resourceLines.length > 0 ? `资源：${resourceLines.join("、")}` : null,
  ].filter((value): value is string => value !== null);
  return `要求操作：${actorPrefix}${actionName}${details.length > 0 ? `：${details.join("，")}` : ""}`;
}

function missionRoadmapRequirementText(
  requirement: MissionRoadmapRequirement,
  names: MissionRoadmapNames,
): string {
  if (requirement.kind === "DERIVED_STATE") {
    return `世界能力：当前${derivedStateDisplayValue(requirement)}`;
  }

  const actionText = publicActionCompletedRequirementText(requirement, names);
  if (actionText !== null) return actionText;

  if (
    requirement.kind === "RESOURCE_AT_LEAST"
    && requirement.region_key
    && requirement.resource_key
    && typeof requirement.minimum === "number"
  ) {
    const regionName = names.regionNames?.[requirement.region_key] ?? "相关区域";
    const resourceName = resourceDisplayName(
      requirement.resource_key,
      names.resourceNames?.[requirement.resource_key],
    );
    const current = requirement.knowledge_status === "UNKNOWN"
      ? "未知"
      : String(requirement.current_known_available ?? 0);
    return `${regionName}：${resourceName}储备 ${current} / ${requirement.minimum}`;
  }

  if (requirement.fact_key) {
    const accepted = requirement.accepted_values ?? [];
    const targetName = requirement.node_key
      ? names.nodeNames?.[requirement.node_key] ?? "目标设施"
      : "目标设施";
    const factName = requirement.node_key
      ? names.factNames?.[publicFactIdentity(requirement.node_key, requirement.fact_key)] ?? "目标状态"
      : "目标状态";
    const currentValue = requirement.node_key
      ? names.factValues?.[publicFactIdentity(requirement.node_key, requirement.fact_key)]
      : undefined;
    const stateText = factStateDisplayText(
      factName,
      currentValue,
      accepted,
      names.factSafeMetadata?.[publicFactIdentity(requirement.node_key ?? "", requirement.fact_key)] ?? false,
      names.factValueLabels?.[publicFactIdentity(requirement.node_key ?? "", requirement.fact_key)],
    );
    return `${targetName}：${stateText}`;
  }

  return requirement.description;
}

type MissionRoadmapRequirementKind =
  | "FACT"
  | "RESOURCE_AT_LEAST"
  | "DERIVED_STATE"
  | "ACTION_COMPLETED";

const MISSION_ROADMAP_REQUIREMENT_KIND_LABELS: Record<MissionRoadmapRequirementKind, string> = {
  FACT: "状态要求",
  RESOURCE_AT_LEAST: "资源要求",
  DERIVED_STATE: "综合状态要求",
  ACTION_COMPLETED: "操作要求",
};

function missionRoadmapRequirementKind(
  requirement: MissionRoadmapRequirement,
): MissionRoadmapRequirementKind {
  if (requirement.kind) return requirement.kind;
  if (requirement.action_key) return "ACTION_COMPLETED";
  if (requirement.derived_key) return "DERIVED_STATE";
  if (
    requirement.region_key
    && requirement.resource_key
    && typeof requirement.minimum === "number"
  ) {
    return "RESOURCE_AT_LEAST";
  }
  return "FACT";
}

function missionRoadmapRequirementKindLabel(stages: MissionRoadmapStage[]): string {
  const kinds: MissionRoadmapRequirementKind[] = [];
  stages.forEach((stage) => {
    const stageKinds: MissionRoadmapRequirementKind[] = stage.requirements.map(
      missionRoadmapRequirementKind,
    );
    // A Derived State is the public heading for its expanded children.  Do
    // not turn those children into a second top-level requirement kind.
    const visibleStageKinds: MissionRoadmapRequirementKind[] = stageKinds.includes("DERIVED_STATE")
      ? ["DERIVED_STATE"]
      : stageKinds;
    visibleStageKinds.forEach((kind) => {
      if (!kinds.includes(kind)) kinds.push(kind);
    });
  });
  const displayedKinds: MissionRoadmapRequirementKind[] = kinds.length > 0 ? kinds : ["FACT"];
  return displayedKinds.map(
    (kind) => MISSION_ROADMAP_REQUIREMENT_KIND_LABELS[kind],
  ).join(" · ");
}

function publicFactExpectedValue(
  factKey: string,
  factName: string,
  value: string | number | boolean,
): string {
  return factDisplayValue({
    node_key: "",
    fact_key: factKey,
    name: factName,
    value,
  }, value);
}

function publicFactRequirementStatus(
  requirement: MissionRoadmapRequirement,
  names: MissionRoadmapNames,
): string {
  const currentValue = requirement.node_key
    ? names.factValues?.[publicFactIdentity(requirement.node_key, requirement.fact_key ?? "")]
      ?? requirement.current_known_value
    : requirement.current_known_value;
  if (
    requirement.knowledge_status === "UNKNOWN"
    || currentValue === undefined
    || currentValue === null
    || currentValue === "UNKNOWN"
  ) {
    return "未知";
  }
  return (requirement.accepted_values ?? []).some((value) => value === currentValue)
    ? "已满足"
    : "未满足";
}

type MissionRoadmapDisplayRow =
  | {
    kind: MissionRoadmapRequirementKind;
    type: "status";
    primary: string;
    status: string;
    tone: "success" | "warning";
  }
  | {
    kind: MissionRoadmapRequirementKind;
    type: "field";
    label: string;
    value: string;
  }
  | {
    kind: MissionRoadmapRequirementKind;
    type: "text";
    primary: string;
  };

function publicFactRequirementRow(
  requirement: MissionRoadmapRequirement,
  names: MissionRoadmapNames,
): MissionRoadmapDisplayRow {
  const nodeName = requirement.node_key
    ? names.nodeNames?.[requirement.node_key] ?? "相关对象"
    : "相关对象";
  const acceptedValues = requirement.accepted_values ?? [];
  const factKey = requirement.fact_key ?? "";
  const factName = requirement.node_key && requirement.fact_key
    ? names.factNames?.[publicFactIdentity(requirement.node_key, requirement.fact_key)]
      ?? factDisplayLabel({
        node_key: requirement.node_key,
        fact_key: requirement.fact_key,
        name: "",
        value: acceptedValues[0] ?? requirement.current_known_value ?? "",
      })
    : "目标状态";
  const expected = acceptedValues.length > 0
    ? acceptedValues.map((value) => publicFactExpectedValue(factKey, factName, value)).join(" 或 ")
    : "目标状态";
  const status = publicFactRequirementStatus(requirement, names);
  return {
    kind: "FACT",
    type: "status",
    primary: `${nodeName} · ${factName}：${expected}`,
    status,
    tone: status === "已满足" ? "success" : "warning",
  };
}

function publicResourceRequirementRow(
  requirement: MissionRoadmapRequirement,
  names: MissionRoadmapNames,
): MissionRoadmapDisplayRow {
  const regionName = requirement.region_key
    ? names.regionNames?.[requirement.region_key]
      ?? names.nodeNames?.[requirement.region_key]
      ?? "相关区域"
    : "相关区域";
  const resourceName = requirement.resource_key
    ? resourceDisplayName(
      requirement.resource_key,
      names.resourceNames?.[requirement.resource_key],
    )
    : "相关资源";
  const current = requirement.knowledge_status !== "UNKNOWN"
    && typeof requirement.current_known_available === "number"
    ? requirement.current_known_available
    : null;
  const minimum = requirement.minimum ?? 0;
  return {
    kind: "RESOURCE_AT_LEAST",
    type: "status",
    primary: `${regionName} · ${resourceName} ≥ ${requirement.minimum ?? "—"}`,
    status: current === null ? "未知" : `${current} / ${minimum}`,
    tone: current !== null && current >= minimum ? "success" : "warning",
  };
}

function publicActionRequirementRows(
  requirement: MissionRoadmapRequirement,
  names: MissionRoadmapNames,
): MissionRoadmapDisplayRow[] {
  const rows: MissionRoadmapDisplayRow[] = [{
    kind: "ACTION_COMPLETED",
    type: "field",
    label: "操作",
    value: requirement.action_name ?? "执行操作",
  }];
  const sourceBinding = (requirement.binding_constraints ?? []).find(
    (binding) => binding.role === "source_region" && typeof binding.value === "string",
  );
  const sourceKey = typeof sourceBinding?.value === "string" ? sourceBinding.value : undefined;
  const sourceName = sourceKey
    ? names.regionNames?.[sourceKey] ?? names.nodeNames?.[sourceKey]
    : undefined;
  const targetName = requirement.target_key
    ? requirement.target_name
      ?? names.nodeNames?.[requirement.target_key]
      ?? names.regionNames?.[requirement.target_key]
    : undefined;
  if (sourceName) rows.push({ kind: "ACTION_COMPLETED", type: "field", label: "起点", value: sourceName });
  if (targetName) rows.push({ kind: "ACTION_COMPLETED", type: "field", label: "目标", value: targetName });

  const resources = publicActionParameterResources(requirement);
  if (resources.length > 0) {
    rows.push({
      kind: "ACTION_COMPLETED",
      type: "field",
      label: "资源",
      value: resources.map(({ resourceKey }) => resourceDisplayName(
        resourceKey,
        names.resourceNames?.[resourceKey],
      )).join("、"),
    });
    rows.push({
      kind: "ACTION_COMPLETED",
      type: "field",
      label: "数量",
      value: resources.map(({ amount }) => String(amount)).join("、"),
    });
  }
  if (requirement.actor_name) {
    rows.push({ kind: "ACTION_COMPLETED", type: "field", label: "执行者", value: requirement.actor_name });
  }
  return rows;
}

function missionRoadmapRequirementRows(
  requirement: MissionRoadmapRequirement,
  names: MissionRoadmapNames,
): MissionRoadmapDisplayRow[] {
  switch (missionRoadmapRequirementKind(requirement)) {
    case "DERIVED_STATE":
      return [];
    case "ACTION_COMPLETED":
      return publicActionRequirementRows(requirement, names);
    case "RESOURCE_AT_LEAST":
      return [publicResourceRequirementRow(requirement, names)];
    case "FACT":
      return requirement.fact_key
        ? [publicFactRequirementRow(requirement, names)]
        : [{
          kind: "FACT",
          type: "text",
          primary: missionRoadmapRequirementText(requirement, names),
        }];
  }
}

export function MissionRoadmap({
  stages,
  presentation,
  regionNames,
  resourceNames,
  nodeNames,
  factNames,
  factValues,
  factSafeMetadata,
  factValueLabels,
}: {
  stages: MissionRoadmapStage[];
  presentation?: PlayerGameState["presentation"];
  summary?: string;
  regionNames?: Record<string, string>;
  resourceNames?: Record<string, string>;
  nodeNames?: Record<string, string>;
  factNames?: Record<string, string>;
  factValues?: Record<string, string | number | boolean>;
  factSafeMetadata?: Record<string, boolean>;
  factValueLabels?: Record<string, string>;
}) {
  const defaultOpen = presentation?.default_open === "FULL";
  const [detailsOpen, setDetailsOpen] = useState(defaultOpen);
  useEffect(() => {
    setDetailsOpen(defaultOpen);
  }, [defaultOpen, presentation?.revision]);
  if (stages.length === 0) return null;
  return (
    <div className="mission-roadmap-details">
      <button
        type="button"
        className="mission-roadmap-toggle"
        aria-expanded={detailsOpen}
        onClick={() => setDetailsOpen((current) => !current)}
      >
        <span className="mission-roadmap-toggle-summary">
          {missionRoadmapRequirementKindLabel(stages)}
        </span>
        <span className="mission-roadmap-toggle-label">{detailsOpen ? "收起详情" : "查看详情"}</span>
      </button>
      {detailsOpen && (
        <ol className="mission-roadmap" aria-label="任务路线图">
          {stages.flatMap((stage) => {
            const rows = stage.requirements.flatMap((requirement) => (
              missionRoadmapRequirementRows(requirement, {
                regionNames,
                resourceNames,
                nodeNames,
                factNames,
                factValues,
                factSafeMetadata,
                factValueLabels,
              })
            ));
            const visibleRows = presentation?.roadmap_detail === "SUMMARY"
              ? rows.filter((row) => row.type !== "field").slice(0, 3)
              : rows;
            const safeRows = visibleRows.length > 0 ? visibleRows : rows.slice(0, 1);
            if (safeRows.length === 0) return [];
            return [(
              <li key={stage.key} className={stage.status.toLowerCase()}>
                <div className="mission-roadmap-row-list">
                  {safeRows.map((row, index) => {
                    if (row.type === "field") {
                      return (
                        <div
                          className="mission-roadmap-action-row"
                          key={`${stage.key}:${row.kind}:${row.label}:${index}`}
                          data-requirement-kind={row.kind}
                        >
                          <span className="mission-roadmap-action-label">{row.label}</span>
                          <span className="mission-roadmap-action-value">{row.value}</span>
                        </div>
                      );
                    }
                    return (
                      <div
                        className={row.type === "status" ? "mission-roadmap-row" : "mission-roadmap-text-row"}
                        key={`${stage.key}:${row.kind}:${index}`}
                        data-requirement-kind={row.kind}
                      >
                        <span className="mission-roadmap-row-primary">{row.primary}</span>
                        {row.type === "status" && (
                          <span className={`mission-roadmap-status-badge ${row.tone}`}>
                            {row.status}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </li>
            )];
          })}
        </ol>
      )}
    </div>
  );
}

function planningHeadline(
  event: PublicTimelineEvent,
  cycle: PublicPlanningCycle | null,
  acceptedPlan: PublicPlanHistory | null,
): string {
  if (cycle) {
    const status = cycle.status.toUpperCase();
    if (status === "RUNNING") return "Agent 正在规划";
    if (status === "ACCEPTED") {
      if (cycle.cycle_type === "INITIAL") {
        return `Agent 已完成计划${acceptedPlan ? ` · 执行方案 ${acceptedPlan.ordinal}` : ""}`;
      }
      return `Agent 已重新规划${acceptedPlan ? ` · 执行方案 ${acceptedPlan.ordinal}` : ""}`;
    }
    return "Agent 未能完成计划";
  }
  const isApproval = event.kind.startsWith("APPROVAL_");
  if (isApproval) return stepDescription(event.title);
  if (event.kind === "GOAL_ACCEPTED") return "任务已接受";
  if (event.kind.startsWith("PLAN_") && event.success === false) return "Agent 未能完成计划";
  if (event.kind === "PLAN_CREATED") return "Agent 已完成计划";
  if (event.kind === "PLAN_UPDATED") return "Agent 已重新规划";
  if (event.kind === "TASK_STARTED") return "任务已接受";
  if (event.kind.startsWith("TASK_")) return timelinePresentation[event.kind].label;
  return event.title;
}

function planningAttemptPresentation(
  cycle: PublicPlanningCycle,
  attempt: PublicPlanningAttempt,
  index: number,
): { status: string; detail: string } {
  if (attempt.status === "RUNNING") {
    return { status: "规划中", detail: "正在生成执行方案…" };
  }
  if (attempt.status === "ACCEPTED") {
    const count = attempt.accepted_step_count;
    return {
      status: "已完成",
      detail: count > 0 ? `已生成 ${count} 步执行方案` : "已生成执行方案",
    };
  }
  if (attempt.status === "REJECTED" && index < cycle.attempts.length - 1) {
    return { status: "需要调整", detail: "当前方案需要调整" };
  }
  if (attempt.status === "REJECTED") {
    return { status: "未能完成", detail: "多次尝试后仍未生成可执行方案" };
  }
  if (index < cycle.attempts.length - 1) {
    return { status: "未能完成", detail: "本次规划未能完成" };
  }
  return {
    status: "未能完成",
    detail: "规划服务未能完成本次规划",
  };
}

function planDisplayStatus(plan: PublicPlanHistory): PublicPlanDisplayStatus {
  if (plan.display_status) return plan.display_status;
  if (plan.status === "EXECUTING") return "EXECUTING";
  if (plan.status === "ADJUSTED") return "ADJUSTED";
  if (plan.status === "BLOCKED") return "BLOCKED";
  return "OBJECTIVE_COMPLETED";
}

function resourceUsageText(
  usage: PublicResourceUsage[] | undefined,
  kind: PublicPlanHistoryStep["resource_usage_kind"] | PublicTimelineEvent["resource_usage_kind"],
  {transportLabel = true}: {transportLabel?: boolean} = {},
): string | null {
  if (!usage || usage.length === 0) return null;
  const prefix = kind === "TRANSPORT" && transportLabel ? "运输：" : kind === "CONSUME" ? "消耗：" : "";
  return prefix + usage.map((item) => `${item.resource_name} ×${item.amount}`).join(" · ");
}

function locationWithoutResourceDetail(
  location: ActionLocation | null | undefined,
  usage: PublicResourceUsage[] | undefined,
): ActionLocation | null | undefined {
  if (!location || !usage || usage.length === 0 || !location.detail) return location;
  return { ...location, detail: null };
}

function PlanStepLocation({ step }: { step: PublicPlanHistoryStep }) {
  const usage = step.resource_usage ?? [];
  const resourceText = resourceUsageText(usage, step.resource_usage_kind);
  if (!resourceText) return <ActionLocationLine location={step.location} />;
  const location = locationWithoutResourceDetail(step.location, usage);
  if (usage.length <= 2) {
    if (location) {
      return (
        <ActionLocationLine
          location={{
            ...location,
            detail: [location.detail, resourceText].filter(Boolean).join(" · ") || null,
          }}
        />
      );
    }
    return <p className="plan-resource-line">{resourceText}</p>;
  }
  return (
    <>
      <ActionLocationLine location={location} />
      <p className="plan-resource-line plan-resource-line--stacked">{resourceText}</p>
    </>
  );
}

function timelineResultText(event: PublicTimelineEvent): string | null {
  const usageText = resourceUsageText(event.resource_usage, event.resource_usage_kind, {
    transportLabel: false,
  });
  const result = meaningfulResult(event.result_summary);
  if (!usageText && !result) return null;
  const status = resultLabel(result) ?? (
    event.resource_usage_kind === "TRANSPORT" ? "资源已运输" : "资源已消耗"
  );
  return [status, usageText].filter(Boolean).join(" · ");
}

type PlanDisplayState = "COLLAPSED" | "COMPACT" | "FULL";

function defaultPlanDisplayState(
  plan: PublicPlanHistory,
  latestId: string | null,
  profileDefault: "COLLAPSED" | "COMPACT" | "FULL" | undefined,
): PlanDisplayState {
  if (profileDefault === "COLLAPSED") return "COLLAPSED";
  if (profileDefault === "FULL") return "FULL";
  if (profileDefault === "COMPACT") return plan.total_steps >= 6 ? "COMPACT" : "FULL";
  return plan.total_steps >= 6 && plan.id === latestId
    ? "COMPACT"
    : plan.id === latestId ? "FULL" : "COLLAPSED";
}

function PlanningCycleDetails({ cycle }: { cycle: PublicPlanningCycle }) {
  const [open, setOpen] = useState(false);
  return (
    <section
      className={`planning-cycle-details ${cycle.status.toLowerCase()}`}
      data-testid={`planning-cycle-${cycle.id}`}
    >
      <button
        className="planning-details-toggle"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span>{open ? "▾ 收起规划详情" : "▸ 查看规划详情"}</span>
      </button>
      {open && (
        <div className="planning-cycle-detail-body">
          {cycle.attempts.length === 0 ? (
            <p className="planning-attempts-empty">无可用规划明细</p>
          ) : (
            <ol className="planning-attempts">
              {cycle.attempts.map((attempt, index) => {
                const attemptDuration = formatDuration(attempt.duration_ms);
                const presentation = planningAttemptPresentation(cycle, attempt, index);
                return (
                  <li
                    className={`planning-attempt ${attempt.status.toLowerCase()}`}
                    data-testid={`planning-attempt-${cycle.id}-${attempt.attempt_index}`}
                    key={`${cycle.id}:${attempt.attempt_index}`}
                  >
                    <div className="planning-attempt-heading">
                      <strong>
                        第 {index + 1} 次尝试
                        {" · "}
                        {presentation.status}
                      </strong>
                      {attemptDuration && <small>{attemptDuration}</small>}
                    </div>
                    <p>{presentation.detail}</p>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

export function Timeline({
  task,
  presentation,
}: {
  task: PublicTask | null;
  presentation?: PlayerGameState["presentation"];
}) {
  const timelineRef = useRef<HTMLDivElement>(null);
  const timelineDensity = presentation?.timeline_density ?? "DETAILED";
  useEffect(() => {
    const element = timelineRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [task?.id, task?.timeline.length]);
  if (!task) {
    return (
      <div className="console-welcome">
        <span>令</span>
        <div>
          <strong>任务日志已准备就绪</strong>
          <p>下达一个高层目标后，这里会记录已经发生的玩家可见事件。</p>
        </div>
      </div>
    );
  }
  return (
    <div
      ref={timelineRef}
      className={`mission-timeline timeline-density-${timelineDensity.toLowerCase()}`}
      aria-live="polite"
    >
      {task.timeline.map((event) => {
        const base = timelinePresentation[event.kind];
        const presentation =
          ((event.kind === "ACTION_RESULT" || event.kind.startsWith("PLAN_")) && event.success === false)
            ? { ...base, mark: "✕", tone: "failed" }
            : base;
        const isApproval = event.kind.startsWith("APPROVAL_");
        const eventLabel = (event.kind === "GOAL_ACCEPTED" || event.kind === "TASK_STARTED")
          ? "任务状态"
          : event.kind.startsWith("PLAN_")
          ? "执行方案"
          : event.kind.startsWith("TASK_")
            ? "任务状态"
              : isApproval
                ? "玩家决定"
                : presentation.label;
        const planningCycle = event.planning_cycle_id
          ? (task.planning_process ?? []).find((cycle) => cycle.id === event.planning_cycle_id) ?? null
          : null;
        const acceptedPlan = planningCycle?.status.toUpperCase() === "ACCEPTED"
          ? task.plan_history.find((plan) => plan.planning_cycle_id === planningCycle.id) ?? null
          : null;
        const headline = planningHeadline(event, planningCycle, acceptedPlan);
        const planReason = replanReason(task, event);
        const planDuration = formatDuration(
          planningCycle?.wall_clock_duration_ms ?? event.duration_ms,
        );
        const eventLocation = locationWithoutResourceDetail(event.location, event.resource_usage);
        const actionResultText = timelineResultText(event);
        return (
          <article className={`timeline-entry ${presentation.tone}`} key={event.id}>
            <span className="timeline-mark">{presentation.mark}</span>
            <div className="timeline-entry-content">
              <div className="timeline-entry-heading">
                <small>
                  {eventLabel}
                  {event.actor_name ? ` · ${event.actor_name}` : ""}
                </small>
                {planningCycle ? (
                  planDuration && (
                  <div className="timeline-plan-meta">
                    <small className="timeline-duration">· {planDuration}</small>
                  </div>
                  )
                ) : planDuration ? (
                  <small className="timeline-duration">· {planDuration}</small>
                ) : null}
              </div>
              {planningCycle ? (
                <div className="timeline-plan-headline">
                  <strong>
                    {headline}
                    {event.kind === "ACTION_RESULT" && actionLocationText(eventLocation)
                      ? ` · ${actionLocationText(eventLocation)}`
                      : ""}
                  </strong>
                  <small className="timeline-attempt-count">{planningCycle.attempt_count} 次尝试</small>
                </div>
              ) : (
                <strong>
                  {headline}
                  {event.kind === "ACTION_RESULT" && actionLocationText(eventLocation)
                    ? ` · ${actionLocationText(eventLocation)}`
                    : ""}
                </strong>
              )}
              {planReason && <p className="timeline-plan-reason">{planReason}</p>}
              {planningCycle && timelineDensity !== "COMPACT" && <PlanningCycleDetails cycle={planningCycle} />}
              {event.kind !== "ACTION_RESULT" && <ActionLocationLine location={eventLocation} />}
              {event.detail && timelineDensity !== "COMPACT" && !event.kind.startsWith("PLAN_") && (
                <p>
                  说明：
                  {uiLabel(event.detail)}
                </p>
              )}
              {!event.kind.startsWith("PLAN_") && actionResultText && (
                <p className="timeline-action-result">{actionResultText}</p>
              )}
              {timelineDensity !== "COMPACT" && !event.kind.startsWith("PLAN_") && event.knowledge_changes.length > 0 && (
                <ul className="knowledge-gains">
                  {event.knowledge_changes.map((change) => (
                    <li key={`${event.id}:${change.key}`}>
                      {change.name}
                      {change.value !== null
                        ? `：${typeof change.value === "string" ? uiLabel(change.value) : String(change.value)}`
                        : ""}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function PlanHistory({
  task,
  presentation,
}: {
  task: PublicTask;
  presentation?: PlayerGameState["presentation"];
}) {
  const latestId = task.plan_history.at(-1)?.id ?? null;
  const [displayStates, setDisplayStates] = useState<Record<string, PlanDisplayState>>({});
  const initializedTaskId = useRef<string | null>(null);
  useEffect(() => {
    setDisplayStates((current) => {
      const next = initializedTaskId.current === task.id ? { ...current } : {};
      initializedTaskId.current = task.id;
      task.plan_history.forEach((plan) => {
        if (!next[plan.id]) {
          next[plan.id] = defaultPlanDisplayState(
            plan,
            latestId,
            presentation?.plan_default,
          );
        }
      });
      return next;
    });
  }, [latestId, presentation?.plan_default, task.id, task.plan_history]);
  if (!task.plan_history.length) return <p className="console-empty">尚未生成执行方案。</p>;
  return (
    <div className="plan-history">
      {task.plan_history.map((plan) => {
        const isLongPlan = plan.total_steps >= 6;
        const storedState = displayStates[plan.id];
        const state = !isLongPlan && storedState === "COMPACT"
          ? "FULL"
          : storedState ?? defaultPlanDisplayState(plan, latestId, presentation?.plan_default);
        const open = state !== "COLLAPSED";
        const markerSequence = interruptionMarkerSequence(plan);
        const interruption = plan.interruption;
        const displayStatus = planDisplayStatus(plan);
        const reason = plan.display_reason ?? (
          plan.interruption
            ? `${plan.interruption.step_name} ${
                plan.interruption.kind === "KNOWLEDGE_CONFLICT" ? "冲突" : "失败"
              }`
            : plan.failed_step_name
              ? `${plan.failed_step_name} 失败`
              : null
        );
        const steps = state === "COLLAPSED" ? [] : plan.steps;
        const toggleLabel = state === "COLLAPSED" ? "展开" : state === "COMPACT" ? "展开全部" : "收起";
        return (
          <section
            className={`plan-history-card ${displayStatus.toLowerCase()} ${state.toLowerCase()}`}
            key={plan.id}
          >
            <button
              className="plan-history-toggle"
              type="button"
              aria-expanded={open}
              onClick={() =>
                setDisplayStates((current) => {
                    const currentState = current[plan.id] ?? state;
                  const nextState = !isLongPlan
                    ? currentState === "FULL" ? "COLLAPSED" : "FULL"
                    : currentState === "COLLAPSED"
                      ? "COMPACT"
                      : currentState === "COMPACT"
                        ? "FULL"
                        : "COLLAPSED";
                  return { ...current, [plan.id]: nextState };
                })
              }
            >
              <span className="plan-history-heading">
                <strong>执行方案 {plan.ordinal} · {planDisplayStatusLabel[displayStatus]}</strong>
                <small>
                  {plan.completed_steps}/{plan.total_steps} 完成{reason ? ` · ${reason}` : ""}
                </small>
              </span>
              <span className="plan-history-meta">
                <b>{toggleLabel}</b>
              </span>
            </button>
            {open && (
              <div className={isLongPlan && state === "COMPACT" ? "plan-history-step-viewport compact" : undefined}>
                <ol className="plan-history-steps">
                  {steps.map((step) => (
                    <Fragment key={step.id}>
                      <li className={step.status.toLowerCase()}>
                        <b>{planStepMark[step.status]}</b>
                        <div>
                          <strong>{step.assigned_actor_name} · {step.action_name}</strong>
                          {step.subtitle && (
                            <div className="action-location-line plan-step-subtitle">
                              <strong>{step.subtitle}</strong>
                            </div>
                          )}
                          <PlanStepLocation step={step} />
                          {meaningfulResult(step.result_summary) && (
                            <p>{resultLabel(meaningfulResult(step.result_summary))}</p>
                          )}
                        </div>
                      </li>
                      {markerSequence === step.sequence && interruption && (
                        <li className="plan-interruption">
                          <b>!</b>
                          <div>
                            <strong>计划已中断</strong>
                            <p>
                              原因：{interruption.step_name}{" "}
                              {interruption.kind === "KNOWLEDGE_CONFLICT" ? "冲突" : "失败"}
                            </p>
                          </div>
                        </li>
                      )}
                    </Fragment>
                  ))}
                </ol>
              </div>
            )}
            {!open && (reason || task.execution_phase === "APPROVAL_REQUIRED") && (
              <p className="plan-safety-summary" data-testid={`plan-safety-${plan.id}`}>
                {task.execution_phase === "APPROVAL_REQUIRED" ? "需要玩家批准" : reason}
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}

export function WaitingStatus({
  startedAt,
  label,
  testId,
}: {
  startedAt: number;
  label: string;
  testId: string;
}) {
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    setNow(startedAt);
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  const elapsed = Math.floor(Math.max(0, now - startedAt) / 1000);
  return (
    <div className="play-waiting-status" data-testid={testId} role="status" aria-live="polite">
      <span>{label}</span>
      <strong>· {elapsed}s</strong>
    </div>
  );
}

export function ActionExecutionControls({
  disabled,
  starting,
  continuousExecuting,
  onStart,
  onContinuous,
}: {
  disabled: boolean;
  starting: boolean;
  continuousExecuting: boolean;
  onStart: () => void;
  onContinuous: () => void;
}) {
  return (
    <>
      <div className="action-execution-controls">
        <button type="button" disabled={disabled} onClick={onStart}>
          {starting ? "正在执行……" : "知悉，开始执行"}
        </button>
        <button
          type="button"
          data-testid="continuous-execution-button"
          disabled={disabled}
          onClick={onContinuous}
        >
          连续执行
        </button>
      </div>
      {continuousExecuting && (
        <span className="continuous-execution-status" data-testid="continuous-execution-status" role="status">
          执行中…
        </span>
      )}
    </>
  );
}

type KnowledgeAccordionKey = "resources" | "locations" | "actors" | "facts" | "relations";

type KnowledgeAccordionProps = {
  id: KnowledgeAccordionKey;
  title: string;
  count: number;
  countLabel?: string;
  summary: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
};

export function KnowledgeAccordion({
  id,
  title,
  count,
  countLabel,
  summary,
  open,
  onToggle,
  children,
}: KnowledgeAccordionProps) {
  const contentId = `known-world-${id}-content`;
  return (
    <section className="knowledge-accordion" data-testid={`knowledge-accordion-${id}`}>
      <button
        className="knowledge-accordion-toggle"
        type="button"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={onToggle}
      >
        <span className="knowledge-accordion-heading">
          <strong>{title} · {countLabel ?? count}</strong>
          <small>{summary}</small>
        </span>
        <span className="knowledge-accordion-action">{open ? "收起" : "展开"}</span>
      </button>
      {open && <div className="knowledge-accordion-content" id={contentId}>{children}</div>}
    </section>
  );
}

type KnownWorldAccordionsProps = {
  presentation?: PlayerGameState["presentation"];
  resources: PlayerGameState["resources"];
  publicResourceNames?: Record<string, string>;
  resourceIntelligence?: ResourceIntelligence;
  visibleNodes: PlayerGameState["visible_nodes"];
  actors: PlayerGameState["actors"];
  knownFacts: PlayerGameState["known_facts"];
  knownRelations?: NonNullable<PlayerGameState["known_relations"]>;
  knownActionRequirements?: NonNullable<PlayerGameState["known_action_requirements"]>;
  knownTargetActionContracts?: PublicTargetActionContract[];
  knownProducerBindings?: PublicProducerBinding[];
  task?: PublicTask | null;
  resourceTask?: PublicTask | null;
};

export function KnownWorldAccordions({
  presentation,
  resources,
  publicResourceNames = {},
  resourceIntelligence,
  visibleNodes,
  actors,
  knownFacts,
  knownRelations = [],
  knownActionRequirements = [],
  knownTargetActionContracts = [],
  knownProducerBindings = [],
  task = null,
  resourceTask,
}: KnownWorldAccordionsProps) {
  const [expanded, setExpanded] = useState<Record<KnowledgeAccordionKey, boolean>>({
    resources: true,
    locations: false,
    actors: false,
    facts: false,
    relations: false,
  });
  const [expandedFacilities, setExpandedFacilities] = useState<Record<string, boolean>>({});
  const [expandedResourceRegions, setExpandedResourceRegions] = useState<Record<string, boolean>>({});
  const previousVisibleReserveRegionSignature = useRef<string | null>(null);
  const toggle = (id: KnowledgeAccordionKey) => {
    setExpanded((current) => ({ ...current, [id]: !current[id] }));
  };
  const resourceRegionOpen = (key: string, defaultOpen: boolean) =>
    expandedResourceRegions[key] ?? defaultOpen;
  const toggleResourceRegion = (key: string, defaultOpen: boolean) => {
    setExpandedResourceRegions((current) => ({
      ...current,
      [key]: !(current[key] ?? defaultOpen),
    }));
  };
  const resourceGroups = groupResourcesByRegion(resources);
  const locationGroups = groupNodesByRegion(visibleNodes);
  const actorGroups = groupActorsByTask(actors, task);
  const actorFields = presentation
    ? presentation.actor_fields
    : ["NAME", "ROLE", "LOCATION", "COMMAND_REACHABILITY"];
  const displayedRelations = meaningfulKnownRelations(knownRelations);
  const nodeByKey = new Map(visibleNodes.map((node) => [node.key, node]));
  const nodeDisplayName = (key: string, candidate?: string | null) =>
    candidate ?? nodeByKey.get(key)?.name ?? "已知地点";
  const resourceRequirementText = (requirement: unknown) => {
    if (!requirement || typeof requirement !== "object" || Array.isArray(requirement)) return "";
    const normalized = requirement as Record<string, unknown>;
    const nodeKey = typeof normalized.node_key === "string" ? normalized.node_key : "";
    const targetName = nodeKey ? nodeDisplayName(nodeKey) : null;
    const text = resourceAvailabilityRequirementText(normalized, targetName);
    return text ? "解锁条件：" + text : "";
  };
  const factsByNode = new Map<string, PlayerGameState["known_facts"]>();
  const relationsBySource = new Map<string, NonNullable<PlayerGameState["known_relations"]>>();
  const regionFacts = new Map<string, PlayerGameState["known_facts"]>();
  const regionRelations = new Map<string, NonNullable<PlayerGameState["known_relations"]>>();
  const assignedFactKeys = new Set<string>();
  const assignedRelationKeys = new Set<string>();
  const factIdentity = (fact: PlayerGameState["known_facts"][number]) => `${fact.node_key}:${fact.fact_key}`;
  const nodeFamily = (node: PlayerGameState["visible_nodes"][number] | undefined) =>
    node?.node_family ?? node?.node_type_key?.toUpperCase();
  const factFamily = (fact: PlayerGameState["known_facts"][number]) =>
    fact.node_family ?? fact.node_type_key?.toUpperCase();
  const isRegionNode = (node: PlayerGameState["visible_nodes"][number] | undefined) =>
    nodeFamily(node) === "REGION";
  const isFacilityNode = (node: PlayerGameState["visible_nodes"][number] | undefined) =>
    nodeFamily(node) === "FACILITY";
  const isTransportNode = (node: PlayerGameState["visible_nodes"][number] | undefined) =>
    nodeFamily(node) === "TRANSPORT";

  knownFacts.forEach((fact) => {
    const node = nodeByKey.get(fact.node_key);
    const facts = factsByNode.get(fact.node_key) ?? [];
    facts.push(fact);
    factsByNode.set(fact.node_key, facts);
    if (isFacilityNode(node) || factFamily(fact) === "FACILITY" || isTransportNode(node)) {
      assignedFactKeys.add(factIdentity(fact));
      return;
    }
    if (isRegionNode(node) || factFamily(fact) === "REGION") {
      const regionKey = node?.key ?? fact.region_key;
      if (regionKey) {
        const group = regionFacts.get(regionKey) ?? [];
        group.push(fact);
        regionFacts.set(regionKey, group);
        assignedFactKeys.add(factIdentity(fact));
      }
    }
  });

  displayedRelations.forEach((relation) => {
    const source = nodeByKey.get(relation.source_node_key);
    const relations = relationsBySource.get(relation.source_node_key) ?? [];
    relations.push(relation);
    relationsBySource.set(relation.source_node_key, relations);
    const relationKey = relationDisplayKey(relation);
    if (isFacilityNode(source) || isTransportNode(source)) {
      assignedRelationKeys.add(relationKey);
    } else if (source && isRegionNode(source)) {
      const group = regionRelations.get(source.key) ?? [];
      group.push(relation);
      regionRelations.set(source.key, group);
      assignedRelationKeys.add(relationKey);
    }
  });

  const fallbackFacts = knownFacts.filter((fact) => !assignedFactKeys.has(factIdentity(fact)));
  const fallbackRelations = displayedRelations.filter(
    (relation) => !assignedRelationKeys.has(relationDisplayKey(relation)),
  );
  const fallbackFactGroups = groupFactsByRegion(fallbackFacts);
  // Target-specific applicability and permissions are already filtered by
  // SharedKnowledgeProjection on the backend.  The Player renderer only
  // formats the DTO; it must not synthesize a second target contract from
  // action-level requirements.
  const targetActionContracts = mergeTargetActionContracts(knownTargetActionContracts);
  const contractsByTarget = new Map<string, PublicTargetActionContract[]>();
  targetActionContracts.forEach((contract) => {
    const contracts = contractsByTarget.get(contract.target_key) ?? [];
    contracts.push(contract);
    contractsByTarget.set(contract.target_key, contracts);
  });
  const producerBindingsByTarget = new Map<string, PublicProducerBinding[]>();
  knownProducerBindings.forEach((binding) => {
    const bindings = producerBindingsByTarget.get(binding.target_key) ?? [];
    bindings.push(binding);
    producerBindingsByTarget.set(binding.target_key, bindings);
  });
  const resourceNames = new Map<string, string>();
  const isPublicResourceName = (key: string, candidate: unknown): candidate is string =>
    typeof candidate === "string"
    && candidate.trim().length > 0
    && candidate !== key
    && !/^[a-z0-9_]+$/.test(candidate);
  const setResourceName = (key: string, candidate: unknown) => {
    if (!isPublicResourceName(key, candidate) || resourceNames.has(key)) return;
    resourceNames.set(key, candidate);
  };
  Object.entries(publicResourceNames).forEach(([key, name]) => setResourceName(key, name));
  resources.forEach((resource) => setResourceName(resource.key, resource.name));
  if (resourceIntelligence) {
    Object.values(resourceIntelligence.regions).forEach((region) => {
      Object.entries(region.resources).forEach(([key, resource]) => setResourceName(key, resource.resource_name));
    });
    Object.entries(resourceIntelligence.global_resources).forEach(([key, resource]) => setResourceName(key, resource.resource_name));
  }
  visibleNodes.forEach((node) => {
    (node.associated_known_resources ?? []).forEach((resource) => {
      if (typeof resource.resource_key !== "string" || typeof resource.resource_name !== "string") return;
      setResourceName(resource.resource_key, resource.resource_name);
    });
  });
  const resourceName = (key: string, candidate?: string) =>
    resourceDisplayName(key, isPublicResourceName(key, candidate) ? candidate : resourceNames.get(key));
  const targetResourceRequirementIdentities = new Set(
    targetActionContracts.flatMap((contract) =>
      uniquePublicResourceRequirements(
        contract.action_key,
        contract.resource_requirements,
        contract.target_key,
      ).map((requirement) => publicResourceRequirementIdentity(
        contract.action_key,
        requirement,
        contract.target_key,
      )),
    ),
  );
  const actionResourceRequirementRows = knownActionRequirements.flatMap((action) => {
    const targetKey = targetKeyForPublicActionRequirement(action, visibleNodes);
    const requirements = uniquePublicResourceRequirements(
      action.action_key,
      action.resource_requirements,
      targetKey ?? undefined,
    ).filter(
      (requirement) => !targetResourceRequirementIdentities.has(
        publicResourceRequirementIdentity(action.action_key, requirement, targetKey ?? undefined),
      ),
    );
    const parts = publicResourceRequirementParts(requirements, resourceName);
    return parts.length > 0
      ? [{ key: action.action_key, actionName: action.action_name, value: parts.join("、") }]
      : [];
  });
  const reserveTask = resourceTask === undefined ? task : resourceTask;
  const resourceReserveRequirements = new Map<string, number>();
  if (
    reserveTask
    && ["ACTIVE", "NEEDS_PLAYER_INPUT"].includes(reserveTask.status)
    && !["COMPLETED", "BLOCKED", "ABORTED"].includes(reserveTask.execution_phase)
  ) {
    reserveTask.roadmap.stages.forEach((stage) => {
      stage.requirements.forEach((requirement) => {
        if (
          requirement.kind === "RESOURCE_AT_LEAST"
          && typeof requirement.region_key === "string"
          && typeof requirement.resource_key === "string"
          && typeof requirement.minimum === "number"
        ) {
          resourceReserveRequirements.set(
            `${requirement.region_key}:${requirement.resource_key}`,
            requirement.minimum,
          );
        }
      });
    });
  }
  const visibleReserveRegionKeys = new Set<string>();
  resourceReserveRequirements.forEach((_minimum, requirementKey) => {
    const separator = requirementKey.indexOf(":");
    if (separator > 0) visibleReserveRegionKeys.add(requirementKey.slice(0, separator));
  });
  const visibleReserveRegionSignature = Array.from(visibleReserveRegionKeys).sort().join("|");
  useEffect(() => {
    const previousSignature = previousVisibleReserveRegionSignature.current;
    if (previousSignature !== null) {
      const previousKeys = new Set(previousSignature ? previousSignature.split("|") : []);
      const newlyVisibleRegionKeys = (visibleReserveRegionSignature
        ? visibleReserveRegionSignature.split("|")
        : []).filter((regionKey) => !previousKeys.has(regionKey));
      if (newlyVisibleRegionKeys.length > 0) {
        setExpandedResourceRegions((current) => {
          const next = { ...current };
          newlyVisibleRegionKeys.forEach((regionKey) => {
            next[regionKey] = true;
          });
          return next;
        });
      }
    }
    previousVisibleReserveRegionSignature.current = visibleReserveRegionSignature;
  }, [visibleReserveRegionSignature]);
  const surveyedRegionCount = resourceIntelligence
    ? Object.values(resourceIntelligence.regions).filter(
        (region) => region.resource_survey_completed,
      ).length
    : null;
  const statusTone = (value: string | number | boolean) =>
    value === false || value === 0 || value === "false" || value === "UNKNOWN" || value === "UNAVAILABLE" || value === "BLOCKED"
      ? "neutral"
      : "success";
  const targetContractsFor = (nodeKey: string) => contractsByTarget.get(nodeKey) ?? [];
  const renderKnownLocations = () => (
    <div className="console-region-groups">
      {locationGroups.map((group) => {
        const groupFacts = regionFacts.get(group.key) ?? [];
        const groupRelations = regionRelations.get(group.key) ?? [];
        return (
          <details className="knowledge-region" key={group.key} open={group.key === "__all__"}>
            <summary>
              <span>
                <strong>{group.name}</strong>
                <small>{group.items.length} {"\u4e2a\u5df2\u77e5\u5730\u70b9"}</small>
              </span>
            </summary>
            <div className="knowledge-region-content">
              {(groupFacts.length > 0 || groupRelations.length > 0) && (
                <div className="knowledge-region-facts">
                  {groupFacts.map((fact) => (
                    <div className="knowledge-entry" key={"region-fact:" + factIdentity(fact)}>
                      <div className="knowledge-entry-copy">
                        <strong>{factDisplayLabel(fact)}</strong>
                      </div>
                      <span className={"console-pill " + statusTone(fact.value) + " knowledge-status-pill"}>
                        {factDisplayValue(fact)}
                      </span>
                    </div>
                  ))}
                  {groupRelations.map((relation) => (
                    <div className="knowledge-relation" key={"region-relation:" + relationDisplayKey(relation)}>
                      <div className="knowledge-relation-line">
                        <strong>{nodeDisplayName(relation.source_node_key, relation.source_node_name)}</strong>
                        <span className="knowledge-relation-arrow" aria-hidden="true">{"\u2192"}</span>
                        <strong>{nodeDisplayName(relation.target_node_key, relation.target_node_name)}</strong>
                      </div>
                      <small>{knownRelationDescription(relation.relation_type_key, relation.relation_type_name)}</small>
                    </div>
                  ))}
                </div>
              )}
              <div className="knowledge-location-list">
                {group.items.map((node) => {
                  const nodeFacts = factsByNode.get(node.key) ?? [];
                  const nodeRelations = relationsBySource.get(node.key) ?? [];
                  const facility = isFacilityNode(node);
                  const transport = isTransportNode(node);
                  const primaryHeaderFact = nodeFacts.find(
                    (fact) => resolveFacilityFactRole(fact) === "HEADER_PRIMARY",
                  );
                  const secondaryHeaderFact = nodeFacts.find(
                    (fact) => resolveFacilityFactRole(fact) === "HEADER_SECONDARY",
                  );
                  const mainFacts = nodeFacts.filter(
                    (fact) => !["SUPPORTING", "REQUIREMENT_ONLY"].includes(resolveFacilityFactRole(fact)),
                  );
                  const leadingFact = primaryHeaderFact ?? mainFacts[0];
                  const supportingFact = secondaryHeaderFact ?? mainFacts[1];
                  const facilityPresentation = presentation ? {
                    ...presentation,
                    summary_slot: node.presentation?.summary_slot ?? presentation.summary_slot,
                    entity_detail: node.presentation?.detail_level ?? presentation.entity_detail,
                    default_open: node.presentation?.default_open ?? presentation.default_open,
                    knowledge_level: node.presentation?.knowledge_level ?? presentation.knowledge_level,
                    semantic_order: node.presentation?.semantic_order ?? presentation.semantic_order,
                  } : undefined;
                  const targetContracts = targetContractsFor(node.key);
                  const associatedResources = node.associated_known_resources ?? [];
                  const facilityResourceRows = facility
                    ? buildFacilityResourceRows(
                      node.key,
                      resourceIntelligence,
                      associatedResources,
                      resourceName,
                      nodeDisplayName,
                      (factNodeKey, factKey) => {
                        const fact = factsByNode.get(factNodeKey)?.find(
                          (candidate) => candidate.fact_key === factKey,
                        );
                        return fact ? factDisplayLabel(fact) : undefined;
                      },
                      producerBindingsByTarget.get(node.key) ?? [],
                    )
                    : [];
                  const targetActionRequirementRows = buildTargetActionRequirementRows({
                    contracts: targetContracts,
                    knownFacts,
                    visibleNodeKeys: new Set(visibleNodes.map((visibleNode) => visibleNode.key)),
                    resourceName,
                    nodeName: nodeDisplayName,
                    nodeByKey,
                  });
                  const facilityDetailRows = facility
                    ? buildFacilityDetailRows({
                      nodeKey: node.key,
                      knownFacts,
                      knownRelations: nodeRelations,
                      knownActionRequirements,
                      producerBindings: producerBindingsByTarget.get(node.key) ?? [],
                      facilityResourceRows,
                      resourceName,
                      resolveNodeName: nodeDisplayName,
                      presentation: facilityPresentation,
                    })
                    : [];
                  const renderTargetActionRequirementRows = () => targetActionRequirementRows.map((row) => (
                    <div className="knowledge-facility-attribute" key={row.key}>
                      <span className="knowledge-facility-attribute-label">{row.label}</span>
                      <span className="knowledge-facility-attribute-value">{row.value}</span>
                    </div>
                  ));

                  if (transport) {
                    return (
                      <div className="knowledge-transport-card" data-testid={"transport-card-" + node.key} key={node.key}>
                        <div className="knowledge-transport-heading">
                          <span>
                            <strong>{node.name}</strong>
                            <small>{node.endpoint_region_names?.join(" ↔ ") ?? group.name}</small>
                          </span>
                          <span className={"knowledge-facility-status " + statusTone(leadingFact?.value ?? "UNKNOWN")}>
                            {leadingFact ? factDisplayValue(leadingFact) : "待探索"}
                          </span>
                          <span className="knowledge-transport-column-spacer" aria-hidden="true" />
                        </div>
                      </div>
                    );
                  }

                  if (facility) {
                    const defaultFacilityOpen = facilityPresentation?.default_open === "FULL";
                    const facilityOpen = expandedFacilities[node.key]
                      ?? defaultFacilityOpen;
                    return (
                      <details
                        className="knowledge-facility-card"
                        data-testid={"facility-card-" + node.key}
                        key={node.key}
                        open={facilityOpen}
                      >
                        <summary
                          onClick={(event) => {
                            event.preventDefault();
                            setExpandedFacilities((current) => ({
                              ...current,
                              [node.key]: !facilityOpen,
                            }));
                          }}
                        >
                          <span className="knowledge-facility-heading">
                            <strong>{node.name}</strong>
                          </span>
                          {facilityPresentation?.summary_slot !== "BODY" && <span className="knowledge-facility-statuses">
                            <span className={"knowledge-facility-status " + statusTone(supportingFact?.value ?? "UNKNOWN")}>
                              {supportingFact ? factDisplayValue(supportingFact) : "状态未知"}
                            </span>
                            <span className={"knowledge-facility-status " + statusTone(leadingFact?.value ?? "UNKNOWN")}>
                              {leadingFact ? facilityStatusDisplayValue(leadingFact) : "状态未知"}
                            </span>
                          </span>}
                          <span className="knowledge-facility-toggle" aria-hidden="true">
                            {facilityOpen ? "-" : "+"}
                          </span>
                        </summary>
                        <div className="knowledge-facility-details">
                          {facilityDetailRows.length > 0
                            ? facilityDetailRows.map((row) => (
                              <div
                                className={`knowledge-facility-attribute${row.kind === "DETAIL" ? "" : " knowledge-facility-helper"}`}
                                key={row.key}
                              >
                                <span className="knowledge-facility-attribute-label">{row.label}</span>
                                <span className="knowledge-facility-attribute-value">{row.value}</span>
                              </div>
                            ))
                            : <div className="knowledge-empty-state">{"暂无更多已知信息"}</div>}
                        </div>
                      </details>
                    );
                  }

                  return (
                    <details className="knowledge-node-card" key={node.key}>
                      <summary>
                        <span>
                          <strong>{node.name}</strong>
                          <small>{node.endpoint_region_names?.join(" \u2194 ") ?? node.region_name ?? group.name}</small>
                        </span>
                      </summary>
                      <div className="knowledge-node-details">
                        {targetActionRequirementRows.length > 0 && renderTargetActionRequirementRows()}
                        {nodeFacts.map((fact) => (
                          <div className="knowledge-entry" key={"node-fact:" + factIdentity(fact)}>
                            <span>{factDisplayLabel(fact)}</span>
                            <span className={"console-pill " + statusTone(fact.value) + " knowledge-status-pill"}>
                              {factDisplayValue(fact)}
                            </span>
                          </div>
                        ))}
                        {nodeRelations.map((relation) => (
                          <div className="knowledge-relation" key={relationDisplayKey(relation)}>
                            <div className="knowledge-relation-line">
                              <strong>{relation.source_node_name ?? relation.source_node_key}</strong>
                              <span className="knowledge-relation-arrow" aria-hidden="true">{"\u2192"}</span>
                              <strong>{relation.target_node_name ?? relation.target_node_key}</strong>
                            </div>
                            <small>{knownRelationDescription(relation.relation_type_key, relation.relation_type_name)}</small>
                          </div>
                        ))}
                        {nodeFacts.length === 0 && nodeRelations.length === 0 && (
                          <div className="knowledge-empty-state">{"\u6682\u65e0\u66f4\u591a\u5df2\u77e5\u4fe1\u606f"}</div>
                        )}
                      </div>
                    </details>
                  );
                })}
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
  return (
    <div className="knowledge-accordions">
      <KnowledgeAccordion
        id="locations"
        title="已知地点"
        count={visibleNodes.length}
        summary={"\u8bbe\u65bd\u72b6\u6001\u3001\u5df2\u77e5\u8d44\u6e90\u4e0e\u901a\u9053\u4fe1\u606f"}
        open={expanded.locations}
        onToggle={() => toggle("locations")}
      >
        {actionResourceRequirementRows.length > 0 && (
          <div className="knowledge-action-requirements" data-testid="known-action-requirements">
            <strong>公开行动资源要求</strong>
            {actionResourceRequirementRows.map((row) => (
              <div className="knowledge-facility-attribute" key={row.key}>
                <span className="knowledge-facility-attribute-label">{row.actionName}：</span>
                <span className="knowledge-facility-attribute-value">{row.value}</span>
              </div>
            ))}
          </div>
        )}
        {renderKnownLocations()}
      </KnowledgeAccordion>
      <KnowledgeAccordion
        id="actors"
        title="参与者"
        count={actors.length}
        summary="身份、角色与已知位置"
        open={expanded.actors}
        onToggle={() => toggle("actors")}
      >
        <div className="console-region-groups">
          {actorGroups.map((group) => (
            <details className="knowledge-region actor-group" open key={group.key}>
              <summary>
                <span>
                  <strong>{group.label} · {group.actors.length}</strong>
                </span>
              </summary>
              <div className="knowledge-region-content">
                <div className="knowledge-entry-list">
                  {group.actors.map((actor) => (
                    <div className="knowledge-entry" key={actor.key}>
                      <div className="knowledge-entry-copy">
                        {actorFields.map((field) => {
                          if (field === "NAME") return <strong key={field}>{actor.name}</strong>;
                          if (field === "ROLE") return <small key={field}>{actor.role_name}</small>;
                          if (field === "LOCATION") {
                            return (
                              <span className="console-pill success knowledge-status-pill" data-actor-field={field} key={field}>
                                {actor.current_node_name}
                              </span>
                            );
                          }
                          if (field === "STATUS") {
                            const statusLabel = actor.status === "ACTIVE"
                              ? "行动中"
                              : actor.status === "PLANNED"
                                ? "计划中"
                                : actor.status === "IDLE" ? "待命中" : group.label;
                            return <small data-actor-field={field} key={field}>{statusLabel}</small>;
                          }
                          if (field === "TASK" && actor.task_name) {
                            return <small data-actor-field={field} key={field}>{actor.task_name}</small>;
                          }
                          if (field === "COMMAND_REACHABILITY") {
                            return (
                              <span
                                className={`console-pill ${actor.command_reachability === "DISCONNECTED" ? "danger" : "success"} knowledge-status-pill`}
                                data-actor-field={field}
                                key={field}
                              >
                                {actor.command_reachability === "DISCONNECTED" ? "失联" : "在线"}
                              </span>
                            );
                          }
                          return null;
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </details>
          ))}
        </div>
      </KnowledgeAccordion>
      <KnowledgeAccordion
        id="resources"
        countLabel={
          resourceIntelligence
            ? "已探查区域 " + surveyedRegionCount + " / " + resourceIntelligence.total_regions
            : undefined
        }
        title="资源"
        count={resources.length}
        summary="可用资源状态"
        open={expanded.resources}
        onToggle={() => toggle("resources")}
      >
        {resourceIntelligence && (
          <div className="console-region-groups">
            {Object.entries(resourceIntelligence.regions).map(([regionKey, region]) => {
              const displayResources = Object.entries(region.resources).map(
                ([resourceKey, resource]) => ({ resourceKey, resource, synthetic: false }),
              );
              resourceReserveRequirements.forEach((minimum, requirementKey) => {
                const separator = requirementKey.indexOf(":");
                const requirementRegionKey = requirementKey.slice(0, separator);
                const requirementResourceKey = requirementKey.slice(separator + 1);
                if (
                  requirementRegionKey === regionKey
                  && !Object.prototype.hasOwnProperty.call(region.resources, requirementResourceKey)
                ) {
                  displayResources.push({
                    resourceKey: requirementResourceKey,
                    resource: {
                      resource_name: resourceName(requirementResourceKey),
                      known_available: 0,
                      known_total: 0,
                      pools: [],
                    },
                    synthetic: true,
                  });
                }
              });
              const knownResources = displayResources.filter(
                ({ resourceKey, resource }) =>
                  (resource.known_total ?? resource.known_available) > 0
                  || resourceReserveRequirements.has(`${regionKey}:${resourceKey}`),
              );
              const hasPositiveResource = knownResources.some(
                ({ resource }) => resource.known_available > 0,
              );
              const defaultOpen = hasPositiveResource || visibleReserveRegionKeys.has(regionKey);
              return (
                <details
                  className="knowledge-region"
                  key={regionKey}
                  open={resourceRegionOpen(regionKey, defaultOpen)}
                >
                    <summary
                      onClick={(event) => {
                        event.preventDefault();
                        toggleResourceRegion(regionKey, defaultOpen);
                      }}
                  >
                    <span>
                      <strong>{region.region_name ?? regionKey}</strong>
                      <small>{region.resource_survey_completed ? "已完成查探" : "未完成查探"}</small>
                    </span>
                  </summary>
                  <div className="knowledge-region-content">
                    <div className="knowledge-entry-list">
                      {knownResources.length === 0 ? (
                        <div className="knowledge-empty-state">暂无资源信息</div>
                      ) : (
                        knownResources.map(({ resourceKey, resource, synthetic }) => (
                          <div className="knowledge-entry" key={regionKey + ":" + resourceKey}>
                            <div className="knowledge-entry-copy">
                              <strong>
                                {resource.resource_name}
                                {(resource.display_unit ?? resource.unit)
                                  ? ` · ${resource.display_unit ?? resource.unit}`
                                  : ""}
                              </strong>
                              {!synthetic && !region.resource_survey_completed && <small>已确认</small>}
                              {resource.pools
                                .filter(
                                  (pool) => pool.availability === "UNAVAILABLE" && pool.quantity > 0,
                                )
                                .map((pool, index) => (
                                  (() => {
                                    const requirement = resourceRequirementText(pool.availability_requirement);
                                    return (
                                      <small key={index}>
                                        暂不可用 {pool.quantity}
                                        {!requirement && pool.facility_name ? ` · ${pool.facility_name}` : ""}
                                        {requirement ? " · " + requirement : ""}
                                      </small>
                                    );
                                  })()
                                ))}
                            </div>
                            <div className="knowledge-resource-pills">
                              {resourceReserveRequirements.has(`${regionKey}:${resourceKey}`) && (
                                <span
                                  className={`console-pill ${resource.known_available >= resourceReserveRequirements.get(`${regionKey}:${resourceKey}`)! ? "success" : "warning"} knowledge-status-pill resource-reserve-pill`}
                                >
                                  {"储备 " + resource.known_available + " / " + resourceReserveRequirements.get(`${regionKey}:${resourceKey}`)}
                                </span>
                              )}
                              {!synthetic && (
                                <span className="console-pill success knowledge-status-pill">
                                  {resource.known_total == null || resource.known_total === resource.known_available
                                    ? resource.known_available
                                    : resource.known_available + " / " + resource.known_total}
                                </span>
                              )}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </details>
              );
            })}
            {Object.entries(resourceIntelligence.global_resources).some(
              ([, resource]) => (resource.known_total ?? resource.known_available) > 0,
            ) && (
              <details className="knowledge-region" open>
                <summary><span><strong>全局资源</strong></span></summary>
                <div className="knowledge-region-content">
                  <div className="knowledge-entry-list">
                    {Object.entries(resourceIntelligence.global_resources)
                      .filter(([, resource]) => (resource.known_total ?? resource.known_available) > 0)
                      .map(([resourceKey, resource]) => (
                        <div className="knowledge-entry" key={"global:" + resourceKey}>
                          <div className="knowledge-entry-copy">
                            <strong>
                              {resource.resource_name}
                              {(resource.display_unit ?? resource.unit)
                                ? ` · ${resource.display_unit ?? resource.unit}`
                                : ""}
                            </strong>
                          </div>
                          <span className="console-pill success knowledge-status-pill">
                            {resource.known_total == null || resource.known_total === resource.known_available
                              ? resource.known_available
                              : resource.known_available + " / " + resource.known_total}
                          </span>
                        </div>
                      ))}
                  </div>
                </div>
              </details>
            )}
          </div>
        )}
        <div className="console-region-groups">
          {(resourceIntelligence ? [] : resourceGroups).map((group) => (
            <details
              className="knowledge-region"
              key={group.key}
              open={resourceRegionOpen(
                group.key,
                group.items.some((resource) => resource.value > 0) || visibleReserveRegionKeys.has(group.key),
              )}
            >
              <summary
                onClick={(event) => {
                  event.preventDefault();
                  toggleResourceRegion(
                    group.key,
                    group.items.some((resource) => resource.value > 0) || visibleReserveRegionKeys.has(group.key),
                  );
                }}
              >
                <span>
                  <strong>{group.name}</strong>
                  <small>{group.items.length} 项资源</small>
                </span>
              </summary>
              <div className="knowledge-region-content">
                <div className="knowledge-entry-list">
                  {group.items.filter((resource) => resource.value > 0).length === 0 ? (
                    <div className="knowledge-empty-state">暂无资源信息</div>
                  ) : group.items.filter((resource) => resource.value > 0).map((resource) => (
                    <div className="knowledge-entry" key={`${group.key}:${resource.key}`}>
                      <div className="knowledge-entry-copy">
                        <strong>{resource.name}</strong>
                      </div>
                      <span className={`console-pill ${statusTone(resource.value)} knowledge-status-pill`}>
                        {resource.value}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </details>
          ))}
        </div>
      </KnowledgeAccordion>
      {(fallbackFacts.length > 0 || fallbackRelations.length > 0) && (
      <>
      {fallbackFacts.length > 0 && (
      <KnowledgeAccordion
        id="facts"
        title="已知事实"
        count={fallbackFacts.length}
        summary="当前已知世界状态"
        open={expanded.facts}
        onToggle={() => toggle("facts")}
      >
        <div className="console-region-groups">
          {fallbackFactGroups.map((group) => (
            <details className="knowledge-region" key={group.key} open={group.key === "__all__"}>
              <summary>
                <span>
                  <strong>{group.name}</strong>
                  <small>{group.facts.length} 条事实</small>
                </span>
              </summary>
              <div className="knowledge-region-content">
                <div className="knowledge-entry-list">
                {group.facts.map((fact) => (
                  <div className="knowledge-entry" key={`${group.key}:${fact.node_key}.${fact.fact_key}`}>
                    <div className="knowledge-entry-copy knowledge-fact-copy">
                      <strong>{fact.node_name ?? "未知地点"}</strong>
                      <small>{factDisplayLabel(fact)}</small>
                    </div>
                    <span className={`console-pill ${statusTone(fact.value)} knowledge-status-pill knowledge-fact-value`}>
                      {factDisplayValue(fact)}
                    </span>
                  </div>
                ))}
                </div>
              </div>
            </details>
          ))}
        </div>
      </KnowledgeAccordion>
      )}
      <KnowledgeAccordion
        id="relations"
        title="已知关系"
        count={fallbackRelations.length}
        summary="当前已掌握的关键系统关系"
        open={expanded.relations}
        onToggle={() => toggle("relations")}
      >
        {fallbackRelations.length === 0 ? (
          <div className="knowledge-empty-state">暂无已知关键关系</div>
        ) : (
          <div className="knowledge-relation-list">
            {fallbackRelations.map((relation) => (
              <div className="knowledge-relation" key={relationDisplayKey(relation)}>
                <div className="knowledge-relation-line">
                  <strong>{relation.source_node_name ?? relation.source_node_key}</strong>
                  <span className="knowledge-relation-arrow" aria-hidden="true">→</span>
                  <strong>{relation.target_node_name ?? relation.target_node_key}</strong>
                </div>
                <small>{knownRelationDescription(relation.relation_type_key, relation.relation_type_name)}</small>
              </div>
            ))}
          </div>
        )}
      </KnowledgeAccordion>
      </>
      )}
    </div>
  );
}

export function TaskTabs({
  tasks,
  selectedTaskId,
  onSelect,
  inheritedTaskCount = 0,
}: {
  tasks: PlayerGameState["task_history"];
  selectedTaskId: string | null;
  onSelect: (id: string) => void;
  inheritedTaskCount?: number;
}) {
  if (!tasks.length) return null;
  return (
    <nav className="task-tabs" aria-label="任务历史">
      {tasks.map((item, index) => (
        <Fragment key={item.id}>
          {inheritedTaskCount > 0 && index === inheritedTaskCount && index < tasks.length && <div className="history-boundary" data-testid="history-boundary" role="separator">— 该存档初始状态 —</div>}
          <button
          type="button"
          data-testid={`task-tab-${item.id}`}
          data-task-id={item.id}
          className={selectedTaskId === item.id ? "selected" : ""}
          aria-pressed={selectedTaskId === item.id}
          onClick={() => onSelect(item.id)}
        >
          <span>任务 {item.sequence}</span>
          <strong>{taskObjectiveLabel(item.goal, item.objective_names)}</strong>
          <em className={taskTone[item.status] ?? "neutral"}>{uiLabel(item.status)}</em>
          </button>
        </Fragment>
      ))}
    </nav>
  );
}

export function GoalComposer({
  goal,
  pendingGoal,
  resolving,
  startedAt,
  busy,
  feedback = null,
  readyDraft = null,
  confirming = false,
  goalPresets = [],
  presetsLoaded = false,
  onGoalChange,
  onSubmit,
  onConfirm,
}: {
  goal: string;
  pendingGoal: string | null;
  resolving: boolean;
  startedAt: number | null;
  busy: boolean;
  feedback?: string | null;
  readyDraft?: PublicResolvedGoalDraft | null;
  confirming?: boolean;
  goalPresets?: string[];
  presetsLoaded?: boolean;
  onGoalChange: (value: string) => void;
  onSubmit: () => void;
  onConfirm?: (draftId: string) => void;
}) {
  const [selectedPresetText, setSelectedPresetText] = useState("");
  const displayedGoal = resolving ? pendingGoal ?? goal : goal;
  const handlePresetChange = (value: string) => {
    setSelectedPresetText(value);
    if (value) onGoalChange(value);
  };
  return (
    <section className="command-panel goal-composer-panel" data-testid="goal-composer">
      <header className="command-panel-heading">
        <div>
          <p>当前 · 下达目标</p>
          <h1>下达目标</h1>
        </div>
        <span className="console-pill success">GameInstance</span>
      </header>
      <form
        className="command-composer-v2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!resolving && goal.trim()) onSubmit();
        }}
      >
        <div className="goal-input-row">
          <label className="sr-only" htmlFor="goal">目标内容</label>
          <textarea
            id="goal"
            rows={2}
            value={displayedGoal}
            onChange={(event) => {
              setSelectedPresetText("");
              onGoalChange(event.target.value);
            }}
            placeholder="描述你希望达成的目标，例如“修复中央隧道”……"
            disabled={resolving}
          />
          <button disabled={resolving || !goal.trim() || busy} type="submit">
            {resolving ? "解析中…" : "解析目标"}
          </button>
        </div>
        <select
          id="goal-preset-select"
          aria-label="选择快捷目标"
          value={presetsLoaded ? selectedPresetText : ""}
          onChange={(event) => handlePresetChange(event.target.value)}
          disabled={resolving || !presetsLoaded}
        >
          <option value="">{presetsLoaded ? '选择快捷目标……' : '正在加载快捷目标……'}</option>
          {goalPresets.map((preset) => (
            <option key={preset} value={preset}>{preset}</option>
          ))}
        </select>
        {readyDraft && (
          <div className="goal-confirmation-feedback" data-testid="goal-confirmation-feedback" role="status">
            <span>{readyDraft.presentation_text}</span>
            <button
              type="button"
              disabled={confirming || busy}
              onClick={() => onConfirm?.(readyDraft.draft_id)}
            >
              {confirming ? "确认中…" : "确认目标"}
            </button>
          </div>
        )}
        {!readyDraft && feedback && (
          <p className="goal-submission-feedback" data-testid="goal-submission-feedback" role="status">
            {feedback}
          </p>
        )}
        {resolving && startedAt !== null && (
          <WaitingStatus
            startedAt={startedAt}
            label="Agent 正在理解目标"
            testId="goal-resolving-status"
          />
        )}
      </form>
    </section>
  );
}

function scenarioGoalPresets(
  metadata: PlayerGameState["scenario_metadata"] | undefined,
): string[] {
  return (metadata?.goal_presets ?? []).flatMap((item) => {
    if (!item.name.trim()) {
      return [];
    }
    return [item.name];
  });
}

export function MissionLogPanel({ children }: { children: ReactNode }) {
  return (
    <section className="command-panel mission-log-panel mission-log-panel--tall" data-testid="mission-log-panel">
      {children}
    </section>
  );
}

export function GamePage() {
  const { gameId = "" } = useParams();
  const queryClient = useQueryClient();
  const fork = useForkGame();
  const [goal, setGoal] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [pendingGoal, setPendingGoal] = useState<string | null>(null);
  const [acceptedTask, setAcceptedTask] = useState<PublicTask | null>(null);
  const [activeOperation, setActiveOperation] = useState<ActivePlayOperation | null>(null);
  const [continuousExecuting, setContinuousExecuting] = useState(false);
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [developerToken, setDeveloperToken] = useState("");
  const [presentationDrawerOpen, setPresentationDrawerOpen] = useState(false);
  const [savedPresentationProfile, setSavedPresentationProfile] = useState<PresentationProfileResponse | null>(null);
  const [workingPresentationProfile, setWorkingPresentationProfile] = useState<PresentationProfileDocument | null>(null);
  const [presentationSaveState, setPresentationSaveState] = useState<"CLEAN" | "DIRTY" | "SAVING" | "CONFLICT" | "ERROR">("CLEAN");
  const [checkpointNotice, setCheckpointNotice] = useState<string | null>(null);
  const [goalFeedback, setGoalFeedback] = useState<string | null>(null);
  const [lastParseResult, setLastParseResult] = useState<
    GoalSubmission | { status: "SYSTEM_ERROR"; presentation_text: string; draft_id: null } | null
  >(null);
  const play = useQuery({
    queryKey: ["play", gameId, selectedTaskId],
    queryFn: () => api.playState(gameId, selectedTaskId),
    placeholderData: (previous) => previous,
    refetchOnWindowFocus: !continuousExecuting,
    refetchOnReconnect: !continuousExecuting,
    refetchInterval: (query) =>
      planningRefetchInterval(query.state.data, activeOperation, continuousExecuting),
  });
  const livePlay = useQuery({
    queryKey: ["play", gameId, "live"],
    queryFn: () => api.playState(gameId, null),
    placeholderData: (previous) => previous,
    refetchOnWindowFocus: !continuousExecuting,
    refetchOnReconnect: !continuousExecuting,
    refetchInterval: (query) =>
      planningRefetchInterval(query.state.data, activeOperation, continuousExecuting),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["play", gameId] });
  const syncLivePlay = (state: PlayerGameState) => {
    syncPlayStateCaches(
      (queryKey, nextState) => queryClient.setQueryData<PlayerGameState>(queryKey, nextState),
      gameId,
      selectedTaskId,
      state,
    );
    void refresh();
  };
  const submit = useMutation({
    mutationFn: () => api.submitGoal(gameId, goal, crypto.randomUUID()),
    onMutate: () => {
      setGoalFeedback(null);
      setLastParseResult(null);
      setPendingGoal(goal);
      setAcceptedTask(null);
      setActiveOperation({ kind: "goal", taskId: null, startedAt: Date.now() });
    },
    onSuccess: (result) => {
      setLastParseResult(result);
      setGoalFeedback(
        result.status === "READY_FOR_CONFIRMATION" ? null : result.presentation_text,
      );
      void refresh();
    },
    onError: (error) => {
      const presentationText = goalSubmissionErrorText(error);
      setGoalFeedback(presentationText);
      setLastParseResult({
        status: "SYSTEM_ERROR",
        presentation_text: presentationText,
        draft_id: null,
      });
      setPendingGoal(null);
      setActiveOperation(null);
    },
    onSettled: () => {
      setPendingGoal(null);
      setActiveOperation((operation) => (operation?.kind === "goal" ? null : operation));
    },
  });
  const confirmGoal = useMutation({
    mutationFn: (draftId: string) => api.confirmGoalDraft(gameId, draftId),
    onSuccess: (task) => {
      setAcceptedTask(task);
      setSelectedTaskId(task.id);
      setLastParseResult(null);
      setGoalFeedback(null);
      setGoal("");
      void refresh();
    },
  });
  const startPlanning = useMutation({
    mutationFn: ({ version }: { version: number; taskId: string }) =>
      api.startInitialPlanning(gameId, version),
    onMutate: ({ taskId }) =>
      setActiveOperation({ kind: "planning", taskId, startedAt: Date.now() }),
    onSuccess: syncLivePlay,
    onError: () => setActiveOperation(null),
    onSettled: () =>
      setActiveOperation((operation) =>
        operation?.kind === "planning" ? null : operation,
      ),
  });
  const abandon = useMutation({
    mutationFn: (taskId: string) => api.abandonTask(gameId, taskId),
    onSuccess: () => void refresh(),
  });
  const archive = useMutation({
    mutationFn: (revision: number) => api.archiveGame(gameId, revision),
    onSuccess: () => void refresh(),
  });
  const checkpoint = useMutation({
    mutationFn: (revision: number) => api.checkpointGame(gameId, revision, crypto.randomUUID()),
    onSuccess: (target) => {
      setCheckpointNotice("已创建存档 " + target.id.slice(0, 8));
      void queryClient.invalidateQueries({ queryKey: ["games"] });
      void refresh();
    },
  });
  const decision = useMutation({
    mutationFn: ({
      approve,
      decisionId,
      taskVersion,
    }: {
      approve: boolean;
      decisionId: string;
      taskVersion: number;
    }) => api.decideApproval(gameId, decisionId, approve, taskVersion),
    onSuccess: syncLivePlay,
  });
  const pacing = useMutation({
    mutationFn: ({ phase, version }: { phase: "action" | "debrief"; version: number }) =>
      phase === "action"
        ? api.acknowledgeAction(gameId, version)
        : api.acknowledgeDebrief(gameId, version),
    onSuccess: syncLivePlay,
  });
  const continuous = useMutation({
    mutationFn: (version: number) => api.runUntilBoundary(gameId, version),
    onMutate: () => setContinuousExecuting(true),
    onSuccess: syncLivePlay,
    onSettled: () => setContinuousExecuting(false),
  });
  const replan = useMutation({
    mutationFn: ({ version }: { version: number; taskId: string }) =>
      api.replan(gameId, version),
    onMutate: ({ taskId }) =>
      setActiveOperation({ kind: "replanning", taskId, startedAt: Date.now() }),
    onSuccess: syncLivePlay,
    onError: () => setActiveOperation(null),
    onSettled: () =>
      setActiveOperation((operation) =>
        operation?.kind === "replanning" ? null : operation,
      ),
  });
  const developer = useQuery({
    queryKey: ["developer", gameId, developerToken],
    queryFn: () => api.developerSnapshot(gameId, developerToken),
    enabled: developerOpen && Boolean(developerToken),
  });
  const presentationScenarioId = play.data?.game.scenario_id ?? "";
  const presentationProfileQuery = useQuery({
    queryKey: ["presentation", presentationScenarioId],
    queryFn: () => api.presentation(presentationScenarioId),
    enabled: presentationDrawerOpen && Boolean(presentationScenarioId),
    refetchOnWindowFocus: false,
  });
  const presentationRevisionQuery = useQuery({
    queryKey: ["presentation-revision", presentationScenarioId],
    queryFn: () => api.presentationRevision(presentationScenarioId),
    enabled: Boolean(presentationScenarioId),
    refetchOnWindowFocus: true,
    refetchInterval: PRESENTATION_PROFILE_REFRESH_INTERVAL_MS,
  });
  const refetchPresentationProfile = presentationProfileQuery.refetch;
  useEffect(() => {
    const next = presentationProfileQuery.data;
    if (!next) return;
    if (!savedPresentationProfile) {
      setSavedPresentationProfile(next);
      setWorkingPresentationProfile(clonePresentationProfile(next.profile));
      setPresentationSaveState("CLEAN");
      return;
    }
    if (next.revision <= savedPresentationProfile.revision) return;
    const localDirty = Boolean(
      workingPresentationProfile
      && JSON.stringify(savedPresentationProfile.profile) !== JSON.stringify(workingPresentationProfile),
    );
    setSavedPresentationProfile(next);
    if (localDirty) {
      setPresentationSaveState("CONFLICT");
      return;
    }
    setWorkingPresentationProfile(clonePresentationProfile(next.profile));
    setPresentationSaveState("CLEAN");
  }, [presentationProfileQuery.data, savedPresentationProfile, workingPresentationProfile]);

  useEffect(() => {
    const nextRevision = presentationRevisionQuery.data?.revision;
    if (nextRevision == null) return;
    if (!savedPresentationProfile) {
      void queryClient.invalidateQueries({ queryKey: ["play", gameId] });
      return;
    }
    if (nextRevision <= savedPresentationProfile.revision) return;
    const localDirty = Boolean(
      workingPresentationProfile
      && JSON.stringify(savedPresentationProfile.profile) !== JSON.stringify(workingPresentationProfile),
    );
    if (localDirty) {
      setPresentationSaveState("CONFLICT");
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["play", gameId] });
    if (presentationDrawerOpen) void refetchPresentationProfile();
  }, [
    gameId,
    presentationDrawerOpen,
    refetchPresentationProfile,
    presentationRevisionQuery.data?.revision,
    queryClient,
    savedPresentationProfile,
    workingPresentationProfile,
  ]);

  const reloadGamePresentation = async () => {
    const result = await presentationProfileQuery.refetch();
    if (!result.data) return;
    setSavedPresentationProfile(result.data);
    setWorkingPresentationProfile(clonePresentationProfile(result.data.profile));
    setPresentationSaveState("CLEAN");
  };

  const saveGamePresentation = useMutation({
    mutationFn: () => {
      if (!savedPresentationProfile || !workingPresentationProfile) {
        throw new Error("PresentationProfile 尚未加载");
      }
      return api.savePresentation(
        presentationScenarioId,
        savedPresentationProfile.revision,
        workingPresentationProfile,
      );
    },
    onMutate: () => setPresentationSaveState("SAVING"),
    onSuccess: (next) => {
      setSavedPresentationProfile(next);
      setWorkingPresentationProfile(clonePresentationProfile(next.profile));
      setPresentationSaveState("CLEAN");
      queryClient.setQueryData(["presentation", presentationScenarioId], next);
      void queryClient.invalidateQueries({ queryKey: ["presentation", presentationScenarioId] });
      void queryClient.invalidateQueries({ queryKey: ["presentation-revision", presentationScenarioId] });
      void queryClient.invalidateQueries({ queryKey: ["play", gameId] });
    },
    onError: (error) => setPresentationSaveState(error instanceof ApiError && error.status === 409 ? "CONFLICT" : "ERROR"),
  });

  const loadedTask = play.data?.current_task ?? null;
  const resolvingGoal = submit.isPending || pendingGoal !== null;
  const goalResolving = resolvingGoal && activeOperation?.kind === "goal";

  if (!play.data || !livePlay.data) {
    return <main className="page"><p>正在加载游戏状态……</p></main>;
  }
  const { game } = play.data;
  const liveGame = livePlay.data.game;
  const localPresentation = workingPresentationProfile && savedPresentationProfile
    ? resolvePresentationProfilePreview(workingPresentationProfile, savedPresentationProfile.revision)
    : null;
  const effectivePresentation = localPresentation ?? play.data.presentation;
  const goalPresets = scenarioGoalPresets(play.data.scenario_metadata);
  const roadmapNodeNames = {
    ...Object.fromEntries(play.data.visible_nodes.map((node) => [node.key, node.name])),
  };
  const roadmapRegionNames = {
    ...roadmapNodeNames,
    ...Object.fromEntries(
      Object.entries(play.data.resource_intelligence?.regions ?? {}).flatMap(([key, region]) => (
        region.region_name ? [[key, region.region_name]] : []
      )),
    ),
  };
  const roadmapResourceNames = {
    ...Object.fromEntries(play.data.resources.map((resource) => [resource.key, resource.name])),
    ...Object.fromEntries(
      Object.values(play.data.resource_intelligence?.regions ?? {}).flatMap((region) => (
        Object.entries(region.resources).map(([key, resource]) => [key, resource.resource_name])
      )),
    ),
  };
  const roadmapFactNames = Object.fromEntries(
    play.data.known_facts.map((fact) => [publicFactIdentity(fact.node_key, fact.fact_key), factDisplayLabel(fact)]),
  );
  const roadmapFactValues = Object.fromEntries(
    play.data.known_facts.map((fact) => [publicFactIdentity(fact.node_key, fact.fact_key), fact.value]),
  );
  const roadmapFactSafeMetadata = Object.fromEntries(
    play.data.known_facts.map((fact) => [
      publicFactIdentity(fact.node_key, fact.fact_key),
      fact.node_family != null || fact.presentation_slot != null || fact.value_label != null,
    ]),
  );
  const roadmapFactValueLabels = Object.fromEntries(
    play.data.known_facts.flatMap((fact) => fact.value_label
      ? [[publicFactIdentity(fact.node_key, fact.fact_key), fact.value_label]]
      : []),
  );
  const selectedTaskLoading = Boolean(
    selectedTaskId !== null && loadedTask?.id !== selectedTaskId,
  );
  const task =
    selectedTaskId !== null
      ? selectedTaskLoading
        ? null
        : loadedTask
      : acceptedTask && loadedTask?.id !== acceptedTask.id
        ? acceptedTask
        : loadedTask ?? acceptedTask;
  const taskExplanation = task
    ? taskExplanationLabel(task.status, task.explanation)
    : null;
  // The live projection is the authoritative GameInstance-level source for
  // whether any Task is still active.  Do not infer this from acceptedTask:
  // that local response remains in memory after the Task reaches a terminal
  // state and would incorrectly hide the GameInstance Goal Composer.
  const activeTaskId = liveGame.active_task_id;
  const gameHasActiveTask = activeTaskId !== null;
  const selectedTaskActive = Boolean(
    task &&
      activeTaskId === task.id &&
      ["ACTIVE", "NEEDS_PLAYER_INPUT"].includes(task.status),
  );
  const busy =
    submit.isPending ||
    confirmGoal.isPending ||
    startPlanning.isPending ||
    abandon.isPending ||
    archive.isPending ||
    checkpoint.isPending ||
    fork.isPending ||
    decision.isPending ||
    pacing.isPending ||
    continuous.isPending ||
    continuousExecuting ||
    replan.isPending;
  const mutationError =
    confirmGoal.error ?? startPlanning.error ?? abandon.error ?? archive.error ?? checkpoint.error ?? fork.error ?? decision.error ?? pacing.error ?? continuous.error ?? replan.error;
  const projectedGoalDraft = livePlay.data.current_goal_draft ?? play.data.current_goal_draft ?? null;
  const responseGoalDraft: PublicResolvedGoalDraft | null =
    lastParseResult?.status === "READY_FOR_CONFIRMATION" && lastParseResult.draft_id
      ? {
        draft_id: lastParseResult.draft_id,
        submitted_goal: lastParseResult.submitted_goal,
        presentation_text: lastParseResult.presentation_text,
        status: "READY",
        created_at: "",
      }
      : null;
  const readyGoalDraft = resolvingGoal || acceptedTask
    ? null
    : lastParseResult === null
      ? projectedGoalDraft
      : responseGoalDraft;
  const goalSubmissionFeedback = goalFeedback;
  const viewedTaskId =
    selectedTaskId ?? activeTaskId ?? task?.id ?? acceptedTask?.id ?? null;
  const operationSelected = Boolean(
    activeOperation !== null && viewedTaskId === activeOperation.taskId,
  );
  const planningForTask = operationSelected && activeOperation?.kind === "planning";
  const replanningForTask = operationSelected && activeOperation?.kind === "replanning";
  const goalAccepted = task?.execution_phase === "AWAITING_PLAN_START";
  const successDebrief = task?.execution_phase === "AWAITING_DEBRIEF_ACK";
  const failureDebrief = task?.execution_phase === "AWAITING_REPLAN_ACK";
  const planInvalidatedDebrief = Boolean(
    failureDebrief && task?.debrief?.success && task.debrief.plan_invalidated,
  );
  const segmentCompleteDebrief = Boolean(
    failureDebrief && task?.debrief?.success && !planInvalidatedDebrief,
  );
  const segmentCompletion = segmentCompletionMessage(
    segmentCompleteDebrief,
    planInvalidatedDebrief,
  );

  return (
    <main className="game-console">
      {mutationError && (
        <div className="console-error">
          <strong>命令无法继续</strong>
          <span>{confirmGoal.error ? confirmGoalErrorText(confirmGoal.error) : errorText(mutationError)}</span>
        </div>
      )}
      <section className="scenario-strip">
        <div><span>场景</span><strong>{game.scenario_name}</strong></div>
        <div><span>实例</span><strong>{game.id.slice(0, 8)}</strong></div>
        <div><span>精确版本</span><strong>版本 {game.scenario_version_number}</strong></div>
        <div><span>运行状态</span><strong className={`console-pill ${game.status === "ACTIVE" ? "success" : "neutral"}`}>{uiLabel(game.status)}</strong></div>
        <div className="scenario-strip-action"><span>界面</span><button type="button" data-testid="game-presentation-settings-button" onClick={() => setPresentationDrawerOpen((open) => !open)}>{presentationDrawerOpen ? "关闭设置" : "界面设置"}</button></div>
      </section>
      {presentationDrawerOpen && <aside className="presentation-drawer" data-testid="game-presentation-drawer">
        <header className="presentation-drawer-heading"><div><p className="eyebrow">Live preview</p><h2>界面设置</h2></div><button type="button" onClick={() => setPresentationDrawerOpen(false)}>关闭</button></header>
        {presentationProfileQuery.isLoading && <p className="muted">正在加载当前 PresentationProfile……</p>}
        {presentationProfileQuery.error && <p className="error">无法加载界面设置。</p>}
        {workingPresentationProfile && <PresentationSettingsPanel
          profile={workingPresentationProfile}
          compact
          onChange={(next) => {
            setWorkingPresentationProfile(next);
            setPresentationSaveState((current) => current === "CONFLICT" ? current : "DIRTY");
          }}
          disabled={saveGamePresentation.isPending || liveGame.status !== "ACTIVE"}
        />}
        {savedPresentationProfile && workingPresentationProfile && <footer className="presentation-drawer-footer">
          <span className={`save-state ${presentationSaveState.toLowerCase()}`} data-testid="game-presentation-save-state">{presentationSaveState}</span>
          <span>修订 {savedPresentationProfile.revision}</span>
          <button type="button" disabled={saveGamePresentation.isPending || liveGame.status !== "ACTIVE"} onClick={() => { setWorkingPresentationProfile(clonePresentationProfile(savedPresentationProfile.profile)); setPresentationSaveState("CLEAN"); }}>放弃修改</button>
          <button type="button" className="primary-button" disabled={saveGamePresentation.isPending || presentationSaveState !== "DIRTY" || liveGame.status !== "ACTIVE"} onClick={() => saveGamePresentation.mutate()}>{saveGamePresentation.isPending ? "正在保存……" : "保存并刷新当前游戏"}</button>
        </footer>}
        {(saveGamePresentation.error || presentationSaveState === "CONFLICT") && <p className="error">{presentationSaveState === "CONFLICT" ? <><span>保存冲突：服务器修订已变化。</span> <button type="button" onClick={() => void reloadGamePresentation()}>重新加载服务器版本</button></> : "保存失败，请稍后重试。"}</p>}
      </aside>}
      <section className="command-grid">
        <aside className="command-panel world-panel">
          <header className="command-panel-heading"><div><p>01 · 世界</p><h1>已知世界</h1></div><span className="console-pill success">玩家可见</span></header>
          <KnownWorldAccordions
            presentation={effectivePresentation}
            resources={play.data.resources}
            publicResourceNames={roadmapResourceNames}
            resourceIntelligence={play.data.resource_intelligence}
            visibleNodes={play.data.visible_nodes}
            actors={play.data.actors}
            knownFacts={play.data.known_facts}
            knownRelations={play.data.known_relations}
            knownActionRequirements={play.data.known_action_requirements}
            knownTargetActionContracts={play.data.known_target_action_contracts}
            knownProducerBindings={play.data.known_producer_bindings}
            task={task}
            resourceTask={selectedTaskActive ? task : null}
          />
        </aside>
        <div className="conversation-column-v2">
          <MissionLogPanel>
            <header className="command-panel-heading">
              <div><p>02 · 历史</p><h1>任务执行记录</h1></div>
              <span className="console-pill neutral">已发生事件</span>
            </header>
            <TaskTabs
              tasks={play.data.task_history}
              inheritedTaskCount={play.data.game.inherited_task_count}
              selectedTaskId={selectedTaskId ?? task?.id ?? null}
              onSelect={setSelectedTaskId}
            />
            <div className="timeline-scroll">
              <Timeline task={task} presentation={effectivePresentation} />
              {planningForTask && activeOperation && (
                <WaitingStatus
                  startedAt={activeOperation.startedAt}
                  label="Agent 正在规划"
                  testId="planning-status"
                />
              )}
              {replanningForTask && activeOperation && (
                <WaitingStatus
                  startedAt={activeOperation.startedAt}
                  label="Agent 正在根据最新情况重新规划"
                  testId="replanning-status"
                />
              )}
              {selectedTaskLoading && (
                <div className="task-loading-notice" role="status">
                  正在加载所选任务记录……
                </div>
              )}
            </div>
          </MissionLogPanel>
          {task && (
          <section className="command-panel current-report-panel" data-task-id={task.id}>
            <header className="command-panel-heading">
              <div><p>当前 · 汇报</p><h1>Agent 当前汇报</h1></div>
              <span className="console-pill warning">逐步确认</span>
            </header>
            {task && goalAccepted && selectedTaskActive && !planningForTask && !replanningForTask && (
              <section className="player-checkpoint goal-accepted-card" data-testid="goal-accepted-card">
                <small>目标已接受</small>
                <h2>{task.goal}</h2>
                <p>Agent 理解为：{taskObjectiveLabel(task.goal, task.objective_names)}</p>
                <button
                  disabled={busy}
                  onClick={() => startPlanning.mutate({ version: task.pacing_version, taskId: task.id })}
                >
                  {startPlanning.isPending ? "正在规划……" : "不错，开始规划"}
                </button>
              </section>
            )}
            {task && task.execution_phase === "AWAITING_ACTION_ACK" && task.briefing && selectedTaskActive && !planningForTask && !replanningForTask && (
              <section className="player-checkpoint action-briefing">
                <small>下一步行动</small>
                <h2>{task.briefing.actor_name} 准备执行</h2>
                <p><strong>{task.briefing.action_name}</strong> · 目标：{task.briefing.target_name}</p>
                <ActionLocationLine location={task.briefing.location} />
                <p>{task.briefing.purpose}</p>
                <ActionExecutionControls
                  disabled={busy}
                  starting={pacing.isPending}
                  continuousExecuting={continuousExecuting}
                  onStart={() => pacing.mutate({ phase: "action", version: task.pacing_version })}
                  onContinuous={() => {
                    if (!continuousExecuting) continuous.mutate(task.pacing_version);
                  }}
                />
              </section>
            )}
            {task && (successDebrief || failureDebrief) && task.debrief && !planningForTask && !replanningForTask && (
              <section className={`player-checkpoint action-debrief ${task.debrief.success ? "success" : "failed"}`}>
                <small>行动汇报</small>
                <h2>{task.debrief.success ? "✓" : "✕"} {task.debrief.action_name}</h2>
                <ActionLocationLine location={task.debrief.location} />
                <p>{task.debrief.result_summary}</p>
                {task.debrief.knowledge_changes.length > 0 && <>
                  <h3>新获知识</h3>
                  <ul>{task.debrief.knowledge_changes.map((change) => <li key={change.key}>{change.name}{change.value !== null ? `：${typeof change.value === "string" ? uiLabel(change.value) : String(change.value)}` : ""}</li>)}</ul>
                </>}
                {segmentCompletion && (
                  <div
                    className={planInvalidatedDebrief ? "plan-invalidation-message" : "segment-complete-message"}
                    data-testid={planInvalidatedDebrief ? "plan-invalidation-message" : "segment-complete-message"}
                  >
                    <strong>{segmentCompletion.title}</strong>
                    <p>{segmentCompletion.detail}</p>
                  </div>
                )}
                <button
                  disabled={busy || !selectedTaskActive}
                  onClick={() => {
                    if (failureDebrief) {
                      replan.mutate({ version: task.pacing_version, taskId: task.id });
                    } else {
                      pacing.mutate({ phase: "debrief", version: task.pacing_version });
                    }
                  }}
                >
                  {debriefButtonLabel({
                    failureDebrief,
                    segmentCompleteDebrief,
                    planInvalidated: planInvalidatedDebrief,
                    replanning: replan.isPending,
                  })}
                </button>
              </section>
            )}
            {play.data.pending_approval_id && task.execution_phase === "APPROVAL_REQUIRED" && selectedTaskActive && <div className="console-approval"><small>需要你的决定</small><strong>该行动超出了 Agent 的自主权限，需要你的批准。</strong><div><button disabled={busy} onClick={() => decision.mutate({ approve: true, decisionId: play.data.pending_approval_id!, taskVersion: task.version })}>批准</button><button className="danger-button" disabled={busy} onClick={() => decision.mutate({ approve: false, decisionId: play.data.pending_approval_id!, taskVersion: task.version })}>拒绝并重新规划</button></div></div>}
            {["COMPLETED", "BLOCKED", "ABORTED"].includes(task.execution_phase) && <div className={`current-report-terminal ${taskTone[task.status] ?? "neutral"}`}><strong>{uiLabel(task.status)}</strong><p>{task.explanation ?? "当前任务已经结束。你可以查看完整记录与计划历史。"}</p></div>}
            {liveGame.status !== "ACTIVE" && <p className="archived-notice">游戏已归档，当前为只读状态。</p>}
          </section>
          )}
          {liveGame.status === "ACTIVE" && !gameHasActiveTask && (
            <GoalComposer
              goal={goal}
              pendingGoal={pendingGoal}
              resolving={goalResolving}
              startedAt={goalResolving ? activeOperation?.startedAt ?? null : null}
              busy={busy}
              feedback={goalSubmissionFeedback}
              readyDraft={readyGoalDraft}
              confirming={confirmGoal.isPending}
              goalPresets={goalPresets}
              presetsLoaded={play.isFetched}
              onGoalChange={setGoal}
              onSubmit={() => submit.mutate()}
              onConfirm={(draftId) => confirmGoal.mutate(draftId)}
            />
          )}
        </div>
        <aside className="command-panel execution-panel-v2">
          <header className="command-panel-heading"><div><p>03 · 计划</p><h1>计划演进</h1></div></header>
          {planningForTask && <p className="plan-waiting-message">正在生成初始方案……</p>}
          {replanningForTask && <p className="plan-waiting-message">正在生成调整方案……</p>}
          {task && goalAccepted && !planningForTask && !replanningForTask && <p className="plan-waiting-message">尚未开始规划。</p>}
          {!task && !selectedTaskLoading && <p className="console-empty">等待下达第一个目标。</p>}
          {selectedTaskLoading && <p className="plan-waiting-message">正在加载所选任务记录……</p>}
          {task && (
            <div className="task-brief">
              <small>当前目标</small>
              <TaskGoalHeading goal={task.goal} status={task.status} />
              {taskExplanation && taskExplanation !== task.goal && <code>{taskExplanation}</code>}
              {task.roadmap.stages.length > 0 && (
                <MissionRoadmap
                  key={task.id}
                  stages={task.roadmap.stages}
                  presentation={effectivePresentation}
                  regionNames={roadmapRegionNames}
                  resourceNames={roadmapResourceNames}
                  nodeNames={roadmapNodeNames}
                  factNames={roadmapFactNames}
                  factValues={roadmapFactValues}
                  factSafeMetadata={roadmapFactSafeMetadata}
                  factValueLabels={roadmapFactValueLabels}
                />
              )}
            </div>
          )}
          {task && !planningForTask && (
            <PlanHistory task={task} presentation={effectivePresentation} />
          )}
          {game.status === "ACTIVE" && selectedTaskActive && task && <button className="console-button danger-button full" disabled={busy} onClick={() => abandon.mutate(task.id)}>放弃当前目标</button>}
        </aside>
      </section>
      <section className="developer-bar-v2"><div><p>开发者控制</p><span>只有输入服务端配置的凭证后，浏览器前端才会读取内部状态。</span>{checkpointNotice && <small className="checkpoint-notice" role="status">{checkpointNotice}</small>}{game.status === "ACTIVE" && gameHasActiveTask && <small className="lifecycle-help">当前有活动任务，完成或放弃后才能归档。</small>}</div><div><button onClick={() => setDeveloperOpen((value) => !value)}>开发者视图</button>{game.status === "ACTIVE" && <><button disabled={busy || gameHasActiveTask} title={gameHasActiveTask ? "当前有活动任务，完成或放弃后才能存档" : undefined} onClick={() => checkpoint.mutate(liveGame.runtime_revision)}>存档</button><button className="danger-button" disabled={busy || gameHasActiveTask} title={gameHasActiveTask ? "当前有活动任务，完成或放弃后才能归档" : undefined} onClick={() => archive.mutate(liveGame.runtime_revision)}>结束并归档游戏</button></>}{game.status === "ARCHIVED" && <button disabled={busy} onClick={() => fork.fork(gameId)}>以此归档状态新开一局</button>}</div></section>
      {developerOpen && <section className="developer-panel-v2"><label>开发者凭证<input type="password" value={developerToken} onChange={(event) => setDeveloperToken(event.target.value)} /></label>{developer.error && <p className="developer-error">开发者访问被拒绝。</p>}{developer.data && <><h2>内部运行时快照</h2><pre>{JSON.stringify(developer.data, null, 2)}</pre></>}</section>}
    </main>
  );
}
