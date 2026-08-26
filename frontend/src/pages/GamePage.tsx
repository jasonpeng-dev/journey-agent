import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api";
import { groupActorsByTask } from "../actorPresentation";
import { useForkGame } from "../hooks/useForkGame";
import {
  factDisplayLabel,
  factDisplayValue,
  facilityStatusDisplayValue,
  generationCapabilityDisplayValue,
  resourceDisplayName,
  resourceAvailabilityRequirementText,
  knownRelationDescription,
  meaningfulKnownRelations,
  relationDisplayKey,
} from "../knowledgePresentation";
import type {
  ActionLocation,
  PublicPlanHistory,
  PublicPlanHistoryStep,
  PlayerGameState,
  PublicTargetActionContract,
  ResourceIntelligence,
  PublicTask,
  PublicTimelineEvent,
  ScenarioVersionDetail,
} from "../types";
import { errorText, resultLabel, stepDescription, uiLabel } from "../ui";
import {
  debriefButtonLabel,
  formatDuration,
  segmentCompletionMessage,
  syncPlayStateCaches,
  type ActivePlayOperation,
} from "../playPresentation";
import {
  actionLocationText,
  groupFactsByRegion,
  groupNodesByRegion,
  groupResourcesByRegion,
  meaningfulResult,
} from "../spatialPresentation";

