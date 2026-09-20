import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { authoredDisplayName, type JsonObject } from "../editor";
import { authoredReferenceName } from "./editor/ReferencePicker";
import type { InitializationFinding, InitializationPreview, InitializationProjectionItem } from "../types";
import { displayEnumValue } from "../ui";

type Props = {
  document: JsonObject;
  preview: InitializationPreview | null;
  loading: boolean;
  error: string | null;
  scenarioId: string;
  onChange: (document: JsonObject) => void;
  onDeleteResourcePool: (poolKey: string) => void;
};

const domainLabels: Record<string, string> = {
  basic: "基础配置", nodes: "节点", actors: "参与者", resources: "资源",
  relations: "关系", derived: "派生状态预览",
};

const subgroupHeadings: Record<string, string> = {
  basic: "配置分组", nodes: "节点类型", actors: "角色", resources: "资源分类",
  relations: "关系类型", derived: "派生状态",
};

const hierarchyGroupLabels: Record<string, string> = {
  entry: "开局入口",
  "resource-pools": "资源池",
  "region-resource-knowledge": "区域资源知识",
  "compatibility-resources": "兼容来源",
  "derived-states": "派生状态",
};

function displayGroupLabel(group: { id: string; label: string }): string {
  return hierarchyGroupLabels[group.id] ?? group.label;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function projectionObjectKey(item: InitializationProjectionItem): string {
  return stringValue(item.locator.object_key) || stringValue(item.context.key);
}

function resourcePoolDetails(item: InitializationProjectionItem, document: JsonObject): { poolKey: string; resourceKey: string; locationKey: string; ownLabel: string } {
  const context = object(item.context);
  const initialization = object(document.initialization);
  const pools = array(initialization.resource_pools);
  const locatorPoolKey = stringValue(item.locator.object_key);
  const contextPoolKey = stringValue(context.pool_key);
  const poolKey = contextPoolKey || locatorPoolKey;
  const pool = pools.find((candidate) => candidate.pool_key === poolKey);
  const resourceKey = stringValue(pool?.resource_key) || stringValue(context.resource_key);
  const facilityKey = stringValue(pool?.facility_key) || stringValue(context.facility_key);
  const regionKey = stringValue(pool?.region_key) || stringValue(context.region_key);
  return {
    poolKey,
    resourceKey,
    locationKey: facilityKey || regionKey,
    ownLabel: pool ? authoredDisplayName(pool, "") : "",
  };
}

function displayItemLabel(item: InitializationProjectionItem, document: JsonObject): string {
  if (item.id === "bootstrap-entry") return "起始节点与主要参与者";
  if (item.id.startsWith("pool:")) {
    const { poolKey, resourceKey, locationKey, ownLabel } = resourcePoolDetails(item, document);
    if (ownLabel) return ownLabel;
    const resourceName = authoredReferenceName(document, "resource", resourceKey);
    const locationName = authoredReferenceName(document, "node", locationKey);
    if (locationName && resourceName) return `${locationName} · ${resourceName}`;
    if (resourceName || locationName) return resourceName ?? locationName ?? poolKey;
    return poolKey || item.id;
  }
  if (item.id.startsWith("region-knowledge:")) {
    const key = projectionObjectKey(item) || item.id.slice("region-knowledge:".length);
    return authoredReferenceName(document, "node", key) ?? key;
  }
  if (item.id.startsWith("derived:")) {
    const key = projectionObjectKey(item) || item.id.slice("derived:".length);
    return authoredReferenceName(document, "derived_state", key) ?? item.label;
  }
  return item.label;
}

function displayItemIdentity(item: InitializationProjectionItem, detail = false): string {
  if (item.id.startsWith("pool:")) {
    const key = projectionObjectKey(item);
    return key ? (detail ? `pool:${key}` : key) : item.id;
  }
  if (item.id.startsWith("region-knowledge:")) return projectionObjectKey(item) || item.id;
  if (item.id.startsWith("derived:")) {
    const key = projectionObjectKey(item) || item.id.slice("derived:".length);
    return detail && key ? `derived:${key}` : key;
  }
  return item.id;
}

function navigateColumn(event: KeyboardEvent<HTMLElement>) {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>(":scope > .initialization-panel-body > button"));
  const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
  if (current < 0) return;
  event.preventDefault();
  buttons[(current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
}

function array(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function object(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function replaceIn(document: JsonObject, root: "world" | "actors" | "initialization", collection: string, index: number, value: JsonObject): JsonObject {
  const next = structuredClone(document) as JsonObject;
  const owner = object(next[root]);
  const items = array(owner[collection]);
  owner[collection] = items.map((item, itemIndex) => itemIndex === index ? value : item);
  next[root] = owner;
  return next;
}

function SelectField({ label, value, choices, onChange, source, enumType }: { label: string; value: unknown; choices: Array<{ key: string; name: string }>; onChange: (value: string) => void; source?: string; enumType?: string }) {
  return <label className="initialization-field"><span>{label}{source && <SourceBadge source={source} />}</span><select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>{choices.map((choice) => <option value={choice.key} key={choice.key}>{enumType ? displayEnumValue(enumType, choice.key) : choice.name}</option>)}</select></label>;
}

function ScalarField({ label, value, onChange, source, type = "text" }: { label: string; value: unknown; onChange: (value: unknown) => void; source?: string; type?: "text" | "number" | "checkbox" }) {
  if (type === "checkbox") return <label className="initialization-field initialization-check"><span>{label}{source && <SourceBadge source={source} />}</span><span className="initialization-checkbox-control"><input type="checkbox" checked={value === true} onChange={(event) => onChange(event.target.checked)} />{value === true ? "是" : "否"}</span></label>;
  return <label className="initialization-field"><span>{label}{source && <SourceBadge source={source} />}</span><input type={type} value={String(value ?? "")} onChange={(event) => onChange(type === "number" ? Number(event.target.value) : event.target.value)} /></label>;
}

function SourceBadge({ source }: { source: string }) {
  return <small className={`source-badge source-${source.toLowerCase()}`}>{displayEnumValue("source", source)}</small>;
}

function findingFor(preview: InitializationPreview, identity: string): InitializationFinding | undefined {
  return preview.projection.findings.find((item) => item.identity === identity);
}

function designHref(item: InitializationProjectionItem, scenarioId: string): string {
  const locator = item.locator;
  if (!locator.object_key) return `/scenarios/${scenarioId}/edit/${locator.section}`;
  return `/scenarios/${scenarioId}/edit/${locator.section}/${encodeURIComponent(locator.object_key)}`;
}

function Detail({ item, preview, document, scenarioId, onChange, onDeleteResourcePool }: { item: InitializationProjectionItem; preview: InitializationPreview; document: JsonObject; scenarioId: string; onChange: (document: JsonObject) => void; onDeleteResourcePool: (poolKey: string) => void }) {
  const world = object(document.world);
  const actors = object(document.actors);
  const initialization = object(document.initialization);
  const nodeChoices = array(world.nodes).map((node) => ({ key: String(node.key), name: String(node.name ?? node.key) }));
  const actorChoices = array(actors.actor_profiles).map((actor) => ({ key: String(actor.key), name: String(actor.name ?? actor.key) }));
  const heading = <header className="initialization-detail-heading"><div><h4>{displayItemLabel(item, document)}</h4><code>{displayItemIdentity(item, true)}</code></div>{item.locator.section !== "initialization" && <Link className="editor-button editor-button-secondary" to={designHref(item, scenarioId)}>打开完整定义</Link>}</header>;

  if (item.id === "bootstrap-entry") return <div>{heading}<section className="initialization-detail-section"><h5>开局入口</h5><SelectField label="起始节点" value={initialization.start_node_key} choices={nodeChoices} source="EXPLICIT" onChange={(value) => onChange({ ...document, initialization: { ...initialization, start_node_key: value } })} /><SelectField label="主要参与者" value={initialization.primary_actor_key} choices={actorChoices} source="EXPLICIT" onChange={(value) => onChange({ ...document, initialization: { ...initialization, primary_actor_key: value } })} /></section></div>;

  if (item.id.startsWith("node:")) {
    const key = item.id.slice(5);
    const nodes = array(world.nodes);
    const index = nodes.findIndex((node) => node.key === key);
    const node = nodes[index];
    if (!node) return null;
    const update = (value: JsonObject) => onChange(replaceIn(document, "world", "nodes", index, value));
    return <div>{heading}<section className="initialization-detail-section"><h5>真实初始状态</h5><SelectField label="节点访问状态" value={node.initial_access} choices={[{ key: "AVAILABLE", name: "AVAILABLE" }, { key: "LOCKED", name: "LOCKED" }]} enumType="access" source="EXPLICIT" onChange={(value) => update({ ...node, initial_access: value })} /></section><section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="节点可见性" value={node.initial_visibility} choices={[{ key: "KNOWN", name: "KNOWN" }, { key: "HIDDEN", name: "HIDDEN" }]} enumType="visibility" source="EXPLICIT" onChange={(value) => update({ ...node, initial_visibility: value })} /></section>{array(node.facts).map((fact, factIndex) => <section className="initialization-fact" key={String(fact.key)}><div><strong>{String(fact.name ?? fact.key)}</strong><code>{String(fact.key)}</code><small>{displayEnumValue("value_type", String(fact.value_type ?? ""))}</small></div>{fact.value_type === "ENUM" ? <SelectField label="真实值 · 初始值" value={fact.initial_value} choices={(Array.isArray(fact.allowed_values) ? fact.allowed_values : []).map((value) => ({ key: String(value), name: String(value) }))} source="EXPLICIT" onChange={(value) => update({ ...node, facts: array(node.facts).map((old, oldIndex) => oldIndex === factIndex ? { ...fact, initial_value: value } : old) })} /> : <ScalarField label="真实值 · 初始值" value={fact.initial_value} source="EXPLICIT" type={fact.value_type === "INTEGER" ? "number" : fact.value_type === "BOOLEAN" ? "checkbox" : "text"} onChange={(value) => update({ ...node, facts: array(node.facts).map((old, oldIndex) => oldIndex === factIndex ? { ...fact, initial_value: value } : old) })} />}<SelectField label="知识 · 可见性" value={fact.initial_visibility} choices={[{ key: "KNOWN", name: "KNOWN" }, { key: "HIDDEN", name: "HIDDEN" }]} enumType="visibility" source="EXPLICIT" onChange={(value) => update({ ...node, facts: array(node.facts).map((old, oldIndex) => oldIndex === factIndex ? { ...fact, initial_visibility: value } : old) })} /></section>)}</div>;
  }

  if (item.id.startsWith("actor:")) {
    const key = item.id.slice(6);
    const profiles = array(actors.actor_profiles);
    const index = profiles.findIndex((actor) => actor.key === key);
    const actor = profiles[index];
    if (!actor) return null;
    const update = (value: JsonObject) => onChange(replaceIn(document, "actors", "actor_profiles", index, value));
    const reachability = findingFor(preview, `actor:${key}:command_reachability`);
    return <div>{heading}<section className="initialization-detail-section"><h5>参与者开局状态</h5><SelectField label="初始位置" value={actor.initial_node_key} choices={nodeChoices} source="EXPLICIT" onChange={(value) => update({ ...actor, initial_node_key: value })} /><SelectField label="指挥可达性" value={actor.command_reachability ?? "ONLINE"} choices={[{ key: "ONLINE", name: "ONLINE" }, { key: "DISCONNECTED", name: "DISCONNECTED" }]} enumType="reachability" source={reachability?.source ?? "DEFAULT"} onChange={(value) => update({ ...actor, command_reachability: value })} /><div className="initialization-readonly-row"><span>运行时初始状态</span><strong>{displayEnumValue("generic", "ACTIVE")}</strong><SourceBadge source="ENGINE" /></div><button type="button" className="editor-button editor-button-secondary" onClick={() => onChange({ ...document, initialization: { ...initialization, primary_actor_key: key } })}>设为主要参与者</button></section></div>;
  }

  if (item.id.startsWith("relation:")) {
    const key = item.id.slice(9);
    const relations = array(world.relations);
    const index = relations.findIndex((relation) => String(relation.key ?? `${relation.source_node_key}__${relation.relation_type_key}__${relation.target_node_key}`) === key);
    const relation = relations[index];
    if (!relation) return null;
    const update = (value: JsonObject) => onChange(replaceIn(document, "world", "relations", index, value));
    const source = findingFor(preview, `relation:${key}:initial_visibility`)?.source ?? "DEFAULT";
    return <div>{heading}<section className="initialization-detail-section"><h5>关系拓扑 · 只读</h5><p>{String(relation.source_node_key)} → {String(relation.target_node_key)}</p><code>{String(relation.relation_type_key)}</code></section><section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="关系可见性" value={relation.initial_visibility ?? "VISIBLE"} choices={[{ key: "VISIBLE", name: "VISIBLE" }, { key: "HIDDEN", name: "HIDDEN" }]} enumType="visibility" source={source} onChange={(value) => update({ ...relation, initial_visibility: value })} /></section></div>;
  }

  if (item.id.startsWith("pool:")) {
    const poolKey = projectionObjectKey(item);
    const pools = array(initialization.resource_pools);
    const index = pools.findIndex((pool) => pool.pool_key === poolKey);
    const pool = pools[index];
    const finding = findingFor(preview, item.field_ids[0]);
    if (!pool) return <div>{heading}<section className="initialization-detail-section"><h5>兼容来源 · 只读</h5><p>该运行时资源池由旧字段或 Resource.initial_value 生成。</p><SourceBadge source="LEGACY_FALLBACK" /><pre>{JSON.stringify(finding?.value, null, 2)}</pre></section></div>;
    const update = (value: JsonObject) => onChange(replaceIn(document, "initialization", "resource_pools", index, value));
    const resources = array(world.resources).map((resource) => ({ key: String(resource.key), name: String(resource.name ?? resource.key) }));
    const requirement = object(pool.availability_requirement);
    return <div>{heading}<section className="initialization-detail-section"><h5>资源池真实状态</h5><SelectField label="资源" value={pool.resource_key} choices={resources} source="EXPLICIT" onChange={(value) => update({ ...pool, resource_key: value })} /><SelectField label="区域" value={pool.region_key ?? ""} choices={[{ key: "", name: "全局" }, ...nodeChoices]} source="EXPLICIT" onChange={(value) => update({ ...pool, region_key: value || null })} /><SelectField label="设施" value={pool.facility_key ?? ""} choices={[{ key: "", name: "无" }, ...nodeChoices]} source="EXPLICIT" onChange={(value) => update({ ...pool, facility_key: value || null })} /><ScalarField label="数量" value={pool.quantity} type="number" source="EXPLICIT" onChange={(value) => update({ ...pool, quantity: value })} /><ScalarField label="已预留" value={pool.reserved_value} type="number" source="EXPLICIT" onChange={(value) => update({ ...pool, reserved_value: value })} /><SelectField label="可用性" value={pool.availability} choices={[{ key: "AVAILABLE", name: "AVAILABLE" }, { key: "UNAVAILABLE", name: "UNAVAILABLE" }]} enumType="availability" source="EXPLICIT" onChange={(value) => update({ ...pool, availability: value })} />{pool.availability_requirement ? <div className="initialization-nested"><h5>可用性要求</h5><SelectField label="节点" value={requirement.node_key} choices={nodeChoices} source="EXPLICIT" onChange={(value) => update({ ...pool, availability_requirement: { ...requirement, node_key: value } })} /><ScalarField label="事实键" value={requirement.fact_key} source="EXPLICIT" onChange={(value) => update({ ...pool, availability_requirement: { ...requirement, fact_key: value } })} /><ScalarField label="期望值" value={requirement.value} source="EXPLICIT" type={typeof requirement.value === "boolean" ? "checkbox" : typeof requirement.value === "number" ? "number" : "text"} onChange={(value) => update({ ...pool, availability_requirement: { ...requirement, value } })} /><button type="button" className="editor-button editor-button-secondary" onClick={() => update({ ...pool, availability_requirement: null })}>移除可用性要求</button></div> : <button type="button" className="editor-button editor-button-secondary" onClick={() => update({ ...pool, availability_requirement: { node_key: nodeChoices[0]?.key ?? "", fact_key: "", value: true } })}>添加可用性要求</button>}</section><section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="可见性" value={pool.visibility} choices={[{ key: "VISIBLE", name: "VISIBLE" }, { key: "HIDDEN", name: "HIDDEN" }]} enumType="visibility" source="EXPLICIT" onChange={(value) => update({ ...pool, visibility: value })} /><ScalarField label="可由调查发现" value={pool.survey_discoverable} type="checkbox" source="EXPLICIT" onChange={(value) => update({ ...pool, survey_discoverable: value })} /></section><button type="button" className="editor-button editor-button-danger" onClick={() => onDeleteResourcePool(poolKey)}>删除此资源池</button></div>;
  }

  if (item.id.startsWith("region-knowledge:")) {
    const key = item.id.slice("region-knowledge:".length);
    const states = array(initialization.region_resource_knowledge);
    const index = states.findIndex((state) => state.region_key === key);
    const state = states[index] ?? { region_key: key, resource_inventory_visibility: "VISIBLE", resource_survey_completed: true };
    const source = index >= 0 ? "EXPLICIT" : "DEFAULT";
    const update = (value: JsonObject) => {
      const next = structuredClone(document) as JsonObject;
      const nextInitialization = object(next.initialization);
      nextInitialization.region_resource_knowledge = index >= 0 ? states.map((old, oldIndex) => oldIndex === index ? value : old) : [...states, value];
      next.initialization = nextInitialization;
      onChange(next);
    };
    return <div>{heading}<section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="库存可见性" value={state.resource_inventory_visibility} choices={[{ key: "VISIBLE", name: "VISIBLE" }, { key: "HIDDEN", name: "HIDDEN" }]} enumType="visibility" source={source} onChange={(value) => update({ ...state, resource_inventory_visibility: value })} /><ScalarField label="调查已完成" value={state.resource_survey_completed} type="checkbox" source={source} onChange={(value) => update({ ...state, resource_survey_completed: value })} /></section></div>;
  }

  if (item.id.startsWith("derived:")) return <div>{heading}<section className="initialization-detail-section"><h5>真实初始状态</h5><div className="derived-preview-value">{displayEnumValue("generic", String(item.context.truth ?? "UNKNOWN"))}</div></section><section className="initialization-detail-section"><h5>玩家初始知识</h5><div className="derived-preview-value">{displayEnumValue("generic", String(item.context.knowledge ?? "UNKNOWN"))}</div><SourceBadge source="DERIVED" /><p>{String(item.context.dependencies)} 项依赖 · 只读计算结果</p></section></div>;
  return <div>{heading}</div>;
}

function EmptyPanel({ children }: { children: string }) {
  return <div className="initialization-panel-empty">{children}</div>;
}

export function InitializationWorkspace({ document, preview, loading, error, scenarioId, onChange, onDeleteResourcePool }: Props) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [overviewOpen, setOverviewOpen] = useState(false);
  const domains = preview?.projection.domains ?? [];
  const requestedDomainId = searchParams.get("domain") ?? "";
  const requestedGroupId = searchParams.get("group") ?? "";
  const requestedItemId = searchParams.get("item") ?? "";
  const domain = domains.find((candidate) => candidate.id === requestedDomainId);
  const inferredGroup = domain && !requestedGroupId && requestedItemId ? domain.groups.find((candidate) => candidate.items.some((candidateItem) => candidateItem.id === requestedItemId)) : undefined;
  const group = domain?.groups.find((candidate) => candidate.id === requestedGroupId) ?? inferredGroup;
  const item = group?.items.find((candidate) => candidate.id === requestedItemId);
  const filteredItems = useMemo(() => (group?.items ?? []).filter((candidate) => `${displayItemLabel(candidate, document)} ${candidate.label} ${candidate.id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [document, group, search]);
  const selectPath = (nextDomain: string, nextGroup = "", nextItem = "") => {
    const next = new URLSearchParams(searchParams);
    for (const key of ["domain", "group", "item"]) next.delete(key);
    if (nextDomain) next.set("domain", nextDomain);
    if (nextGroup) next.set("group", nextGroup);
    if (nextItem) next.set("item", nextItem);
    setSearchParams(next, { replace: true });
  };
  useEffect(() => {
    if (!preview) return;
    const normalizedDomain = domain?.id ?? "";
    const normalizedGroup = group?.id ?? "";
    const normalizedItem = item?.id ?? "";
    if (normalizedDomain === requestedDomainId && normalizedGroup === requestedGroupId && normalizedItem === requestedItemId) return;
    const next = new URLSearchParams(searchParams);
    for (const key of ["domain", "group", "item"]) next.delete(key);
    if (normalizedDomain) next.set("domain", normalizedDomain);
    if (normalizedGroup) next.set("group", normalizedGroup);
    if (normalizedItem) next.set("item", normalizedItem);
    setSearchParams(next, { replace: true });
  }, [domain?.id, group?.id, item?.id, preview, requestedDomainId, requestedGroupId, requestedItemId, searchParams, setSearchParams]);
  useEffect(() => setSearch(""), [domain?.id, group?.id]);
  const createResourcePool = () => {
    const initialization = object(document.initialization);
    const pools = array(initialization.resource_pools);
    const resources = array(object(document.world).resources);
    const poolKey = `new_pool_${pools.length + 1}`;
    onChange({ ...document, initialization: { ...initialization, resource_pools: [...pools, { pool_key: poolKey, resource_key: String(resources[0]?.key ?? ""), region_key: null, facility_key: null, quantity: 0, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false }] } });
    selectPath("resources", "resource-pools");
  };
  if (loading && !preview) return <div className="initialization-loading">正在计算完整开局配置…</div>;
  if (!preview) return <div className="initialization-loading"><strong>无法生成初始化预览</strong><p>{error ?? "请先修复当前工作文档的结构错误。"}</p></div>;
  const summary = preview.projection.summary;
  const differs = preview.parity.published && (preview.parity.initialization_changes.length > 0 || preview.parity.design_changes.length > 0);
  return <div className="initialization-workspace">
     <header className="initialization-workspace-header"><div className="initialization-workspace-toolbar"><nav className="initialization-breadcrumb" aria-label="初始化层级"><button onClick={() => selectPath("")}>类别</button>{domain && <><span>/</span><button onClick={() => selectPath(domain.id)}>{domainLabels[domain.id] ?? domain.label}</button></>}{group && <><span>/</span><button onClick={() => selectPath(domain!.id, group.id)}>{displayGroupLabel(group)}</button></>}{item && <><span>/</span><strong>{displayItemLabel(item, document)}</strong></>}</nav><div className="initialization-status"><span>{summary.warnings > 0 ? `⚠ ${summary.warnings}` : "0 项警告"}</span>{differs && <span>当前草稿与最新发布版本不同</span>}<button type="button" aria-expanded={overviewOpen} onClick={() => setOverviewOpen((open) => !open)}>初始化概览</button></div></div><p>配置当前版本的开局状态</p></header>
    {overviewOpen && <aside className="initialization-overview" aria-label="初始化概览"><div className="initialization-overview-heading"><div><h4>初始化概览</h4><p>当前草稿的开局规模与发布差异</p></div><button type="button" className="editor-button editor-button-ghost" onClick={() => setOverviewOpen(false)}>关闭</button></div><div className="initialization-metrics"><span><strong>{summary.nodes}</strong>节点</span><span><strong>{summary.actors}</strong>参与者</span><span><strong>{summary.resource_pools}</strong>资源池</span><span><strong>{summary.relations}</strong>关系</span><span><strong>{summary.derived_states}</strong>派生状态</span></div><div className="initialization-overview-summary"><span>警告 <strong>{summary.warnings}</strong></span><span>开局变化 <strong>{preview.parity.initialization_changes.length}</strong></span><span>设计变化 <strong>{preview.parity.design_changes.length}</strong></span><span>{preview.parity.published ? "对比最新发布版本" : "尚无发布版本"}</span></div></aside>}
    <div className="initialization-panels" data-testid="initialization-four-panel-workspace">
       <section className={`initialization-panel initialization-column${!domain ? " mobile-active" : ""}`} aria-label="类别" onKeyDown={navigateColumn}><header><h4>类别</h4><span>选择领域</span></header><div className="initialization-panel-body">{domains.map((candidate) => <button aria-current={candidate.id === domain?.id ? "true" : undefined} className={candidate.id === domain?.id ? "selected" : ""} key={candidate.id} onClick={() => selectPath(candidate.id)}><span>{domainLabels[candidate.id] ?? candidate.label}</span><small>{candidate.groups.reduce((total, value) => total + value.items.length, 0)}</small></button>)}</div></section>
       <section className={`initialization-panel initialization-column${domain && !group ? " mobile-active" : ""}`} aria-label="分类" onKeyDown={navigateColumn}><header><h4>{domain ? subgroupHeadings[domain.id] ?? "分类" : "分类"}</h4><span>{domain ? domainLabels[domain.id] ?? domain.label : "等待选择"}</span></header><div className="initialization-panel-body">{domain ? domain.groups.map((candidate) => <button aria-current={candidate.id === group?.id ? "true" : undefined} className={candidate.id === group?.id ? "selected" : ""} key={candidate.id} onClick={() => selectPath(domain.id, candidate.id)}><span>{displayGroupLabel(candidate)}</span><small>{candidate.items.length}</small></button>) : <EmptyPanel>请选择一个类别</EmptyPanel>}</div></section>
       <section className={`initialization-panel initialization-column initialization-items${group && !item ? " mobile-active" : ""}`} aria-label="对象" onKeyDown={navigateColumn}><header><h4>{group ? displayGroupLabel(group) : "对象"}</h4><span>{group ? "选择具体对象" : "等待选择"}</span>{group && <input aria-label="搜索当前项目" placeholder="名称或稳定键" value={search} onChange={(event) => setSearch(event.target.value)} />}{group?.id === "resource-pools" && <button type="button" className="initialization-add" onClick={createResourcePool}>＋ 新增资源池</button>}</header><div className="initialization-panel-body">{group ? filteredItems.map((candidate) => <button aria-current={candidate.id === item?.id ? "true" : undefined} className={candidate.id === item?.id ? "selected" : ""} key={candidate.id} onClick={() => selectPath(domain!.id, group.id, candidate.id)}><span>{displayItemLabel(candidate, document)}</span><code>{displayItemIdentity(candidate)}</code></button>) : <EmptyPanel>请选择一个分类</EmptyPanel>}{group && filteredItems.length === 0 && <EmptyPanel>没有匹配的对象</EmptyPanel>}</div></section>
      <section className={`initialization-panel initialization-detail${item ? " mobile-active" : ""}`} aria-label="初始化配置"><header><h4>初始化配置</h4><span>{item ? "编辑开局状态" : "等待选择"}</span></header><div className="initialization-panel-body">{item ? <Detail item={item} preview={preview} document={document} scenarioId={scenarioId} onChange={onChange} onDeleteResourcePool={onDeleteResourcePool} /> : <EmptyPanel>请选择一个对象以配置初始化状态</EmptyPanel>}</div></section>
    </div>
  </div>;
}
