import type { DraftObject, JsonObject } from "../../editor";
import type { ReactNode } from "react";
import type { InitializationFinding, InitializationPreview, InitializationProjectionItem } from "../../types";
import { displayEnumValue } from "../../ui";
import { AuthoringActionButton, FieldActionRow } from "./AuthoringActionButton";
import { SourceBadge } from "../InitializationWorkspace";
import { ReadonlyFieldDisplay } from "./FormPrimitives";
import { authoredReferenceName } from "./ReferencePicker";
import { hasStructuredInitializationPreviewIssues, initializationPreviewIssuePresentationsForFocus } from "./initialization-preview-errors";
import { typedScalarDisplay } from "./typed-values";

type ProjectedItem = { domain: string; group: string; item: InitializationProjectionItem };

function projectedItems(preview: InitializationPreview): ProjectedItem[] {
  return preview.projection.domains.flatMap((domain) => domain.groups.flatMap((group) => group.items.map((item) => ({ domain: domain.id, group: group.id, item }))));
}

function findFinding(preview: InitializationPreview, identity: string): InitializationFinding | undefined {
  return preview.projection.findings.find((finding) => finding.identity === identity);
}

function formatValue(finding: InitializationFinding, kind: DraftObject["kind"], objectKey: string, field: string, document: JsonObject): string {
  if (field === "initial_access") return displayEnumValue("access", String(finding.value ?? ""));
  if (field === "initial_visibility") return displayEnumValue("visibility", String(finding.value ?? ""));
  if (field === "command_reachability") return displayEnumValue("reachability", String(finding.value ?? ""));
  if (field === "runtime_status") return displayEnumValue("generic", String(finding.value ?? ""));
  if (field === "initial_node_key") {
    const nodes = document.world && typeof document.world === "object" && !Array.isArray(document.world) ? (document.world as JsonObject).nodes : undefined;
    const node = Array.isArray(nodes) ? nodes.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && (candidate as JsonObject).key === finding.value) as JsonObject | undefined : undefined;
    return node && typeof node.name === "string" ? node.name : String(finding.value ?? "—");
  }
  if (field === "initial_value" && typeof finding.value === "boolean") return finding.value ? "是" : "否";
  if (field === "initial_value" && kind === "node" && finding.identity.startsWith("fact:")) {
    const [, nodeKey, factKey] = finding.identity.split(":");
    const world = document.world && typeof document.world === "object" && !Array.isArray(document.world) ? document.world as JsonObject : {};
    const nodes = Array.isArray(world.nodes) ? world.nodes : [];
    const node = nodes.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && (candidate as JsonObject).key === nodeKey) as JsonObject | undefined;
    const facts = Array.isArray(node?.facts) ? node.facts : [];
    const fact = facts.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && (candidate as JsonObject).key === factKey) as JsonObject | undefined;
    if (fact) return typedScalarDisplay(finding.value, fact);
  }
  if (field === "primary_actor") return finding.value === objectKey ? "是" : "否";
  if (finding.value === null || finding.value === undefined || finding.value === "") return "未设置";
  if (typeof finding.value === "object") return JSON.stringify(finding.value);
  return String(finding.value);
}

function findingLabel(kind: DraftObject["kind"], field: string): string {
  if (field === "initial_access") return "节点访问状态";
  if (field === "initial_visibility") return kind === "relation" ? "关系可见性" : "玩家知识";
  if (field === "initial_value") return "真实初始值";
  if (field === "initial_node_key") return "初始位置";
  if (field === "command_reachability") return "指挥可达";
  if (field === "runtime_status") return "运行时初始状态";
  if (field === "primary_actor") return "主要参与者";
  return field.replaceAll("_", " ");
}

function findingRow(finding: InitializationFinding, kind: DraftObject["kind"], objectKey: string, field: string, document: JsonObject, labelPrefix?: string) {
  return <div className="initialization-preview-item" key={finding.identity}>
    <span>{labelPrefix ? `${labelPrefix} · ` : ""}{findingLabel(kind, field)}</span>
    <ReadonlyFieldDisplay value={formatValue(finding, kind, objectKey, field, document)} />
    <SourceBadge source={finding.source} />
  </div>;
}