const taskTone: Record<string, string> = {
  COMPLETED: "success",
  ACTIVE: "warning",
  NEEDS_PLAYER_INPUT: "warning",
  BLOCKED_BY_PLAYER_DECISION: "danger",
  UNREACHABLE_IN_CURRENT_STATE: "danger",
  MODEL_PLAN_REJECTED: "danger",
  MODEL_PROVIDER_TIMEOUT: "danger",
  MODEL_PROVIDER_FAILURE: "danger",
  ABORTED: "neutral",
};
const planStatusLabel: Record<PublicPlanHistory["status"], string> = {
  EXECUTING: "执行中",
  ADJUSTED: "已调整",
  COMPLETED: "已完成",
  BLOCKED: "已阻塞",
};
const planStepMark: Record<PublicPlanHistoryStep["status"], string> = {
  PLANNED: "○",
  CURRENT: "●",
  COMPLETED: "✓",
  FAILED: "✕",
  CANCELLED: "–",
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

function planBeforeReplan(task: PublicTask, event: PublicTimelineEvent): PublicPlanHistory | null {
  if (event.kind !== "PLAN_UPDATED") return null;
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

export function Timeline({ task }: { task: PublicTask | null }) {
  const timelineRef = useRef<HTMLDivElement>(null);
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
    <div ref={timelineRef} className="mission-timeline" aria-live="polite">
      {task.timeline.map((event) => {
        const base = timelinePresentation[event.kind];
        const presentation =
          event.kind === "ACTION_RESULT" && event.success === false
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
        const headline = isApproval
          ? stepDescription(event.title)
          : event.kind === "GOAL_ACCEPTED"
            ? "任务已接受"
            : event.kind === "PLAN_CREATED"
              ? "Agent 已完成计划"
              : event.kind === "PLAN_UPDATED"
                ? "Agent 已重新规划"
              : event.kind === "TASK_STARTED"
            ? "任务已接受"
            : event.kind.startsWith("TASK_")
              ? presentation.label
              : event.title;
        const planReason = replanReason(task, event);
        return (
          <article className={`timeline-entry ${presentation.tone}`} key={event.id}>
            <span className="timeline-mark">{presentation.mark}</span>
            <div className="timeline-entry-content">
              <div className="timeline-entry-heading">
                <small>
                  {eventLabel}
                  {event.actor_name ? ` · ${event.actor_name}` : ""}
                </small>
                {formatDuration(event.duration_ms) && (
                  <small className="timeline-duration">· {formatDuration(event.duration_ms)}</small>
                )}
              </div>
              <strong>
                {headline}
                {event.kind === "ACTION_RESULT" && actionLocationText(event.location)
                  ? ` · ${actionLocationText(event.location)}`
                  : ""}
              </strong>
              {planReason && <p className="timeline-plan-reason">{planReason}</p>}
              {event.kind !== "ACTION_RESULT" && <ActionLocationLine location={event.location} />}
              {event.detail && !event.kind.startsWith("PLAN_") && (
                <p>
                  说明：
                  {uiLabel(event.detail)}
                </p>
              )}
              {!event.kind.startsWith("PLAN_") && meaningfulResult(event.result_summary) && (
                <p>{resultLabel(meaningfulResult(event.result_summary))}</p>
              )}
              {!event.kind.startsWith("PLAN_") && event.knowledge_changes.length > 0 && (
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

export function PlanHistory({ task }: { task: PublicTask }) {
  const latestId = task.plan_history.at(-1)?.id ?? null;
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(latestId ? [latestId] : []),
  );
  useEffect(() => {
    setExpanded(new Set(latestId ? [latestId] : []));
  }, [latestId]);
  if (!task.plan_history.length) return <p className="console-empty">尚未生成执行方案。</p>;
  return (
    <div className="plan-history">
      {task.plan_history.map((plan) => {
        const open = expanded.has(plan.id);
        const markerSequence = interruptionMarkerSequence(plan);
        const interruption = plan.interruption;
        const title = plan.ordinal === 1 ? "初始方案" : `调整方案 ${plan.ordinal - 1}`;
        return (
          <section className={`plan-history-card ${plan.status.toLowerCase()}`} key={plan.id}>
            <button
              className="plan-history-toggle"
              type="button"
              aria-expanded={open}
              onClick={() =>
                setExpanded((current) => {
                  const next = new Set(current);
                  if (next.has(plan.id)) next.delete(plan.id);
                  else next.add(plan.id);
                  return next;
                })
              }
            >
              <span>
                <strong>
                  {title} · {planStatusLabel[plan.status]}
                </strong>
                <small>
                  {plan.completed_steps}/{plan.total_steps} 完成
                  {plan.interruption
                    ? ` · ${plan.interruption.step_name} ${
                        plan.interruption.kind === "KNOWLEDGE_CONFLICT" ? "冲突" : "失败"
                      }`
                    : plan.failed_step_name
                      ? ` · ${plan.failed_step_name} 失败`
                      : ""}
                </small>
              </span>
              <b>{open ? "收起" : "展开"}</b>
            </button>
            {open && (
              <ol className="plan-history-steps">
                {plan.steps.map((step) => (
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
                        <ActionLocationLine location={step.location} />
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
  resources: PlayerGameState["resources"];
  resourceIntelligence?: ResourceIntelligence;
  visibleNodes: PlayerGameState["visible_nodes"];
  actors: PlayerGameState["actors"];
  knownFacts: PlayerGameState["known_facts"];
  knownRelations?: NonNullable<PlayerGameState["known_relations"]>;
  knownTargetActionContracts?: PublicTargetActionContract[];
  task?: PublicTask | null;
};

export function KnownWorldAccordions({
  resources,
  resourceIntelligence,
  visibleNodes,
  actors,
  knownFacts,
  knownRelations = [],
  knownTargetActionContracts = [],
  task = null,
}: KnownWorldAccordionsProps) {
  const [expanded, setExpanded] = useState<Record<KnowledgeAccordionKey, boolean>>({
    resources: true,
    locations: false,
    actors: false,
    facts: false,
    relations: false,
  });
  const [expandedFacilities, setExpandedFacilities] = useState<Record<string, boolean>>({});
  const toggle = (id: KnowledgeAccordionKey) => {
    setExpanded((current) => ({ ...current, [id]: !current[id] }));
  };
  const resourceGroups = groupResourcesByRegion(resources);
  const locationGroups = groupNodesByRegion(visibleNodes);
  const actorGroups = groupActorsByTask(actors, task);
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
  const isRegionNode = (node: PlayerGameState["visible_nodes"][number] | undefined) =>
    node?.node_type_key === "region";
  const isFacilityNode = (node: PlayerGameState["visible_nodes"][number] | undefined) =>
    node?.node_type_key === "facility";
  const isTransportNode = (node: PlayerGameState["visible_nodes"][number] | undefined) =>
    node?.node_type_key === "transport";

  knownFacts.forEach((fact) => {
    const node = nodeByKey.get(fact.node_key);
    const facts = factsByNode.get(fact.node_key) ?? [];
    facts.push(fact);
    factsByNode.set(fact.node_key, facts);
    if (isFacilityNode(node) || fact.node_type_key === "facility" || isTransportNode(node)) {
      assignedFactKeys.add(factIdentity(fact));
      return;
    }
    if (isRegionNode(node) || fact.node_type_key === "region") {
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
  const contractsByTarget = new Map<string, PublicTargetActionContract[]>();
  knownTargetActionContracts.forEach((contract) => {
    const contracts = contractsByTarget.get(contract.target_key) ?? [];
    contracts.push(contract);
    contractsByTarget.set(contract.target_key, contracts);
  });
  const resourceNames = new Map<string, string>(resources.map((resource) => [resource.key, resource.name]));
  if (resourceIntelligence) {
    Object.values(resourceIntelligence.regions).forEach((region) => {
      Object.entries(region.resources).forEach(([key, resource]) => resourceNames.set(key, resource.resource_name));
    });
    Object.entries(resourceIntelligence.global_resources).forEach(([key, resource]) => resourceNames.set(key, resource.resource_name));
  }
  const resourceName = (key: string, candidate?: string) =>
    resourceDisplayName(key, candidate ?? resourceNames.get(key));
  const statusTone = (value: string | number | boolean) =>
    value === false || value === 0 || value === "false" || value === "UNKNOWN" || value === "UNAVAILABLE" || value === "BLOCKED"
      ? "neutral"
      : "success";
  const facilityFactsFor = (nodeKey: string) => factsByNode.get(nodeKey) ?? [];
  const targetContractsFor = (nodeKey: string) => contractsByTarget.get(nodeKey) ?? [];
  const facilityMetadataFacts = new Set(["operational", "power_supply", "power_generation_capable", "generation_capable", "repair_profile"]);


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
                      <small>{knownRelationDescription(relation.relation_type_key)}</small>
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
                  const powerFact = nodeFacts.find((fact) => fact.fact_key === "power_supply");
                  const operationalFact = nodeFacts.find((fact) => fact.fact_key === "operational");
                  const passabilityFact = nodeFacts.find((fact) => fact.fact_key === "passable");
                  const generationFact = nodeFacts.find(
                    (fact) => fact.fact_key === "power_generation_capable" || fact.fact_key === "generation_capable",
                  );
                  const hasPowerOutputRelation = nodeRelations.some(
                    (relation) =>
                      relation.source_node_key === node.key
                      && relation.relation_type_key === "supplies_power_to",
                  );
                  const hasGenerationSemantics = generationFact?.value === true;
                  const targetContracts = targetContractsFor(node.key);
                  const additionalFacts = nodeFacts.filter((fact) => !facilityMetadataFacts.has(fact.fact_key));
                  const associatedResources = (node.associated_known_resources ?? [])
                    .filter((resource) => resource.availability !== "AVAILABLE");
                  const hasFacilityDetails = targetContracts.length > 0
                    || associatedResources.length > 0
                    || hasPowerOutputRelation
                    || hasGenerationSemantics
                    || additionalFacts.length > 0
                    || nodeRelations.length > 0;
                  const repairRequirementRows = targetContracts.map((contract) => {
                    const parts = [
                      ...Object.entries(contract.cost ?? {}).map(
                        ([resourceKey, amount]) => `${resourceName(resourceKey)} ×${String(amount)}`,
                      ),
                      ...(contract.special_requirements ?? []).map((requirement) => {
                        const fact = facilityFactsFor(contract.target_key).find(
                          (item) => item.fact_key === requirement.fact_key,
                        );
                        return `前置条件：${fact ? factDisplayLabel(fact) : "已知状态"}`;
                      }),
                    ];
                    return {
                      key: node.key + ":requirement:" + contract.action_key,
                      value: parts.length > 0 ? parts.join("、") : contract.action_name,
                    };
                  });
                  const repairTeamNames = [
                    ...new Set(
                      targetContracts
                        .map((contract) => contract.required_actor_role_name)
                        .filter((name): name is string => typeof name === "string" && name.length > 0),
                    ),
                  ];
                  const associatedResourceText = associatedResources
                    .map((resource) => {
                      const resourceKey = typeof resource.resource_key === "string" ? resource.resource_key : "";
                      const name = resourceName(
                        resourceKey,
                        typeof resource.resource_name === "string" ? resource.resource_name : undefined,
                      );
                      const quantity = resource.quantity !== null && resource.quantity !== undefined
                        ? ` \u00d7${String(resource.quantity)}`
                        : "";
                      const availability = resource.availability === "UNAVAILABLE"
                        ? "\u6682\u4e0d\u53ef\u7528"
                        : resource.availability === "AVAILABLE"
                          ? "\u53ef\u7528"
                          : "";
                      const requirement = resourceRequirementText(resource.availability_requirement);
                      return [name + quantity, availability, requirement].filter(Boolean).join("\uff0c");
                    })
                    .join("\uff0c");
                  const relationLabels = new Map<string, string[]>();
                  nodeRelations.forEach((relation) => {
                    const label = relation.relation_type_key === "supplies_power_to"
                      ? "\u53ef\u4f9b\u7535"
                      : knownRelationDescription(relation.relation_type_key);
                    const targets = relationLabels.get(label) ?? [];
                    targets.push(nodeDisplayName(relation.target_node_key, relation.target_node_name));
                    relationLabels.set(label, targets);
                  });
                  const facilityRelationRows = Array.from(relationLabels.entries()).map(([label, targets], index) => ({
                    key: node.key + ":relation:" + index,
                    label,
                    value: targets.join("\u3001"),
                  }));

                  if (transport) {
                    return (
                      <div className="knowledge-transport-card" data-testid={"transport-card-" + node.key} key={node.key}>
                        <div className="knowledge-transport-heading">
                          <span>
                            <strong>{node.name}</strong>
                            <small>{node.endpoint_region_names?.join(" ↔ ") ?? group.name}</small>
                          </span>
                          <span className={"knowledge-facility-status " + statusTone(passabilityFact?.value ?? "UNKNOWN")}>
                            {passabilityFact ? factDisplayValue(passabilityFact) : "待探索"}
                          </span>
                          <span className="knowledge-transport-column-spacer" aria-hidden="true" />
                        </div>
                      </div>
                    );
                  }

                  if (facility) {
                    const facilityOpen = expandedFacilities[node.key] === true;
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
                          <span className="knowledge-facility-statuses">
                            <span className={"knowledge-facility-status " + statusTone(powerFact?.value ?? "UNKNOWN")}>
                              {powerFact ? factDisplayValue(powerFact) : "供电未知"}
                            </span>
                            <span className={"knowledge-facility-status " + statusTone(operationalFact?.value ?? "UNKNOWN")}>
                              {operationalFact ? facilityStatusDisplayValue(operationalFact) : "状态未知"}
                            </span>
                          </span>
                          <span className="knowledge-facility-toggle" aria-hidden="true">
                            {facilityOpen ? "-" : "+"}
                          </span>
                        </summary>
                        <div className="knowledge-facility-details">
                          {repairRequirementRows.map((row) => (
                            <div className="knowledge-facility-attribute" key={row.key}>
                              <span className="knowledge-facility-attribute-label">{"修复需求："}</span>
                              <span className="knowledge-facility-attribute-value">{row.value}</span>
                            </div>
                          ))}
                          {repairTeamNames.map((name) => (
                            <div className="knowledge-facility-attribute" key={node.key + ":team:" + name}>
                              <span className="knowledge-facility-attribute-label">{"执行队伍："}</span>
                              <span className="knowledge-facility-attribute-value">{name}</span>
                            </div>
                          ))}
                          {associatedResources.length > 0 && (
                            <div className="knowledge-facility-attribute">
                              <span className="knowledge-facility-attribute-label">{"关联资源："}</span>
                              <span className="knowledge-facility-attribute-value">{associatedResourceText}</span>
                            </div>
                          )}
                          {hasPowerOutputRelation && (
                            <div className="knowledge-facility-attribute">
                              <span className="knowledge-facility-attribute-label">{"送电能力："}</span>
                              <span className="knowledge-facility-attribute-value">
                                {operationalFact?.value === true
                                && (powerFact?.value === "AVAILABLE" || powerFact?.value === true)
                                  ? "已具备"
                                  : "未具备"}
                              </span>
                            </div>
                          )}
                          {hasGenerationSemantics && generationFact && (
                            <div className="knowledge-facility-attribute">
                              <span className="knowledge-facility-attribute-label">{"发电能力："}</span>
                              <span className="knowledge-facility-attribute-value">
                                {generationCapabilityDisplayValue(generationFact)}
                              </span>
                            </div>
                          )}
                          {additionalFacts.map((fact) => (
                            <div className="knowledge-facility-attribute" key={"facility-fact:" + factIdentity(fact)}>
                              <span className="knowledge-facility-attribute-label">{factDisplayLabel(fact) + "："}</span>
                              <span className="knowledge-facility-attribute-value">{factDisplayValue(fact)}</span>
                            </div>
                          ))}
                          {facilityRelationRows.map((row) => (
                            <div className="knowledge-facility-attribute" key={row.key}>
                              <span className="knowledge-facility-attribute-label">{row.label + "："}</span>
                              <span className="knowledge-facility-attribute-value">{row.value}</span>
                            </div>
                          ))}
                          {!hasFacilityDetails && <div className="knowledge-empty-state">{"暂无更多已知信息"}</div>}
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
                            <small>{knownRelationDescription(relation.relation_type_key)}</small>
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
        id="resources"
        countLabel={
          resourceIntelligence
            ? resourceIntelligence.visible_region_count + " / " + resourceIntelligence.total_regions
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
            {Object.entries(resourceIntelligence.regions)
              .filter(
                ([, region]) =>
                  region.resource_inventory_visibility === "VISIBLE" ||
                  Object.keys(region.resources).length > 0,
              )
              .map(([regionKey, region]) => (
                <details className="knowledge-region" open key={regionKey}>
                  <summary>
                    <span>
                      <strong>{region.region_name ?? regionKey}</strong>
                      <small>{region.resource_survey_completed ? "已完成查探" : "可进行完整查探"}</small>
                    </span>
                  </summary>
                  <div className="knowledge-region-content">
                    <div className="knowledge-entry-list">
                      {Object.entries(region.resources).length === 0 ? (
                        <div className="knowledge-entry-empty">当前无已记录资源</div>
                      ) : (
                        Object.entries(region.resources).map(([resourceKey, resource]) => (
                          <div className="knowledge-entry" key={regionKey + ":" + resourceKey}>
                            <div className="knowledge-entry-copy">
                              <strong>{resource.resource_name}</strong>
                              {resource.pools
                                .filter((pool) => pool.availability === "UNAVAILABLE")
                                .map((pool, index) => (
                                  <small key={index}>
                                    暂不可用 {pool.quantity}
                                    {pool.facility_name ? ` · ${pool.facility_name}` : ""}
                                    {resourceRequirementText(pool.availability_requirement)
                                      ? " · " + resourceRequirementText(pool.availability_requirement)
                                      : ""}
                                  </small>
                                ))}
                            </div>
                            <span className="console-pill success knowledge-status-pill">
                              {resource.known_total == null || resource.known_total === resource.known_available
                                ? resource.known_available
                                : resource.known_available + " / " + resource.known_total}
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </details>
              ))}
            {Object.entries(resourceIntelligence.global_resources).length > 0 && (
              <details className="knowledge-region" open>
                <summary><span><strong>全局资源</strong></span></summary>
                <div className="knowledge-region-content">
                  <div className="knowledge-entry-list">
                    {Object.entries(resourceIntelligence.global_resources).map(([resourceKey, resource]) => (
                      <div className="knowledge-entry" key={"global:" + resourceKey}>
                        <div className="knowledge-entry-copy"><strong>{resource.resource_name}</strong></div>
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
            <details className="knowledge-region" open key={group.key}>
              <summary>
                <span>
                  <strong>{group.name}</strong>
                  <small>{group.items.length} 项资源</small>
                </span>
              </summary>
              <div className="knowledge-region-content">
                <div className="knowledge-entry-list">
                {group.items.map((resource) => (
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
      <KnowledgeAccordion
        id="locations"
        title="已知地点"
        count={visibleNodes.length}
        summary={"\u8bbe\u65bd\u72b6\u6001\u3001\u5df2\u77e5\u8d44\u6e90\u4e0e\u901a\u9053\u4fe1\u606f"}
        open={expanded.locations}
        onToggle={() => toggle("locations")}
      >
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
                        <strong>{actor.name}</strong>
                        <small>{actor.role_name}</small>
                      </div>
                      <div className="actor-status-pills">
                        <span className="console-pill success knowledge-status-pill">
                          {actor.current_node_name}
                        </span>
                        <span
                          className={`console-pill ${actor.command_reachability === "DISCONNECTED" ? "danger" : "success"} knowledge-status-pill`}
                        >
                          {actor.command_reachability === "DISCONNECTED" ? "失联" : "在线"}
                        </span>
                      </div>
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
                <small>{knownRelationDescription(relation.relation_type_key)}</small>
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
          <strong>{item.objective_names.join(" · ")}</strong>
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
  objectives = [],
  objectivesLoaded = false,
  onGoalChange,
  onSubmit,
}: {
  goal: string;
  pendingGoal: string | null;
  resolving: boolean;
  startedAt: number | null;
  busy: boolean;
  objectives?: Array<{ key: string; name: string }>;
  objectivesLoaded?: boolean;
  onGoalChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const CUSTOM_GOAL = "__custom_goal__";
  const [selectedObjectiveKey, setSelectedObjectiveKey] = useState("");
  const selectedObjective = objectives.find((item) => item.key === selectedObjectiveKey);
  const customGoalSelected = selectedObjectiveKey === CUSTOM_GOAL;
  const showCustomInput = !objectivesLoaded || objectives.length === 0 || customGoalSelected;
  const displayedGoal = resolving ? pendingGoal ?? goal : goal;
  const handleObjectiveChange = (value: string) => {
    setSelectedObjectiveKey(value);
    const objective = objectives.find((item) => item.key === value);
    onGoalChange(objective?.name ?? "");
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
          if (!resolving && goal.trim() && (showCustomInput || selectedObjective)) onSubmit();
        }}
      >
        <label htmlFor="objective-select">选择任务</label>
        <select
          id="objective-select"
          aria-label="选择任务"
          value={objectivesLoaded ? selectedObjectiveKey : ""}
          onChange={(event) => handleObjectiveChange(event.target.value)}
          disabled={resolving || !objectivesLoaded}
        >
          <option value="" disabled>
            {objectivesLoaded ? "请选择一个任务" : "正在加载任务……"}
          </option>
          {objectives.map((objective) => (
            <option key={objective.key} value={objective.key}>{objective.name}</option>
          ))}
          {objectivesLoaded && <option value={CUSTOM_GOAL}>自定义目标……</option>}
        </select>
        {showCustomInput && (
          <div>
            <label htmlFor="goal">自定义目标</label>
            <textarea
              id="goal"
              rows={3}
              value={displayedGoal}
              onChange={(event) => {
                setSelectedObjectiveKey(CUSTOM_GOAL);
                onGoalChange(event.target.value);
              }}
              placeholder="输入自定义目标"
              disabled={resolving}
            />
            <button disabled={resolving || !goal.trim() || busy} type="submit">
              {resolving ? "正在接收……" : "开始目标"}
            </button>
          </div>
        )}
        {!showCustomInput && selectedObjective && (
          <div className="goal-composer-selected">
            <strong>{selectedObjective?.name}</strong>
            <button disabled={resolving || !goal.trim() || busy} type="submit">
              {resolving ? "正在接收……" : "开始目标"}
            </button>
          </div>
        )}
        {resolving && startedAt !== null && (
          <WaitingStatus
            startedAt={startedAt}
            label="Agent 正在接收任务"
            testId="goal-resolving-status"
          />
        )}
        {!resolving && (
          <p>智能体只会选择当前精确版本中定义的目标和行动；场景作者定义的内容保持其原始语言。</p>
        )}
      </form>
    </section>
  );
}

function scenarioObjectiveOptions(
  version: ScenarioVersionDetail | undefined,
): Array<{ key: string; name: string }> {
  return (version?.definition_document.objectives ?? []).flatMap((item) => {
    if (typeof item.key !== "string" || typeof item.name !== "string" || !item.name.trim()) {
      return [];
    }
    return [{ key: item.key, name: item.name }];
  });
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
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [developerToken, setDeveloperToken] = useState("");
  const [checkpointNotice, setCheckpointNotice] = useState<string | null>(null);
  const play = useQuery({
    queryKey: ["play", gameId, selectedTaskId],
    queryFn: () => api.playState(gameId, selectedTaskId),
    placeholderData: (previous) => previous,
  });
  const livePlay = useQuery({
    queryKey: ["play", gameId, "live"],
    queryFn: () => api.playState(gameId, null),
    placeholderData: (previous) => previous,
  });
  const scenario = useQuery({
    queryKey: ["scenario", play.data?.game.scenario_id],
    queryFn: () => api.scenario(play.data!.game.scenario_id),
    enabled: Boolean(play.data?.game.scenario_id),
  });
  const scenarioVersion = useQuery({
    queryKey: ["scenario-version", play.data?.game.scenario_id, play.data?.game.scenario_version_id],
    queryFn: () => api.scenarioVersion(
      play.data!.game.scenario_id,
      play.data!.game.scenario_version_id,
    ),
    enabled: Boolean(play.data?.game.scenario_id && play.data?.game.scenario_version_id),
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
      setPendingGoal(goal);
      setAcceptedTask(null);
      setActiveOperation({ kind: "goal", taskId: null, startedAt: Date.now() });
    },
    onSuccess: (result) => {
      if (result.status === "ACCEPTED") {
        setGoal("");
        setAcceptedTask(result.task);
        if (result.task) setSelectedTaskId(result.task.id);
        setActiveOperation((operation) =>
          operation?.kind === "goal"
            ? { ...operation, taskId: result.task?.id ?? null }
            : operation,
        );
      }
      void refresh();
    },
    onError: () => {
      setPendingGoal(null);
      setActiveOperation(null);
    },
    onSettled: () => {
      setPendingGoal(null);
      setActiveOperation((operation) => (operation?.kind === "goal" ? null : operation));
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

  const loadedTask = play.data?.current_task ?? null;
  const resolvingGoal = submit.isPending || pendingGoal !== null;
  const goalResolving = resolvingGoal && activeOperation?.kind === "goal";

  if (!play.data || !livePlay.data) {
    return <main className="page"><p>正在加载游戏状态……</p></main>;
  }
  const { game } = play.data;
  const liveGame = livePlay.data.game;
  const objectiveOptions = scenarioObjectiveOptions(scenarioVersion.data);
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
    startPlanning.isPending ||
    abandon.isPending ||
    archive.isPending ||
    checkpoint.isPending ||
    fork.isPending ||
    decision.isPending ||
    pacing.isPending ||
    replan.isPending;
  const mutationError =
    submit.error ?? startPlanning.error ?? abandon.error ?? archive.error ?? checkpoint.error ?? fork.error ?? decision.error ?? pacing.error ?? replan.error;
  const resolutionMessage =
    submit.data && submit.data.status !== "ACCEPTED"
      ? submit.data.clarification_prompt ?? "输入的目标无法映射到当前精确场景版本定义的目标。"
      : null;
  const viewedTaskId =
    selectedTaskId ?? activeTaskId ?? task?.id ?? acceptedTask?.id ?? null;
  const operationSelected = Boolean(
    activeOperation !== null && viewedTaskId === activeOperation.taskId,
  );
  const planningForTask = operationSelected && activeOperation?.kind === "planning";
  const replanningForTask = operationSelected && activeOperation?.kind === "replanning";
  const goalAccepted = task?.execution_phase === "AWAITING_PLAN_START";
  const actionReady = task?.execution_phase === "AWAITING_ACTION_ACK";
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
      {(mutationError || resolutionMessage) && (
        <div className="console-error">
          <strong>命令无法继续</strong>
          <span>{mutationError ? errorText(mutationError) : resolutionMessage}</span>
        </div>
      )}
      <section className="scenario-strip">
        <div><span>场景</span><strong>{scenario.data?.name ?? "正在加载……"}</strong></div>
        <div><span>实例</span><strong>{game.id.slice(0, 8)}</strong></div>
        <div><span>精确版本</span><strong>版本 {game.scenario_version_number} · {game.scenario_content_hash.slice(0, 10)}</strong></div>
        <div><span>运行状态</span><strong className={`console-pill ${game.status === "ACTIVE" ? "success" : "neutral"}`}>{uiLabel(game.status)}</strong></div>
      </section>
      <section className="command-grid">
        <aside className="command-panel world-panel">
          <header className="command-panel-heading"><div><p>01 · 世界</p><h1>已知世界</h1></div><span className="console-pill success">玩家可见</span></header>
          <KnownWorldAccordions
            resources={play.data.resources}
            resourceIntelligence={play.data.resource_intelligence}
            visibleNodes={play.data.visible_nodes}
            actors={play.data.actors}
            knownFacts={play.data.known_facts}
            knownRelations={play.data.known_relations}
            knownTargetActionContracts={play.data.known_target_action_contracts}
            task={task}
          />
        </aside>
        <div className="conversation-column-v2">
          <section className="command-panel mission-log-panel">
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
              <Timeline task={task} />
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
          </section>
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
                <p>Agent 理解为：{task.objective_names.join("、")}</p>
                <button
                  disabled={busy}
                  onClick={() => startPlanning.mutate({ version: task.pacing_version, taskId: task.id })}
                >
                  {startPlanning.isPending ? "正在规划……" : "不错，开始规划"}
                </button>
              </section>
            )}
            {task && actionReady && task.briefing && selectedTaskActive && !planningForTask && !replanningForTask && (
              <section className="player-checkpoint action-briefing">
                <small>下一步行动</small>
                <h2>{task.briefing.actor_name} 准备执行</h2>
                <p><strong>{task.briefing.action_name}</strong> · 目标：{task.briefing.target_name}</p>
                <ActionLocationLine location={task.briefing.location} />
                <p>{task.briefing.purpose}</p>
                <button disabled={busy} onClick={() => pacing.mutate({ phase: "action", version: task.pacing_version })}>
                  {pacing.isPending ? "正在执行……" : "知悉，开始执行"}
                </button>
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
              objectives={objectiveOptions}
              objectivesLoaded={scenarioVersion.isFetched}
              onGoalChange={setGoal}
              onSubmit={() => submit.mutate()}
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
          {task && <div className="task-brief"><small>当前目标</small><strong>{task.goal}</strong><span className={`console-pill ${taskTone[task.status] ?? "neutral"}`}>{uiLabel(task.status)}</span><p>{task.objective_names.join(" · ")}</p>{task.explanation && <code>{uiLabel(task.explanation)}</code>}</div>}
          {task && !planningForTask && <PlanHistory task={task} />}
          {game.status === "ACTIVE" && selectedTaskActive && task && <button className="console-button danger-button full" disabled={busy} onClick={() => abandon.mutate(task.id)}>放弃当前目标</button>}
        </aside>
      </section>
      <section className="developer-bar-v2"><div><p>开发者控制</p><span>只有输入服务端配置的凭证后，浏览器前端才会读取内部状态。</span>{checkpointNotice && <small className="checkpoint-notice" role="status">{checkpointNotice}</small>}{game.status === "ACTIVE" && gameHasActiveTask && <small className="lifecycle-help">当前有活动任务，完成或放弃后才能归档。</small>}</div><div><button onClick={() => setDeveloperOpen((value) => !value)}>开发者视图</button>{game.status === "ACTIVE" && <><button disabled={busy || gameHasActiveTask} title={gameHasActiveTask ? "当前有活动任务，完成或放弃后才能存档" : undefined} onClick={() => checkpoint.mutate(liveGame.runtime_revision)}>存档</button><button className="danger-button" disabled={busy || gameHasActiveTask} title={gameHasActiveTask ? "当前有活动任务，完成或放弃后才能归档" : undefined} onClick={() => archive.mutate(liveGame.runtime_revision)}>结束并归档游戏</button></>}{game.status === "ARCHIVED" && <button disabled={busy} onClick={() => fork.fork(gameId)}>以此归档状态新开一局</button>}</div></section>
      {developerOpen && <section className="developer-panel-v2"><label>开发者凭证<input type="password" value={developerToken} onChange={(event) => setDeveloperToken(event.target.value)} /></label>{developer.error && <p className="developer-error">开发者访问被拒绝。</p>}{developer.data && <><h2>内部运行时快照</h2><pre>{JSON.stringify(developer.data, null, 2)}</pre></>}</section>}
    </main>
  );
}
