import { useEffect, useId, useMemo, useRef, useState } from "react";

import { authoredDisplayName, objectByKindAndKey, sectionForKind, type EntityKind, type JsonObject } from "../../editor";
import { editorLocatorHref } from "../../editor-locator";
import type { Locator, ReferenceEdge } from "../../types";
import { AuthoringActionButton } from "./AuthoringActionButton";
import { EditorDialog } from "./EditorDialog";

type Priority = "HIGH" | "MEDIUM" | "LOW";
type UsageConsumer = { kind: string; key: string | null; name: string; semanticType: string; href: string; stableKey?: string; referencePaths: string[] };
type UsageGroup = { key: string; kind: string; label: string; priority: Priority; consumers: UsageConsumer[] };

const kindLabels: Record<string, string> = {
  node_type: "节点类型",
  node: "世界实体",
  relation_type: "关系类型",
  relation: "关系实例",
  resource: "资源定义",
  role: "角色",
  actor: "参与者",
  interaction: "交互",
  action: "行动",
  action_parameter: "行动参数",
  action_outcome: "行动结果",
  rule: "规则",
  derived_state: "派生状态",
  public_reference: "公开术语",
  initialization: "初始化配置",
  planning: "规划配置",
  metadata: "场景设置",
};

const highValueSources: Record<string, Set<string>> = {
  node_type: new Set(["node"]),
  node: new Set(["node_type", "relation", "interaction", "action"]),
  interaction: new Set(["node", "action"]),
  relation_type: new Set(["relation"]),
  role: new Set(["actor", "action"]),
  actor: new Set(["action"]),
  resource: new Set(["initialization", "action", "rule", "derived_state"]),
  action: new Set(["actor", "rule"]),
  derived_state: new Set(["derived_state", "rule"]),
  node_fact: new Set(["rule", "action", "derived_state"]),
};

function priorityFor(targetKind: string, sourceKind: string): Priority {
  if (highValueSources[targetKind]?.has(sourceKind)) return "HIGH";
  if (sourceKind === "public_reference" || sourceKind === "public_knowledge" || sourceKind === "initialization" || sourceKind === "metadata" || sourceKind === "planning" || sourceKind === "actor") return "MEDIUM";
  if (sourceKind === "document") return "LOW";
  return "LOW";
}