function itemHref(projected: ProjectedItem, scenarioId: string, factKey?: string): string {
  const query = new URLSearchParams({ domain: projected.domain, group: projected.group, item: projected.item.id });
  if (factKey) query.set("fact", factKey);
  return `/scenarios/${scenarioId}/edit/initialization?${query}`;
}

function resourceRows(preview: InitializationPreview, entity: DraftObject, document: JsonObject, scenarioId: string): { rows: ReactNode[]; href: string } {
  const items = projectedItems(preview).filter(({ group, item }) =>
    (group === "resource-pools" || group === "compatibility-resources") && item.context.resource_key === entity.key,
  );
  const rows = items.flatMap(({ item }) => {
    const finding = item.field_ids.map((identity) => findFinding(preview, identity)).find((candidate): candidate is InitializationFinding => Boolean(candidate));
    if (!finding || !finding.value || typeof finding.value !== "object" || Array.isArray(finding.value)) return [];
    const value = finding.value as JsonObject;
    const requirement = value.availability_requirement && typeof value.availability_requirement === "object" && !Array.isArray(value.availability_requirement) ? value.availability_requirement as JsonObject : null;
    const requirementIdentity = requirement ? `${String(requirement.node_key ?? "")}.${String(requirement.fact_key ?? "")}` : "未配置";
    const requirementValue = requirement?.value === true ? "是" : requirement?.value === false ? "否" : String(requirement?.value ?? "未配置");
    const fields: Array<[string, string]> = [
      ["资源池键", String(value.pool_key ?? item.context.pool_key ?? "—")],
      ["所属区域", value.region_key ? authoredReferenceName(document, "node", String(value.region_key)) ?? String(value.region_key) : "全局范围"],
      ["所在设施", value.facility_key ? authoredReferenceName(document, "node", String(value.facility_key)) ?? String(value.facility_key) : "未指定"],
      ["数量", String(value.quantity ?? "—")],
      ["预留数量", String(value.reserved ?? "—")],
      ["可用状态", displayEnumValue("availability", String(value.availability ?? ""))],
      ["玩家可见性", displayEnumValue("visibility", String(value.visibility ?? ""))],
      ["可由调查发现", value.survey_discoverable ? "是" : "否"],
      ["可用性要求事实", requirementIdentity],
      ["可用性要求值", requirementValue],
    ];
    return fields.filter(([label]) => Boolean(requirement) || !label.startsWith("可用性要求")).map(([label, text]) => <div className="initialization-preview-item" key={`${item.id}:${label}`}>
      <span>{item.label} · {label}</span><ReadonlyFieldDisplay value={text} /><SourceBadge source={finding.source} />
    </div>);
  });
  const first = items.length === 1 ? items[0] : undefined;
  return {
    rows,
    href: first ? itemHref(first, scenarioId) : `/scenarios/${scenarioId}/edit/initialization?domain=resources&group=resource-pools`,
  };
}

function fallbackInitializationHref(entity: DraftObject, document: JsonObject, scenarioId: string, factKey?: string): string {
  const world = document.world && typeof document.world === "object" && !Array.isArray(document.world) ? document.world as JsonObject : {};
  const actors = document.actors && typeof document.actors === "object" && !Array.isArray(document.actors) ? document.actors as JsonObject : {};
  const initialization = document.initialization && typeof document.initialization === "object" && !Array.isArray(document.initialization) ? document.initialization as JsonObject : {};
  let domain = "nodes";
  let group = "";
  let item = "";
  if (entity.kind === "node") {
    const node = Array.isArray(world.nodes) ? world.nodes.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && (candidate as JsonObject).key === entity.key) as JsonObject | undefined : undefined;
    domain = "nodes";
    group = `node-type:${String(node?.node_type_key || "unassigned")}`;
    item = `node:${entity.key}`;
  } else if (entity.kind === "actor") {
    const profiles = Array.isArray(actors.actor_profiles) ? actors.actor_profiles : [];
    const actor = profiles.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && (candidate as JsonObject).key === entity.key) as JsonObject | undefined;
    domain = "actors";
    group = `role:${String(actor?.role_key || "unassigned")}`;
    item = `actor:${entity.key}`;
  } else if (entity.kind === "relation") {
    const relations = Array.isArray(world.relations) ? world.relations : [];
    const relation = relations.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && (String((candidate as JsonObject).key ?? `${(candidate as JsonObject).source_node_key}__${(candidate as JsonObject).relation_type_key}__${(candidate as JsonObject).target_node_key}`) === entity.key)) as JsonObject | undefined;
    domain = "relations";
    group = `relation-type:${String(relation?.relation_type_key || "unassigned")}`;
    item = `relation:${entity.key}`;
  } else if (entity.kind === "resource") {
    const pools = Array.isArray(initialization.resource_pools) ? initialization.resource_pools : [];
    const pool = pools.find((candidate) => candidate && typeof candidate === "object" && !Array.isArray(candidate) && (candidate as JsonObject).resource_key === entity.key) as JsonObject | undefined;
    domain = "resources";
    if (pool) {
      group = "resource-pools";
      item = `pool:${String(pool.pool_key)}:${entity.key}:${String(pool.region_key || "global")}`;
    } else {
      group = "compatibility-resources";
      item = `pool:default:${entity.key}:global`;
    }
  }
  const query = new URLSearchParams({ domain, group, item });
  if (factKey) query.set("fact", factKey);
  return `/scenarios/${scenarioId}/edit/initialization?${query}`;
}