function arrayOf(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function relationName(document: JsonObject, key: string): string {
  const relation = objectByKindAndKey(document, "relation", key)?.value;
  if (!relation) return key;
  const source = objectByKindAndKey(document, "node", String(relation.source_node_key ?? ""));
  const target = objectByKindAndKey(document, "node", String(relation.target_node_key ?? ""));
  const type = objectByKindAndKey(document, "relation_type", String(relation.relation_type_key ?? ""));
  return `${source?.name ?? relation.source_node_key ?? "节点"} · ${type?.name ?? relation.relation_type_key ?? "关系"} · ${target?.name ?? relation.target_node_key ?? "节点"}`;
}

function consumerDisplay(document: JsonObject, kind: string, key: string | null): { name: string; semanticType: string; stableKey?: string } {
  if (!key) return { name: kind === "initialization" ? "开局配置" : kind === "metadata" ? "场景语义设置" : "规划配置", semanticType: kindLabels[kind] ?? kind };
  if (kind === "relation") return { name: relationName(document, key), semanticType: "关系实例", stableKey: key };
  if (kind === "action_parameter" || kind === "action_outcome") {
    const [actionKey, nestedKey] = key.split(":", 2);
    const action = objectByKindAndKey(document, "action", actionKey);
    const listKey = kind === "action_parameter" ? "parameters" : "expected_outcomes";
    const nested = arrayOf(action?.value[listKey]).find((item) => (item.key ?? item.code) === nestedKey);
    const identity = kind === "action_parameter" ? nested?.name ?? nestedKey : nested?.name ?? nestedKey;
    return { name: `${action?.name ?? actionKey} · ${identity}`, semanticType: kind === "action_parameter" ? "行动参数" : "行动结果", stableKey: key };
  }
  if (["node_type", "node", "relation_type", "resource", "role", "actor", "interaction", "action", "rule", "derived_state", "public_reference"].includes(kind)) {
    const object = objectByKindAndKey(document, kind as EntityKind, key);
    return { name: object ? authoredDisplayName(object.value, object.name) : key, semanticType: kindLabels[kind] ?? kind, stableKey: key };
  }
  return { name: key, semanticType: kindLabels[kind] ?? kind, stableKey: key };
}

function consumerHref(edge: ReferenceEdge, scenarioId: string, document: JsonObject): string | null {
  const { object_kind: kind, object_key: key, field_path: fieldPath } = edge.source;
  if (kind === "initialization") {
    if (fieldPath === "start_node_key" || fieldPath === "primary_actor_key") {
      const query = new URLSearchParams({ domain: "basic", group: "entry", item: "bootstrap-entry" });
      return `/scenarios/${scenarioId}/edit/initialization?${query}`;
    }
    const poolMatch = fieldPath?.match(/^resource_pools\.(\d+)/);
    const poolIndex = poolMatch ? Number(poolMatch[1]) : -1;
    const pool = arrayOf((document.initialization as JsonObject | undefined)?.resource_pools)[poolIndex];
    if (pool && typeof pool.pool_key === "string") {
      const query = new URLSearchParams({ domain: "resources", group: "resource-pools", item: `pool:${pool.pool_key}` });
      return `/scenarios/${scenarioId}/edit/initialization?${query}`;
    }
    if (fieldPath?.startsWith("region_resource_knowledge.")) {
      const match = fieldPath.match(/^region_resource_knowledge\.(\d+)/);
      const item = match ? arrayOf((document.initialization as JsonObject | undefined)?.region_resource_knowledge)[Number(match[1])] : undefined;
      if (item && typeof item.region_key === "string") {
        const query = new URLSearchParams({ domain: "resources", group: "region-resource-knowledge", item: `region-knowledge:${item.region_key}` });
        return `/scenarios/${scenarioId}/edit/initialization?${query}`;
      }
    }
    return `/scenarios/${scenarioId}/edit/initialization`;
  }
  if (kind === "metadata") return `/scenarios/${scenarioId}/edit/overview${fieldPath ? `?focus_path=${encodeURIComponent(fieldPath)}` : ""}`;
  if (kind === "planning") return `/scenarios/${scenarioId}/edit/${fieldPath?.startsWith("recovery_hints") ? "planning-recovery" : "planning-instructions"}${fieldPath ? `?focus_path=${encodeURIComponent(fieldPath)}` : ""}`;
  if (kind === "action_parameter" || kind === "action_outcome") {
    const [actionKey, nestedKey] = (key ?? "").split(":", 2);
    const nestedPath = kind === "action_parameter" ? `parameters.${nestedKey}` : `expected_outcomes.${nestedKey}`;
    return actionKey ? editorLocatorHref({ owner: "entity", section: "actions", kind: "action", objectKey: actionKey, fieldPath: nestedPath }, scenarioId) : null;
  }
  if (!key || !["node_type", "node", "relation_type", "relation", "resource", "role", "actor", "interaction", "action", "rule", "derived_state", "public_reference"].includes(kind)) return null;
  return editorLocatorHref({ owner: "entity", section: sectionForKind(kind), kind: kind as EntityKind, objectKey: key, fieldPath }, scenarioId);
}

function matchingReferences(references: ReferenceEdge[], kind: string, key: string | null, fieldPath: string | null): ReferenceEdge[] {
  return references.filter((edge) => edge.target.object_kind === kind && edge.target.object_key === key && edge.target.field_path === fieldPath);
}

function targetName(document: JsonObject, target: Locator): string {
  if (target.object_kind === "node" && target.object_key && target.field_path?.startsWith("facts.")) {
    const factKey = target.field_path.slice("facts.".length);
    const node = objectByKindAndKey(document, "node", target.object_key);
    const fact = arrayOf(node?.value.facts).find((item) => item.key === factKey);
    return typeof fact?.name === "string" && fact.name.trim() ? fact.name : factKey;
  }
  return target.object_key ? consumerDisplay(document, target.object_kind, target.object_key).name : kindLabels[target.object_kind] ?? target.object_kind;
}

export function ReferenceUsageSection({ references, target, document, scenarioId }: { references: ReferenceEdge[]; target: Locator; document: JsonObject; scenarioId: string }) {
  const [activeGroupKey, setActiveGroupKey] = useState<string | null>(null);
  const triggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const reactId = useId();
  const safeTargetId = `${target.object_kind}-${target.object_key ?? "root"}-${target.field_path ?? "object"}`.replace(/[^a-zA-Z0-9_-]/g, "-");
  const groups = useMemo(() => {
    const byConsumer = new Map<string, { edges: ReferenceEdge[]; priority: Priority }>();
    for (const edge of matchingReferences(references, target.object_kind, target.object_key, target.field_path)) {
      const priority = priorityFor(target.object_kind === "node" && target.field_path ? "node_fact" : target.object_kind, edge.source.object_kind);
      if (priority === "LOW") continue;
      const id = `${edge.source.object_kind}:${edge.source.object_key ?? ""}`;
      const group = byConsumer.get(id) ?? { edges: [], priority };
      group.edges.push(edge);
      byConsumer.set(id, group);
    }
    const buckets = new Map<string, UsageGroup>();
    for (const { edges, priority } of byConsumer.values()) {
      const edge = edges[0];
      const kind = edge.source.object_kind;
      const key = edge.source.object_key;
      const groupKey = `${priority}:${kind}`;
      const bucket = buckets.get(groupKey) ?? { key: groupKey, kind, label: kindLabels[kind] ?? kind, priority, consumers: [] };
      const display = consumerDisplay(document, kind, key);
      const href = consumerHref(edge, scenarioId, document);
      if (href) bucket.consumers.push({ kind, key, ...display, href, referencePaths: [...new Set(edges.map((item) => item.source.field_path).filter((path): path is string => Boolean(path)))].sort() });
      buckets.set(groupKey, bucket);
    }
    return [...buckets.values()]
      .map((group) => ({ ...group, consumers: group.consumers.sort((a, b) => a.name.localeCompare(b.name)) }))
      .filter((group) => group.consumers.length > 0)
      .sort((a, b) => (a.priority === b.priority ? a.label.localeCompare(b.label) : a.priority === "HIGH" ? -1 : 1));
  }, [document, references, scenarioId, target.object_kind, target.object_key, target.field_path]);
  const activeGroup = useMemo(() => groups.find((group) => group.key === activeGroupKey) ?? null, [activeGroupKey, groups]);

  useEffect(() => {
    if (!activeGroup) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setActiveGroupKey(null);
        window.requestAnimationFrame(() => triggerRefs.current.get(activeGroup.key)?.focus());
      }
    };
    window.addEventListener("keydown", onKeyDown);
    const frame = window.requestAnimationFrame(() => window.document.getElementById(`reference-usage-dialog-${reactId}`)?.querySelector<HTMLButtonElement>(".usage-dialog-close")?.focus());
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.cancelAnimationFrame(frame);
    };
  }, [activeGroup, reactId]);

  useEffect(() => setActiveGroupKey(null), [target.object_kind, target.object_key, target.field_path]);

  if (groups.length === 0) return null;
  const visibleGroups = groups.filter((group) => group.priority === "HIGH");
  const secondaryGroups = groups.filter((group) => group.priority === "MEDIUM");
  const renderGroup = (group: UsageGroup) => <article className="usage-group" key={group.key}>
    <div className="usage-group-copy"><strong>{group.label}</strong><span>被 {group.consumers.length} 个{group.label}使用</span></div>
    {group.consumers.length === 1
      ? <AuthoringActionButton className="usage-group-action" intent="navigate" to={group.consumers[0].href}>前往{group.consumers[0].name}</AuthoringActionButton>
      : <AuthoringActionButton
        intent="view"
        className="usage-group-trigger usage-group-action"
        buttonRef={(element) => {
          if (element) triggerRefs.current.set(group.key, element);
          else triggerRefs.current.delete(group.key);
        }}
        onPress={() => setActiveGroupKey(group.key)}
        ariaLabel={`查看 ${group.consumers.length} 个${group.label}`}
        ariaExpanded={activeGroupKey === group.key}
        ariaControls={`reference-usage-dialog-${reactId}`}
      >{group.consumers.length} 个{group.label}</AuthoringActionButton>}
  </article>;

  return <section className="backreference-usage-section" aria-labelledby={`usage-title-${safeTargetId}`}>
    <h4 id={`usage-title-${safeTargetId}`}>使用情况</h4>
    <div className="usage-groups">{visibleGroups.map(renderGroup)}</div>
    {secondaryGroups.length > 0 && <details className="usage-secondary-details"><summary>其他使用情况</summary><div className="usage-groups">{secondaryGroups.map(renderGroup)}</div></details>}
    {activeGroup && <EditorDialog
      id={`reference-usage-dialog-${reactId}`}
      titleId={`reference-usage-dialog-title-${reactId}`}
      kicker="使用情况"
      title={`使用“${targetName(document, target)}”的${activeGroup.label}`}
      onClose={() => {
        const key = activeGroup.key;
        setActiveGroupKey(null);
        window.requestAnimationFrame(() => triggerRefs.current.get(key)?.focus());
      }}
      footer={<AuthoringActionButton intent="view" className="usage-dialog-close" ariaLabel="关闭使用情况" onPress={() => {
        const key = activeGroup.key;
        setActiveGroupKey(null);
        window.requestAnimationFrame(() => triggerRefs.current.get(key)?.focus());
      }}>关闭</AuthoringActionButton>}
    >
      <div className="usage-dialog-list">{activeGroup.consumers.map((consumer) => <article className="usage-dialog-item" key={`${consumer.kind}:${consumer.key ?? ""}`}>
        <div className="usage-dialog-item-copy"><strong>{consumer.name}</strong><small>{consumer.semanticType}</small>{consumer.stableKey && <code>{consumer.stableKey}</code>}</div>
        <AuthoringActionButton className="usage-dialog-item-action" intent="navigate" to={consumer.href} onPress={() => setActiveGroupKey(null)}>前往{consumer.name}</AuthoringActionButton>
      </article>)}</div>
    </EditorDialog>}
  </section>;
}