function staticRow(label: string, value: string, key: string) {
  return <div className="initialization-preview-item" key={key}><span>{label}</span><ReadonlyFieldDisplay value={value || "未设置"} /></div>;
}

function PreviewIssueSummary({ issues }: { issues: ReturnType<typeof initializationPreviewIssuePresentationsForFocus> }) {
  if (issues.length === 0) return null;
  return <section className="initialization-preview-object-issues" aria-label="当前对象待完善配置">
    <strong>该对象还有 {issues.length} 项配置未完成</strong>
    <ul className="initialization-preview-issue-list">
      {issues.map((issue, index) => <li className={`initialization-preview-issue-row is-${issue.category}`} key={`${issue.groupKey}:${issue.problemLabel}:${index}`}>
        <span>{issue.category === "dependency" ? `${issue.problemLabel}：${issue.reason}` : issue.reason}</span>
        <AuthoringActionButton intent="navigate" to={issue.ownerHref ?? issue.href}>{issue.ownerHref ? issue.ownerActionLabel ?? "前往" : "前往"}</AuthoringActionButton>
      </li>)}
    </ul>
  </section>;
}

export function InitializationReadonlyPreview({ entity, fact, document, preview, loading, scenarioId, previewError, onRetry }: {
  entity: DraftObject;
  fact?: JsonObject;
  document: JsonObject;
  preview: InitializationPreview | null;
  loading: boolean;
  scenarioId: string;
  previewError?: unknown;
  onRetry?: () => void;
}) {
  if (!["node", "actor", "relation", "resource"].includes(entity.kind)) return null;
  const key = entity.key;
  const allItems = preview ? projectedItems(preview) : [];
  const issueSource = preview ? preview.issues ?? [] : previewError;
  const previewIssues = initializationPreviewIssuePresentationsForFocus(issueSource, {
    kind: entity.kind,
    key,
    factKey: typeof fact?.key === "string" ? fact.key : null,
  }, document, scenarioId);
  const hasStructuredIssues = hasStructuredInitializationPreviewIssues(issueSource);
  const projected = entity.kind === "resource"
    ? undefined
    : allItems.find(({ item }) => item.locator.object_kind === entity.kind && item.locator.object_key === key);
  let rows: ReactNode[] = [];
  let href = fallbackInitializationHref(entity, document, scenarioId, fact?.key as string | undefined);

  if (preview && entity.kind === "node" && projected) {
    const fields = projected.item.field_ids.filter((identity) => identity.startsWith(`node:${key}:`));
    rows = fields.flatMap((identity) => {
      const finding = findFinding(preview, identity);
      const field = identity.slice(`node:${key}:`.length);
      return finding ? [findingRow(finding, entity.kind, key, field, document)] : [];
    });
    if (!fact) {
      const facts = Array.isArray(entity.value.facts) ? entity.value.facts.filter((candidate): candidate is JsonObject => Boolean(candidate) && typeof candidate === "object" && !Array.isArray(candidate)) : [];
      for (const currentFact of facts) {
        const factKey = typeof currentFact.key === "string" ? currentFact.key : "";
        const findings = preview.projection.findings.filter((finding) => finding.identity.startsWith(`fact:${key}:${factKey}:`));
        rows.push(...findings.map((finding) => findingRow(finding, entity.kind, key, finding.identity.slice(`fact:${key}:${factKey}:`.length), document, String(currentFact.name ?? factKey))));
      }
    }
    href = itemHref(projected, scenarioId);
  } else if (preview && entity.kind === "actor" && projected) {
    const fields = projected.item.field_ids.filter((identity) => identity.startsWith(`actor:${key}:`));
    rows = fields.flatMap((identity) => {
      const finding = findFinding(preview, identity);
      const field = identity.slice(`actor:${key}:`.length);
      return finding ? [findingRow(finding, entity.kind, key, field, document)] : [];
    });
    const primaryActor = findFinding(preview, "entry:primary_actor");
    if (primaryActor) rows.push(findingRow(primaryActor, entity.kind, key, "primary_actor", document));
    href = itemHref(projected, scenarioId);
  } else if (preview && entity.kind === "relation" && projected) {
    const context = projected.item.context;
    const sourceKey = String(context.source ?? "");
    const targetKey = String(context.target ?? "");
    const relationType = String(context.type ?? "");
    rows = [
      staticRow("来源节点", authoredReferenceName(document, "node", sourceKey) ?? sourceKey, `${key}:source`),
      staticRow("关系类型", relationType, `${key}:type`),
      staticRow("目标节点", authoredReferenceName(document, "node", targetKey) ?? targetKey, `${key}:target`),
    ];
    const identity = projected.item.field_ids.find((fieldId) => fieldId.startsWith("relation:") && fieldId.endsWith(":initial_visibility"));
    const finding = identity ? findFinding(preview, identity) : undefined;
    if (finding) rows.push(findingRow(finding, entity.kind, key, "initial_visibility", document));
    href = itemHref(projected, scenarioId);
  } else if (preview && entity.kind === "resource") {
    const resource = resourceRows(preview, entity, document, scenarioId);
    rows = resource.rows;
    href = resource.href;
  }

  if (entity.kind === "node" && fact && preview) {
    const factKey = typeof fact.key === "string" ? fact.key : "";
    const identityPrefix = `fact:${key}:${factKey}:`;
    const factFindings = preview.projection.findings.filter((finding) => finding.identity.startsWith(identityPrefix));
    rows = factFindings.map((finding) => findingRow(finding, "node", key, finding.identity.slice(identityPrefix.length), document, String(fact.name ?? factKey)));
    const nodeItem = allItems.find(({ item }) => item.id === `node:${key}`);
    if (nodeItem) href = itemHref(nodeItem, scenarioId, factKey);
  }

  return <section className="initialization-readonly-preview" aria-label="开局配置只读预览">
    <h4>开局配置</h4>
    {loading && !preview && <p className="initialization-preview-empty">正在读取当前工作副本的开局投影…</p>}
    {!preview && previewIssues.length > 0 && <div className="initialization-preview-failure" role="alert"><strong>暂时无法生成完整开局配置</strong><PreviewIssueSummary issues={previewIssues} /></div>}
    {!preview && previewIssues.length === 0 && Boolean(previewError) && !hasStructuredIssues && <div className="initialization-preview-failure" role="alert"><strong>开局预览暂不可用</strong><p>预览服务暂未返回可定位的问题。当前对象字段仍可继续编辑，也可以重新读取预览。</p>{onRetry && <FieldActionRow><AuthoringActionButton intent="view" onPress={onRetry}>重新读取预览</AuthoringActionButton></FieldActionRow>}</div>}
    {!preview && !previewError && !loading && <p className="initialization-preview-empty">尚未收到当前工作副本的预览结果。</p>}
    {preview && rows.length > 0 && <div className="initialization-preview-grid">{rows}</div>}
    {preview && previewIssues.length > 0 && <PreviewIssueSummary issues={previewIssues} />}
    {preview && rows.length === 0 && previewIssues.length === 0 && <p className="initialization-preview-empty">当前对象没有对应的开局投影字段。</p>}
    <FieldActionRow><AuthoringActionButton intent="navigate" to={href}>前往初始化</AuthoringActionButton></FieldActionRow>
  </section>;
}
