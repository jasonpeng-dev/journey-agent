import { type KeyboardEvent, type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { authoredDisplayName, type JsonObject } from "../editor";
import type { RootCollectionKey } from "../editor-collections";
import { authoredReferenceName, referenceOptions } from "./editor/ReferencePicker";
import { IdentityCreationDialog, type IdentityCreationField, type IdentityCreationValues } from "./editor/IdentityCreationDialog";
import { BooleanControl, InitializationField, ReadonlyFieldDisplay } from "./editor/FormPrimitives";
import type { InitializationFinding, InitializationPreview, InitializationProjection, InitializationProjectionItem } from "../types";
import { displayEnumValue, platformEnumLabel, type PlatformEnumDomain } from "../ui";
import { AuthoringActionButton, FieldActionRow } from "./editor/AuthoringActionButton";
import { useEditorFocusActivation } from "../editor-focus";
import { initializationPreviewIssuePresentations } from "./editor/initialization-preview-errors";
import { InitializationIssueDialog } from "./editor/InitializationIssueDialog";
import { typedScalarDisplay, typedScalarToken } from "./editor/typed-values";

type Props = {
  document: JsonObject;
  preview: InitializationPreview | null;
  loading: boolean;
  error: unknown | null;
  scenarioId: string;
  onRetry?: () => void;
  onChange: (document: JsonObject) => void;
  onDeleteResourcePool: (poolKey: string) => void;
  onDeleteRootCollectionItem?: (collection: RootCollectionKey, identity: string, subject: string) => void;
  onCreateResourcePool?: () => void;
  onAddRegionResourceKnowledge?: () => void;
};

const domainLabels: Record<string, string> = {
  basic: "开局入口", nodes: "节点", actors: "参与者", resources: "资源",
  relations: "关系", derived: "其他",
};

const subgroupHeadings: Record<string, string> = {
  basic: "开局入口", nodes: "节点类型", actors: "角色", resources: "资源分类",
  relations: "关系详情", derived: "其他概览",
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

function projectionItemMatchesRoute(item: InitializationProjectionItem, requestedItemId: string): boolean {
  if (item.id === requestedItemId) return true;
  if (item.locator.object_kind !== "resource_pool" || !item.locator.object_key || !requestedItemId.startsWith("pool:")) return false;
  return requestedItemId.slice("pool:".length).split(":", 1)[0] === item.locator.object_key;
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

function initializationReferenceHref(domain: string, key: string, scenarioId: string): string {
  if (domain === "resource_pool") {
    const query = new URLSearchParams({ domain: "resources", group: "resource-pools", item: `pool:${key}` });
    return `/scenarios/${scenarioId}/edit/initialization?${query}`;
  }
  const section = domain === "node" || domain === "region" ? "world-entities"
    : domain === "actor" ? "actors"
      : domain === "resource" ? "resources"
        : domain === "relation_type" ? "relation-types"
          : domain === "node_type" ? "node-types" : domain;
  return `/scenarios/${scenarioId}/edit/${section}${key ? `/${encodeURIComponent(key)}` : ""}`;
}

function workspaceEntity(item: InitializationProjectionItem | undefined): { kind: string; key: string } | null {
  const kind = item?.locator.object_kind;
  const key = item?.locator.object_key;
  return typeof kind === "string" && typeof key === "string" ? { kind, key } : null;
}

function withOptionalOverride(value: JsonObject, field: string, nextValue: unknown): JsonObject {
  const next = { ...value };
  if (nextValue === undefined) delete next[field];
  else next[field] = nextValue;
  return next;
}

type InitializationChoice = { key: string; name: string; value?: unknown };
type InitializationEmptyOption = { label: string; value: null };
const DEFAULT_OPTION = "__initialization_model_default__";
const NULL_OPTION = "__initialization_nullable_unset__";
const INVALID_OPTION = "__initialization_invalid_current_value__";
const MISSING_OPTION = "__initialization_missing_required_value__";

function SelectField({ label, value, choices, onChange, source, enumType, path = label, required = false, navigationDomain, emptyOption, defaultValue, defaultLabel, useDefault = false }: { label: string; value: unknown; choices: InitializationChoice[]; onChange: (value: unknown) => void; source?: string; enumType?: PlatformEnumDomain; path?: string; required?: boolean; navigationDomain?: string; emptyOption?: InitializationEmptyOption; defaultValue?: unknown; defaultLabel?: string; useDefault?: boolean }) {
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const valueOf = (choice: InitializationChoice) => choice.value === undefined ? choice.key : choice.value;
  const selectedChoice = choices.find((choice) => Object.is(valueOf(choice), value));
  const missing = required && (value === null || value === undefined || (typeof value === "string" && !value.trim()));
  const invalid = value !== null && value !== undefined && !missing && !selectedChoice;
  const selected = useDefault && defaultValue !== undefined ? DEFAULT_OPTION
    : (value === null || value === undefined) && emptyOption ? NULL_OPTION
      : selectedChoice ? `choice:${choices.indexOf(selectedChoice)}`
        : invalid ? INVALID_OPTION
          : missing ? MISSING_OPTION : "";
  const section = navigationDomain === "resource_pool" ? "initialization" : navigationDomain === "node" || navigationDomain === "region" ? "world-entities" : navigationDomain === "actor" ? "actors" : navigationDomain === "resource" ? "resources" : navigationDomain === "relation_type" ? "relation-types" : navigationDomain === "node_type" ? "node-types" : navigationDomain;
  const ownerHref = scenarioId && navigationDomain ? `/scenarios/${scenarioId}/edit/${section}` : undefined;
  const targetHref = scenarioId && navigationDomain && selectedChoice ? initializationReferenceHref(navigationDomain, selectedChoice.key, scenarioId) : undefined;
  const validationError = invalid ? navigationDomain ? "当前引用无效，请选择一个有效项。" : "当前值无效，请选择一个允许的值。" : missing ? "请选择有效的现有项。" : undefined;
  return <>
    <InitializationField label={label} path={path} required={required} headingAddon={source && <SourceBadge source={source} />} error={validationError}>
    <select aria-invalid={invalid || missing || undefined} value={selected} onChange={(event) => {
      const token = event.target.value;
      if (token === DEFAULT_OPTION) onChange(undefined);
      else if (token === NULL_OPTION) onChange(null);
      else if (token.startsWith("choice:")) onChange(valueOf(choices[Number(token.slice("choice:".length))]));
    }}>
      {defaultValue !== undefined && <option value={DEFAULT_OPTION}>使用默认值（{defaultLabel ?? String(defaultValue)}）</option>}
      {emptyOption && <option value={NULL_OPTION}>{emptyOption.label}</option>}
      {missing && <option value={MISSING_OPTION} disabled>尚未设置，请选择有效值</option>}
      {invalid && <option value={INVALID_OPTION} disabled>当前值无效：{String(value)}</option>}
      {choices.map((choice, index) => <option value={`choice:${index}`} key={`${choice.key}:${index}`}>{enumType ? platformEnumLabel(enumType, choice.key) : choice.name}</option>)}
    </select>
    </InitializationField>
    {navigationDomain && <FieldActionRow><InitializationOwnerLink to={targetHref ?? ownerHref ?? "../"}>{targetHref ? "前往" + (selectedChoice?.name ?? selected) : "前往" + (navigationDomain === "node" || navigationDomain === "region" ? "世界实体" : navigationDomain === "actor" ? "参与者" : navigationDomain === "resource" ? "资源定义" : navigationDomain === "resource_pool" ? "资源池" : navigationDomain === "relation_type" ? "关系类型" : navigationDomain === "node_type" ? "节点类型" : navigationDomain.replaceAll("_", " "))}</InitializationOwnerLink></FieldActionRow>}
  </>;
}

function ScalarField({ label, value, onChange, source, type = "text", path = label, required = false, defaultValue, defaultLabel, useDefault = false }: { label: string; value: unknown; onChange: (value: unknown) => void; source?: string; type?: "text" | "number" | "boolean" | "checkbox"; path?: string; required?: boolean; defaultValue?: unknown; defaultLabel?: string; useDefault?: boolean }) {
  const controlId = useId();
  if (type === "boolean" || type === "checkbox") {
    const valueMissing = required && !useDefault && value !== true && value !== false;
    const emptyLabel = useDefault && defaultValue !== undefined ? `使用默认值（${defaultLabel ?? (defaultValue ? "是" : "否")}）` : "请选择…";
    return <BooleanControl label={label} path={path} value={useDefault ? null : value} onChange={(next) => onChange(next)} allowEmpty onEmpty={() => onChange(undefined)} emptyLabel={emptyLabel} required={required && !useDefault} error={valueMissing ? "请选择“是”或“否”。" : undefined} layout="initialization" headingAddon={source ? <SourceBadge source={source} /> : undefined} />;
  }
  const missing = required && (value === null || value === undefined || (typeof value === "string" && !value.trim()));
  return <InitializationField label={label} path={path} required={required} headingAddon={source && <SourceBadge source={source} />} error={missing ? "此项为必填内容。" : undefined}>
    {type === "number" && defaultValue !== undefined && <small className="initialization-default-note">留空使用默认值（{defaultLabel ?? String(defaultValue)}）</small>}
    <input id={`${controlId}-input`} type={type} value={type === "number" && typeof value !== "number" ? "" : String(value ?? "")} onChange={(event) => onChange(type === "number" ? (event.target.value === "" ? undefined : Number(event.target.value)) : event.target.value)} />
  </InitializationField>;
}
export function SourceBadge({ source }: { source: string }) {
  return <small className={`source-badge source-${source.toLowerCase()}`}>{displayEnumValue("source", source)}</small>;
}

function InitializationOwnerLink({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return <AuthoringActionButton className={className} intent="navigate" to={to}>{children}</AuthoringActionButton>;
}

function findingFor(preview: InitializationPreview, identity: string): InitializationFinding | undefined {
  return preview.projection.findings.find((item) => item.identity === identity);
}

function designHref(item: InitializationProjectionItem, scenarioId: string): string {
  const locator = item.locator;
  if (!locator.object_key) return `/scenarios/${scenarioId}/edit/${locator.section}`;
  const discriminator = locator.object_kind === "relation" || locator.object_kind === "public_reference" ? `?kind=${locator.object_kind}` : "";
  return `/scenarios/${scenarioId}/edit/${locator.section}/${encodeURIComponent(locator.object_key)}${discriminator}`;
}

function Detail(props: { item: InitializationProjectionItem; preview: InitializationPreview; document: JsonObject; scenarioId: string; focusFactKey?: string; onChange: (document: JsonObject) => void; onDeleteResourcePool: (poolKey: string) => void; onDeleteRootCollectionItem: (collection: RootCollectionKey, identity: string, subject: string) => void }) {
  const { item, scenarioId } = props;
  return <div className="initialization-detail-composition">
    <DetailContent {...props} />
    {item.locator.section !== "initialization" && <FieldActionRow><AuthoringActionButton intent="navigate" to={designHref(item, scenarioId)}>前往完整定义</AuthoringActionButton></FieldActionRow>}
  </div>;
}

function AvailabilityRequirementCreateDialog({ document, onCancel, onCreate }: { document: JsonObject; onCancel: () => void; onCreate: (requirement: JsonObject) => void }) {
  const options = referenceOptions(document, "fact");
  const fields: IdentityCreationField[] = [
    { key: "fact_ref", label: "事实", type: "select", required: true, omitEmptyOption: true, options, emptyMessage: "当前没有可选事实。", ownerHref: "../world-entities", ownerLabel: "前往世界实体" },
  ];
  const create = (values: IdentityCreationValues): string | null => {
    const qualifiedKey = values.fact_ref?.trim() ?? "";
    const [nodeKey, factKey] = qualifiedKey.split(".");
    const world = object(document.world);
    const node = array(world.nodes).find((candidate) => candidate.key === nodeKey);
    const fact = array(node?.facts).find((candidate) => candidate.key === factKey);
    if (!node || !fact || !nodeKey || !factKey) return "请选择一个现有事实。";
    onCreate({ node_key: nodeKey, fact_key: factKey });
    return null;
  };
  return <IdentityCreationDialog title="添加可用性要求" fields={fields} onCancel={onCancel} onCreate={create} />;
}
function ResourcePoolDetail({ document, pool, poolKey, heading, onChange, onDelete }: { document: JsonObject; pool: JsonObject; poolKey: string; heading: ReactNode; onChange: (pool: JsonObject) => void; onDelete: (poolKey: string) => void }) {
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const [creationOpen, setCreationOpen] = useState(false);
  const world = object(document.world);
  const nodeChoices = array(world.nodes).map((node) => ({ key: String(node.key), name: String(node.name ?? node.key) }));
  const locality = object(object(document.metadata).locality);
  const localityEnabled = locality.enabled === true && locality.scoped_resources === true;
  const regionChoices = localityEnabled
    ? nodeChoices.filter((choice) => array(world.nodes).some((node) => node.key === choice.key && node.node_type_key === locality.region_node_type_key))
    : [];
  const facilityChoices = localityEnabled && pool.region_key
    ? nodeChoices.filter((choice) => {
      const facility = array(world.nodes).find((node) => node.key === choice.key && node.node_type_key === locality.facility_node_type_key);
      if (!facility) return false;
      if (typeof locality.located_in_relation_type_key !== "string") return true;
      const relations = array(world.relations).filter((relation) => relation.source_node_key === choice.key && relation.relation_type_key === locality.located_in_relation_type_key);
      return relations.length !== 1 || relations[0].target_node_key === pool.region_key;
    })
    : [];
  const resources = array(world.resources).map((resource) => ({ key: String(resource.key), name: String(resource.name ?? resource.key) }));
  const requirement = pool.availability_requirement && typeof pool.availability_requirement === "object" && !Array.isArray(pool.availability_requirement) ? pool.availability_requirement as JsonObject : null;
  const requirementIdentity = requirement ? `${String(requirement.node_key ?? "")}.${String(requirement.fact_key ?? "")}` : "";
  const requirementNode = requirement ? array(world.nodes).find((node) => node.key === requirement.node_key) : undefined;
  const requirementFact = requirementNode ? array(requirementNode.facts).find((fact) => fact.key === requirement?.fact_key) : undefined;
  const requirementFactHref = scenarioId && requirement ? `/scenarios/${scenarioId}/edit/world-entities/${encodeURIComponent(String(requirement.node_key))}?focus_path=${encodeURIComponent(`facts.${String(requirement.fact_key)}`)}` : undefined;
  const update = (key: string, value: unknown) => {
    const next = { ...pool };
    if (value === undefined) delete next[key];
    else next[key] = value;
    onChange(next);
  };
  return <>
    {heading}
    <section className="initialization-detail-section"><h5>资源池</h5>
      <div className="initialization-field"><span>稳定键</span><output className="readonly-field-display stable-identity-value">{poolKey}</output></div>
      <SelectField label="资源定义" value={pool.resource_key} choices={resources} source="EXPLICIT" path={`initialization.resource_pools.${poolKey}.resource_key`} required navigationDomain="resource" onChange={(value) => update("resource_key", value)} />
      <SelectField label="所属区域" value={pool.region_key} choices={regionChoices} emptyOption={{ label: "全局范围", value: null }} source="EXPLICIT" path={`initialization.resource_pools.${poolKey}.region_key`} navigationDomain="node" onChange={(value) => {
        const next: JsonObject = { ...pool, region_key: value };
        const facility = array(world.nodes).find((node) => node.key === pool.facility_key);
        const links = typeof locality.located_in_relation_type_key === "string"
          ? array(world.relations).filter((relation) => relation.source_node_key === pool.facility_key && relation.relation_type_key === locality.located_in_relation_type_key)
          : [];
        const staticallyLinkedRegion = links.length === 1 ? links[0].target_node_key : null;
        if (value === null || (staticallyLinkedRegion && staticallyLinkedRegion !== value) || (!facility && pool.facility_key)) next.facility_key = null;
        onChange(next);
      }} />
      <SelectField label="所在设施" value={pool.facility_key} choices={facilityChoices} emptyOption={{ label: "不指定设施", value: null }} source="EXPLICIT" path={`initialization.resource_pools.${poolKey}.facility_key`} navigationDomain="node" onChange={(value) => update("facility_key", value)} />
      <ScalarField label="数量" value={pool.quantity} type="number" source="EXPLICIT" path={`initialization.resource_pools.${poolKey}.quantity`} required onChange={(value) => update("quantity", value)} />
      <ScalarField label="预留数量" value={pool.reserved_value} type="number" source={Object.hasOwn(pool, "reserved_value") ? "EXPLICIT" : "DEFAULT"} defaultValue={0} defaultLabel="0" useDefault={!Object.hasOwn(pool, "reserved_value")} path={`initialization.resource_pools.${poolKey}.reserved_value`} onChange={(value) => update("reserved_value", value)} />
      <SelectField label="可用状态" value={pool.availability} path={`initialization.resource_pools.${poolKey}.availability`} choices={[{ key: "AVAILABLE", name: "AVAILABLE" }, { key: "UNAVAILABLE", name: "UNAVAILABLE" }]} enumType="resource_pool_availability" source={Object.hasOwn(pool, "availability") ? "EXPLICIT" : "DEFAULT"} defaultValue="AVAILABLE" defaultLabel="可用" useDefault={!Object.hasOwn(pool, "availability")} onChange={(value) => update("availability", value)} />
    </section>
    <section className="initialization-detail-section"><h5>可用性要求</h5>
      {requirement ? <><div className="initialization-field" data-field-path={`initialization.resource_pools.${poolKey}.availability_requirement`}><span>事实身份</span><output className="readonly-field-display">{requirementIdentity}</output></div>{requirementFactHref && <FieldActionRow><InitializationOwnerLink to={requirementFactHref}>前往{typeof requirementFact?.name === "string" ? requirementFact.name : "所选事实"}</InitializationOwnerLink></FieldActionRow>}{requirementFact?.value_type === "ENUM" ? <SelectField label="要求值" value={requirement.value} choices={(Array.isArray(requirementFact.allowed_values) ? requirementFact.allowed_values : []).flatMap((candidate) => typeof candidate === "string" || typeof candidate === "number" || typeof candidate === "boolean" ? [{ key: typedScalarToken(candidate), name: typedScalarDisplay(candidate, requirementFact), value: candidate }] : [])} path={`initialization.resource_pools.${poolKey}.availability_requirement.value`} required source="EXPLICIT" onChange={(value) => update("availability_requirement", { ...requirement, value })} /> : <ScalarField label="要求值" value={requirement.value} source="EXPLICIT" path={`initialization.resource_pools.${poolKey}.availability_requirement.value`} required type={requirementFact?.value_type === "INTEGER" ? "number" : requirementFact?.value_type === "BOOLEAN" ? "boolean" : "text"} onChange={(value) => { const next = { ...requirement }; if (value === undefined) delete next.value; else next.value = value; update("availability_requirement", next); }} />}{!requirementFact && <small className="field-error" role="alert">当前引用无效，请选择有效事实。</small>}<button type="button" className="editor-button editor-button-secondary" onClick={() => { const next = { ...pool }; delete next.availability_requirement; onChange(next); }}>移除可用性要求</button></>: <><p className="typed-help">选择已有事实并填写要求值后，才能添加可用性要求。</p><button type="button" className="editor-button editor-button-secondary" onClick={() => setCreationOpen(true)}>添加可用性要求</button></>}
    </section>
    <section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="可见性" value={pool.visibility} path={`initialization.resource_pools.${poolKey}.visibility`} choices={[{ key: "VISIBLE", name: "可见" }, { key: "HIDDEN", name: "隐藏" }]} enumType="resource_pool_visibility" source={Object.hasOwn(pool, "visibility") ? "EXPLICIT" : "DEFAULT"} defaultValue="VISIBLE" defaultLabel="可见" useDefault={!Object.hasOwn(pool, "visibility")} onChange={(value) => update("visibility", value)} /><ScalarField label="可由调查发现" value={pool.survey_discoverable} type="checkbox" source={Object.hasOwn(pool, "survey_discoverable") ? "EXPLICIT" : "DEFAULT"} defaultValue={false} defaultLabel="否" useDefault={!Object.hasOwn(pool, "survey_discoverable")} path={`initialization.resource_pools.${poolKey}.survey_discoverable`} onChange={(value) => update("survey_discoverable", value)} /></section>
    <button type="button" className="editor-button editor-button-danger" onClick={() => onDelete(poolKey)}>删除此资源池</button>
    {creationOpen && <AvailabilityRequirementCreateDialog document={document} onCancel={() => setCreationOpen(false)} onCreate={(value) => { update("availability_requirement", value); setCreationOpen(false); }} />}
  </>;
}

function derivedDefinitionFor(document: JsonObject, key: string): JsonObject | undefined {
  return array(document.derived_states).find((candidate) => candidate.key === key);
}

function factDefinitionFor(document: JsonObject, nodeKey: string, factKey: string): JsonObject | undefined {
  const node = array(object(document.world).nodes).find((candidate) => candidate.key === nodeKey);
  return array(node?.facts).find((candidate) => candidate.key === factKey);
}

function nodeDefinitionFor(document: JsonObject, nodeKey: string): JsonObject | undefined {
  return array(object(document.world).nodes).find((candidate) => candidate.key === nodeKey);
}

function derivedProjectionItem(preview: InitializationPreview, key: string): InitializationProjectionItem | undefined {
  for (const domain of preview.projection.domains) {
    for (const group of domain.groups) {
      const item = group.items.find((candidate) => candidate.id === `derived:${key}` || candidate.locator.object_key === key);
      if (item) return item;
    }
  }
  return undefined;
}

function derivedTypedValue(value: unknown, definition?: JsonObject): string {
  if (value === null || value === undefined) return "未知";
  const display = typedScalarDisplay(value, definition ?? {});
  if (display === "AVAILABLE") return "是";
  if (display === "UNAVAILABLE") return "否";
  return display;
}

function derivedKnowledgeValue(value: unknown, definition?: JsonObject): string {
  if (value === null || value === undefined) return "未知";
  if (value === "UNKNOWN") return "未知";
  if (value === "KNOWN") return "已知";
  if (value === "HIDDEN") return "隐藏";
  return derivedTypedValue(value, definition);
}

function readonlyReference(name: ReactNode, key: string): ReactNode {
  return <span className="initialization-reference-value"><strong>{name || key || "未命名对象"}</strong>{key && <code>{key}</code>}</span>;
}

function readonlyValueField(label: string, value: ReactNode, source?: string): ReactNode {
  return <div className="initialization-readonly-field"><span className="initialization-readonly-field-label">{label}{source && <SourceBadge source={source} />}</span><ReadonlyFieldDisplay value={value} /></div>;
}

function dependencyIdentity(name: ReactNode, key: string, type: string): ReactNode {
  return <div className="derived-dependency-identity"><div className="derived-dependency-reference"><strong>{name || key || "未命名对象"}</strong>{key && <code>{key}</code>}</div><span className="derived-dependency-type-badge">{type}</span></div>;
}

function knowledgeGateIdentity(value: JsonObject): string {
  const nodeKey = stringValue(value.node_key);
  const factKey = stringValue(value.fact_key);
  const acceptedValues = Array.isArray(value.accepted_values)
    ? value.accepted_values.map((candidate) => JSON.stringify(candidate) ?? String(candidate)).sort()
    : [];
  return JSON.stringify([nodeKey, factKey, acceptedValues]);
}

function knowledgeGateFrom(value: JsonObject): JsonObject {
  const gate = object(value.knowledge_gate);
  return Object.keys(gate).length > 0 ? gate : {};
}

function repeatedKnowledgeGates(dependencies: JsonObject[]): Array<{ key: string; value: JsonObject; count: number }> {
  const counts = new Map<string, { value: JsonObject; count: number }>();
  for (const dependency of dependencies) {
    const gate = knowledgeGateFrom(dependency);
    if (Object.keys(gate).length === 0) continue;
    const key = knowledgeGateIdentity(gate);
    const current = counts.get(key);
    if (current) current.count += 1;
    else counts.set(key, { value: gate, count: 1 });
  }
  return [...counts.entries()]
    .filter(([, entry]) => entry.count >= 2)
    .map(([key, entry]) => ({ key, ...entry }));
}

function DerivedDependencyReadonly({ value, document, preview, suppressedGateKeys = new Set<string>() }: { value: JsonObject; document: JsonObject; preview: InitializationPreview; suppressedGateKeys?: ReadonlySet<string> }): ReactNode {
  const kind = typeof value.kind === "string" ? value.kind : "";
  const acceptedValues = Array.isArray(value.accepted_values) ? value.accepted_values : [];
  const gate = knowledgeGateFrom(value);
  const showGate = Object.keys(gate).length > 0 && !suppressedGateKeys.has(knowledgeGateIdentity(gate));
  if (kind === "FACT") {
    const nodeKey = typeof value.node_key === "string" ? value.node_key : "";
    const factKey = typeof value.fact_key === "string" ? value.fact_key : "";
    const node = nodeDefinitionFor(document, nodeKey);
    const fact = factDefinitionFor(document, nodeKey, factKey);
    const factName = fact ? String(fact.name ?? factKey) : authoredReferenceName(document, "fact", `${nodeKey}.${factKey}`) ?? factKey;
    const nodeName = node ? String(node.name ?? nodeKey) : authoredReferenceName(document, "node", nodeKey) ?? nodeKey;
    const accepted = acceptedValues.map((candidate) => typedScalarDisplay(candidate, fact ?? {})).join("、") || "未设置";
    const initial = fact ? typedScalarDisplay(fact.initial_value, fact) : "未设置";
    const knowledge = fact?.initial_visibility === "KNOWN" ? "已知" : fact?.initial_visibility === "HIDDEN" ? "隐藏" : "未知";
    const factReferenceName = nodeName && factName ? <><span>{nodeName}</span><span aria-hidden="true"> · </span><span>{factName}</span></> : factName || nodeName;
    return <article className="derived-dependency-readonly"><div className="derived-dependency-heading">{dependencyIdentity(factReferenceName, `${nodeKey}.${factKey}`, "事实条件")}</div>{readonlyValueField("要求值", accepted)}{readonlyValueField("真实初始值", initial)}{readonlyValueField("初始可见性", knowledge)}{showGate && <DerivedKnowledgeGateReadonly value={gate} document={document} />}</article>;
  }
  if (kind === "RESOURCE_AT_LEAST") {
    const resourceKey = typeof value.resource_key === "string" ? value.resource_key : "";
    const regionKey = typeof value.region_key === "string" ? value.region_key : "";
    const resourceName = authoredReferenceName(document, "resource", resourceKey) ?? resourceKey;
    const regionName = authoredReferenceName(document, "node", regionKey) ?? regionKey;
    const pools = array(object(document.initialization).resource_pools);
    const initialQuantity = pools.filter((pool) => pool.resource_key === resourceKey && pool.region_key === regionKey && pool.availability !== "UNAVAILABLE").reduce((total, pool) => total + Math.max(0, Number(pool.quantity ?? 0) - Number(pool.reserved_value ?? 0)), 0);
    const resourceReferenceName = regionName && resourceName ? <><span>{regionName}</span><span aria-hidden="true"> · </span><span>{resourceName}</span></> : resourceName || regionName;
    return <article className="derived-dependency-readonly"><div className="derived-dependency-heading">{dependencyIdentity(resourceReferenceName, `${regionKey} / ${resourceKey}`, "资源条件")}</div>{readonlyValueField("要求至少", String(value.minimum ?? "未设置"))}{readonlyValueField("真实初始数量", String(initialQuantity))}{showGate && <DerivedKnowledgeGateReadonly value={gate} document={document} />}</article>;
  }
  if (kind === "DERIVED_STATE") {
    const derivedKey = typeof value.derived_key === "string" ? value.derived_key : "";
    const definition = derivedDefinitionFor(document, derivedKey);
    const target = derivedProjectionItem(preview, derivedKey);
    const targetName = definition ? String(definition.name ?? derivedKey) : authoredReferenceName(document, "derived_state", derivedKey) ?? derivedKey;
    return <article className="derived-dependency-readonly"><div className="derived-dependency-heading">{dependencyIdentity(targetName, derivedKey, "派生状态条件")}</div>{readonlyValueField("要求值", acceptedValues.map((candidate) => derivedTypedValue(candidate, definition)).join("、") || "未设置")}{readonlyValueField("真实初始结果", derivedTypedValue(target?.context.truth, definition))}{readonlyValueField("玩家可知结果", derivedKnowledgeValue(target?.context.knowledge, definition))}{showGate && <DerivedKnowledgeGateReadonly value={gate} document={document} />}</article>;
  }
  return <article className="derived-dependency-readonly"><div className="derived-dependency-heading"><h6>未识别的条件</h6></div><p>当前条件类型暂不支持详细展示。</p></article>;
}

function DerivedKnowledgeGateReadonly({ value, document, shared = false, impactCount = 0 }: { value: JsonObject; document: JsonObject; shared?: boolean; impactCount?: number }): ReactNode {
  const gateFact = typeof value.node_key === "string" && typeof value.fact_key === "string" ? factDefinitionFor(document, value.node_key, value.fact_key) : undefined;
  const nodeKey = typeof value.node_key === "string" ? value.node_key : "";
  const factKey = typeof value.fact_key === "string" ? value.fact_key : "";
  const key = nodeKey && factKey ? `${nodeKey}.${factKey}` : "";
  const gateNode = nodeDefinitionFor(document, nodeKey);
  const nodeName = gateNode ? String(gateNode.name ?? nodeKey) : authoredReferenceName(document, "node", nodeKey) ?? nodeKey;
  const name = gateFact ? String(gateFact.name ?? key) : authoredReferenceName(document, "fact", key) ?? key;
  const accepted = Array.isArray(value.accepted_values) ? value.accepted_values.map((candidate) => typedScalarDisplay(candidate, gateFact ?? {})).join("、") : "未设置";
  const gateReferenceName = nodeName && name ? <><span>{nodeName}</span><span aria-hidden="true"> · </span><span>{name}</span></> : name;
  return <article className="derived-knowledge-gate"><div className="derived-knowledge-gate-heading"><h6>{shared ? "共同揭示条件" : "揭示条件"}</h6></div><p className="typed-help">满足后，玩家/智能体才能获知以下条件；不影响该派生状态的真实计算。</p>{readonlyValueField("事实", readonlyReference(gateReferenceName, key))}{readonlyValueField("要求值", accepted)}{shared && readonlyValueField("影响条件", `${impactCount} 项`)}</article>;
}

function DetailContent({ item, preview, document, scenarioId, focusFactKey, onChange, onDeleteResourcePool, onDeleteRootCollectionItem }: { item: InitializationProjectionItem; preview: InitializationPreview; document: JsonObject; scenarioId: string; focusFactKey?: string; onChange: (document: JsonObject) => void; onDeleteResourcePool: (poolKey: string) => void; onDeleteRootCollectionItem: (collection: RootCollectionKey, identity: string, subject: string) => void }) {
  const world = object(document.world);
  const actors = object(document.actors);
  const initialization = object(document.initialization);
  const nodeChoices = array(world.nodes).map((node) => ({ key: String(node.key), name: String(node.name ?? node.key) }));
  const actorChoices = array(actors.actor_profiles).map((actor) => ({ key: String(actor.key), name: String(actor.name ?? actor.key) }));
  const heading = <header className="initialization-detail-heading"><div><h4>{displayItemLabel(item, document)}</h4><code>{displayItemIdentity(item, true)}</code></div></header>;

  if (item.id === "bootstrap-entry") return <div>{heading}<section className="initialization-detail-section"><h5>开局入口</h5><SelectField label="起始节点" value={initialization.start_node_key} path="initialization.start_node_key" required navigationDomain="node" choices={nodeChoices} source="EXPLICIT" onChange={(value) => onChange({ ...document, initialization: { ...initialization, start_node_key: value } })} /><SelectField label="主要参与者" value={initialization.primary_actor_key} path="initialization.primary_actor_key" required navigationDomain="actor" choices={actorChoices} source="EXPLICIT" onChange={(value) => onChange({ ...document, initialization: { ...initialization, primary_actor_key: value } })} /></section></div>;

  if (item.id.startsWith("node:")) {
    const key = item.id.slice(5);
    const nodes = array(world.nodes);
    const index = nodes.findIndex((node) => node.key === key);
    const node = nodes[index];
    if (!node) return null;
    const update = (value: JsonObject) => onChange(replaceIn(document, "world", "nodes", index, value));
    return <div>
      {heading}
      <section className="initialization-detail-section"><h5>真实初始状态</h5><SelectField label="节点访问状态" value={node.initial_access} path={`world.nodes.${key}.initial_access`} required choices={[{ key: "AVAILABLE", name: "可用" }, { key: "LOCKED", name: "锁定" }]} enumType="node_access" source="EXPLICIT" onChange={(value) => update(withOptionalOverride(node, "initial_access", value))} /></section>
      <section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="节点可见性" value={node.initial_visibility} path={`world.nodes.${key}.initial_visibility`} required choices={[{ key: "KNOWN", name: "已知" }, { key: "HIDDEN", name: "隐藏" }]} enumType="node_knowledge" source="EXPLICIT" onChange={(value) => update(withOptionalOverride(node, "initial_visibility", value))} /></section>
      {array(node.facts).map((fact, factIndex) => <section className={`initialization-fact${String(fact.key) === focusFactKey ? " is-focused" : ""}`} data-fact-key={String(fact.key)} key={String(fact.key)}>
        <div><strong>{String(fact.name ?? fact.key)}</strong><code>{String(fact.key)}</code>{String(fact.key) === focusFactKey && <small>当前定位</small>}<small>{displayEnumValue("value_type", String(fact.value_type ?? ""))}</small></div>
        {fact.value_type === "ENUM" ? <SelectField label="真实值 · 初始值" value={fact.initial_value} path={`world.nodes.${key}.facts.${String(fact.key)}.initial_value`} required choices={(Array.isArray(fact.allowed_values) ? fact.allowed_values : []).flatMap((candidate) => typeof candidate === "string" || typeof candidate === "number" || typeof candidate === "boolean" ? [{ key: typedScalarToken(candidate), name: typedScalarDisplay(candidate, fact), value: candidate }] : [])} source="EXPLICIT" onChange={(value) => update({ ...node, facts: array(node.facts).map((old, oldIndex) => oldIndex === factIndex ? withOptionalOverride(fact, "initial_value", value) : old) })} /> : <ScalarField label="真实值 · 初始值" value={fact.initial_value} source="EXPLICIT" path={`world.nodes.${key}.facts.${String(fact.key)}.initial_value`} required type={fact.value_type === "INTEGER" ? "number" : fact.value_type === "BOOLEAN" ? "boolean" : "text"} onChange={(value) => update({ ...node, facts: array(node.facts).map((old, oldIndex) => oldIndex === factIndex ? withOptionalOverride(fact, "initial_value", value) : old) })} />}
        <SelectField label="知识 · 可见性" value={fact.initial_visibility} path={`world.nodes.${key}.facts.${String(fact.key)}.initial_visibility`} required choices={[{ key: "KNOWN", name: "已知" }, { key: "HIDDEN", name: "隐藏" }]} enumType="node_knowledge" source="EXPLICIT" onChange={(value) => update({ ...node, facts: array(node.facts).map((old, oldIndex) => oldIndex === factIndex ? withOptionalOverride(fact, "initial_visibility", value) : old) })} />
      </section>)}
    </div>;
  }

  if (item.id.startsWith("actor:")) {
    const key = item.id.slice(6);
    const profiles = array(actors.actor_profiles);
    const index = profiles.findIndex((actor) => actor.key === key);
    const actor = profiles[index];
    if (!actor) return null;
    const update = (value: JsonObject) => onChange(replaceIn(document, "actors", "actor_profiles", index, value));
    const reachability = findingFor(preview, `actor:${key}:command_reachability`);
    return <div>{heading}<section className="initialization-detail-section"><h5>参与者开局状态</h5><SelectField label="初始位置" value={actor.initial_node_key} path={`actors.actor_profiles.${key}.initial_node_key`} required navigationDomain="node" choices={nodeChoices} source="EXPLICIT" onChange={(value) => update(withOptionalOverride(actor, "initial_node_key", value))} /><SelectField label="指挥可达性" value={actor.command_reachability} path={`actors.actor_profiles.${key}.command_reachability`} choices={[{ key: "ONLINE", name: "在线" }, { key: "DISCONNECTED", name: "中断" }]} enumType="command_reachability" source={reachability?.source ?? "DEFAULT"} defaultValue="ONLINE" defaultLabel="在线" useDefault={!Object.hasOwn(actor, "command_reachability")} onChange={(value) => update(withOptionalOverride(actor, "command_reachability", value))} /><div className="initialization-readonly-row"><span>运行时初始状态</span><strong>{displayEnumValue("generic", "ACTIVE")}</strong><SourceBadge source="ENGINE" /></div><button type="button" className="editor-button editor-button-secondary" onClick={() => onChange({ ...document, initialization: { ...initialization, primary_actor_key: key } })}>设为主要参与者</button></section></div>;
  }

  if (item.id.startsWith("relation:")) {
    const key = item.id.slice(9);
    const relations = array(world.relations);
    const index = relations.findIndex((relation) => String(relation.key ?? `${relation.source_node_key}__${relation.relation_type_key}__${relation.target_node_key}`) === key);
    const relation = relations[index];
    if (!relation) return null;
    const update = (value: JsonObject) => onChange(replaceIn(document, "world", "relations", index, value));
    const source = findingFor(preview, `relation:${key}:initial_visibility`)?.source ?? "DEFAULT";
    return <div>{heading}<section className="initialization-detail-section"><h5>关系拓扑 · 只读</h5><p>{String(relation.source_node_key)} → {String(relation.target_node_key)}</p><code>{String(relation.relation_type_key)}</code></section><section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="关系可见性" value={relation.initial_visibility} path={`world.relations.${key}.initial_visibility`} choices={[{ key: "VISIBLE", name: "可见" }, { key: "HIDDEN", name: "隐藏" }]} enumType="visibility" source={source} defaultValue="VISIBLE" defaultLabel="可见" useDefault={!Object.hasOwn(relation, "initial_visibility")} onChange={(value) => update(withOptionalOverride(relation, "initial_visibility", value))} /></section></div>;
  }

  if (item.id.startsWith("pool:")) {
    const poolKey = projectionObjectKey(item);
    const pools = array(initialization.resource_pools);
    const index = pools.findIndex((pool) => pool.pool_key === poolKey);
    const pool = pools[index];
    const finding = findingFor(preview, item.field_ids[0]);
    if (!pool) return <div>{heading}<section className="initialization-detail-section"><h5>兼容来源 · 只读</h5><p>该运行时资源池由兼容旧版字段生成。</p><SourceBadge source="LEGACY_FALLBACK" /><pre className="initialization-legacy-resource-value" data-field-path={`world.resources.${String(item.context.resource_key ?? poolKey)}.initial_value`} tabIndex={-1}>{JSON.stringify(finding?.value ?? item.context, null, 2)}</pre></section></div>;
    return <ResourcePoolDetail document={document} pool={pool} poolKey={poolKey} heading={heading} onChange={(value) => onChange(replaceIn(document, "initialization", "resource_pools", index, value))} onDelete={onDeleteResourcePool} />;
  }

  if (item.id.startsWith("region-knowledge:")) {
    const key = item.id.slice("region-knowledge:".length);
    const states = array(initialization.region_resource_knowledge);
    const index = states.findIndex((state) => state.region_key === key);
    if (index < 0) return <div>{heading}<section className="initialization-detail-section"><h5>区域资源知识</h5><p>当前是推导出的默认值；工作副本中尚无对应配置行。</p><FieldActionRow><InitializationOwnerLink to={`/scenarios/${scenarioId}/edit/initialization?domain=resources&group=region-resource-knowledge`}>前往区域资源知识</InitializationOwnerLink></FieldActionRow></section></div>;
    const state = states[index] ?? { region_key: key, resource_inventory_visibility: "VISIBLE", resource_survey_completed: true };
    const source = index >= 0 ? "EXPLICIT" : "DEFAULT";
    const update = (value: JsonObject) => {
      const next = structuredClone(document) as JsonObject;
      const nextInitialization = object(next.initialization);
      nextInitialization.region_resource_knowledge = states.map((old, oldIndex) => oldIndex === index ? value : old);
      next.initialization = nextInitialization;
      onChange(next);
    };
    return <div>{heading}<section className="initialization-detail-section"><h5>玩家初始知识</h5><SelectField label="库存可见性" value={state.resource_inventory_visibility} path={`initialization.region_resource_knowledge.${key}.resource_inventory_visibility`} choices={[{ key: "VISIBLE", name: "可见" }, { key: "HIDDEN", name: "隐藏" }]} enumType="visibility" source={Object.hasOwn(state, "resource_inventory_visibility") ? source : "DEFAULT"} defaultValue="VISIBLE" defaultLabel="可见" useDefault={!Object.hasOwn(state, "resource_inventory_visibility")} onChange={(value) => update(withOptionalOverride(state, "resource_inventory_visibility", value))} /><ScalarField label="调查已完成" value={state.resource_survey_completed} type="checkbox" source={Object.hasOwn(state, "resource_survey_completed") ? source : "DEFAULT"} defaultValue={true} defaultLabel="是" useDefault={!Object.hasOwn(state, "resource_survey_completed")} path={`initialization.region_resource_knowledge.${key}.resource_survey_completed`} onChange={(value) => update(withOptionalOverride(state, "resource_survey_completed", value))} />{index >= 0 && <FieldActionRow><InitializationOwnerLink to={`/scenarios/${scenarioId}/edit/world-entities/${encodeURIComponent(key)}`}>前往{displayItemLabel(item, document)}</InitializationOwnerLink></FieldActionRow>}{index >= 0 && <button type="button" className="editor-button editor-button-danger" onClick={() => onDeleteRootCollectionItem("region_resource_knowledge", key, displayItemLabel(item, document))}>删除区域资源知识</button>}</section></div>;
  }

  if (item.id.startsWith("derived:")) {
    const derivedKey = projectionObjectKey(item) || item.id.slice("derived:".length);
    const definition = derivedDefinitionFor(document, derivedKey);
    const dependencies = array(definition?.dependencies);
    const truthValue = derivedTypedValue(item.context.truth, definition);
    const knowledgeValue = derivedKnowledgeValue(item.context.knowledge, definition);
    const repeatedGates = repeatedKnowledgeGates(dependencies);
    const repeatedGateKeys = new Set(repeatedGates.map((gate) => gate.key));
    return <div>{heading}
      <section className="initialization-detail-section"><div className="initialization-readonly-fields">{readonlyValueField("真实初始结果", truthValue, "DERIVED")}{readonlyValueField("玩家可知结果", knowledgeValue)}</div></section>
      <section className="initialization-detail-section"><h5>组成条件</h5><p className="typed-help">派生状态的真实结果由以下条件计算；揭示条件只控制玩家/智能体何时可以获知这些条件。</p><div className="derived-dependency-list">{dependencies.length > 0 ? dependencies.map((dependency, index) => <DerivedDependencyReadonly key={`${derivedKey}-dependency-${index}`} value={object(dependency)} document={document} preview={preview} suppressedGateKeys={repeatedGateKeys} />) : <p className="typed-help">暂无组成条件。</p>}{repeatedGates.map((gate) => <DerivedKnowledgeGateReadonly key={`shared-gate-${gate.key}`} value={gate.value} document={document} shared impactCount={gate.count} />)}</div></section>
      <p className="typed-help">以上为当前工作副本的只读计算结果。</p>
    </div>;
  }
}

function EmptyPanel({ children }: { children: string }) {
  return <div className="initialization-panel-empty">{children}</div>;
}

function fallbackProjection(document: JsonObject): InitializationProjection {
  const world = object(document.world);
  const actors = object(document.actors);
  const initialization = object(document.initialization);
  const nodes = array(world.nodes);
  const nodeTypes = array(world.node_types);
  const profiles = array(actors.actor_profiles);
  const roles = array(actors.roles);
  const relations = array(world.relations);
  const relationTypes = array(world.relation_types);
  const resources = array(world.resources);
  const pools = array(initialization.resource_pools);
  const legacyResourceStates = array(initialization.resource_initial_states);
  const regionStates = array(initialization.region_resource_knowledge);
  const makeItem = (id: string, label: string, section: string, kind: string | null, key: string | null, context: Record<string, unknown> = {}): InitializationProjectionItem => ({
    id, label, locator: { section, object_kind: kind, object_key: key, field_path: null }, field_ids: [], readonly: false, context,
  });
  const grouped = (items: Array<{ group: string; label: string; item: InitializationProjectionItem }>) => {
    const groups = new Map<string, { id: string; label: string; items: InitializationProjectionItem[] }>();
    for (const entry of items) {
      const current = groups.get(entry.group) ?? { id: entry.group, label: entry.label, items: [] };
      current.items.push(entry.item);
      groups.set(entry.group, current);
    }
    return [...groups.values()];
  };
  const nodeItems = nodes.flatMap((node) => {
    const key = stringValue(node.key);
    if (!key) return [];
    const typeKey = stringValue(node.node_type_key) || "unassigned";
    const typeName = nodeTypes.find((candidate) => candidate.key === typeKey)?.name;
    return [{ group: `node-type:${typeKey}`, label: stringValue(typeName) || (typeKey === "unassigned" ? "待完善节点类型" : typeKey), item: makeItem(`node:${key}`, stringValue(node.name) || key, "world-entities", "node", key, { key, type: typeName ?? typeKey }) }];
  });
  const actorItems = profiles.flatMap((actor) => {
    const key = stringValue(actor.key);
    if (!key) return [];
    const roleKey = stringValue(actor.role_key) || "unassigned";
    const roleName = roles.find((candidate) => candidate.key === roleKey)?.name;
    return [{ group: `role:${roleKey}`, label: stringValue(roleName) || (roleKey === "unassigned" ? "待完善角色" : roleKey), item: makeItem(`actor:${key}`, stringValue(actor.name) || key, "actors", "actor", key, { key, role: roleName ?? roleKey }) }];
  });
  const relationItems = relations.flatMap((relation) => {
    const key = stringValue(relation.key) || [relation.source_node_key, relation.relation_type_key, relation.target_node_key].map((part) => stringValue(part)).join("__");
    if (!key) return [];
    const typeKey = stringValue(relation.relation_type_key) || "unassigned";
    const typeName = relationTypes.find((candidate) => candidate.key === typeKey)?.name;
    const sourceName = authoredReferenceName(document, "node", stringValue(relation.source_node_key)) ?? stringValue(relation.source_node_key);
    const targetName = authoredReferenceName(document, "node", stringValue(relation.target_node_key)) ?? stringValue(relation.target_node_key);
    return [{ group: `relation-type:${typeKey}`, label: stringValue(typeName) || typeKey, item: makeItem(`relation:${key}`, `${sourceName} → ${targetName}`, "relations", "relation", key, { source: relation.source_node_key, type: typeName ?? typeKey, target: relation.target_node_key }) }];
  });
  const poolItems = pools.flatMap((pool) => {
    const poolKey = stringValue(pool.pool_key);
    const resourceKey = stringValue(pool.resource_key);
    if (!poolKey) return [];
    const identity = resourceKey ? `pool:${poolKey}:${resourceKey}:${stringValue(pool.region_key) || "global"}` : `pool:${poolKey}`;
    const resourceLabel = authoredReferenceName(document, "resource", resourceKey) ?? (resourceKey ? resourceKey : "待配置资源");
    return [{ group: "resource-pools", label: "资源池", item: { ...makeItem(identity, stringValue(pool.name) || `${authoredReferenceName(document, "node", stringValue(pool.facility_key ?? pool.region_key)) ?? "全局"} · ${resourceLabel}`, "initialization", "resource_pool", poolKey, { pool_key: poolKey, resource_key: resourceKey, region_key: pool.region_key, facility_key: pool.facility_key }), field_ids: [identity] } }];
  });
  const regionItems = regionStates.flatMap((state) => {
    const key = stringValue(state.region_key);
    if (!key) return [];
    return [{ group: "region-resource-knowledge", label: "区域资源知识", item: makeItem(`region-knowledge:${key}`, authoredReferenceName(document, "node", key) ?? key, "initialization", "region_resource_knowledge", key, { region_key: key }) }];
  });
  const compatibilityResourceItems = resources.flatMap((resource) => {
    const resourceKey = stringValue(resource.key);
    if (!resourceKey) return [];
    const identity = `pool:default:${resourceKey}:global`;
    return [{ group: "compatibility-resources", label: "兼容来源", item: { ...makeItem(identity, stringValue(resource.name) || resourceKey, "resources", "resource", resourceKey, { pool_key: "default", resource_key: resourceKey }), readonly: true, field_ids: [identity] } }];
  });
  const legacyResourceItems = legacyResourceStates.flatMap((state) => {
    const resourceKey = stringValue(state.resource_key);
    if (!resourceKey) return [];
    const scopeKey = stringValue(state.scope_node_key) || "global";
    if (scopeKey === "global" && compatibilityResourceItems.some((entry) => entry.item.id === `pool:default:${resourceKey}:global`)) return [];
    const resourceName = authoredReferenceName(document, "resource", resourceKey) ?? resourceKey;
    const identity = `pool:default:${resourceKey}:${scopeKey}`;
    return [{ group: "compatibility-resources", label: "兼容来源", item: { ...makeItem(identity, resourceName, "resources", "resource", resourceKey, { pool_key: "default", resource_key: resourceKey, scope_node_key: state.scope_node_key, value: state.value, reserved_value: state.reserved_value }), readonly: true, field_ids: [identity] } }];
  });
  const compatibilityItems = [...compatibilityResourceItems, ...legacyResourceItems];
  const derivedItems = array(document.derived_states).flatMap((state) => {
    const key = stringValue(state.key);
    return key ? [{ group: "derived-states", label: "派生状态", item: { ...makeItem(`derived:${key}`, stringValue(state.name) || key, "derived-states", "derived_state", key, { key }), readonly: true } }] : [];
  });
  const domains: InitializationProjection["domains"] = [
    { id: "basic", label: "开局入口", groups: [{ id: "entry", label: "开局入口", items: [makeItem("bootstrap-entry", "起始节点与主要参与者", "initialization", "initialization", null)] }] },
    { id: "nodes", label: "节点", groups: grouped(nodeItems) },
    { id: "actors", label: "参与者", groups: grouped(actorItems) },
    { id: "resources", label: "资源", groups: grouped([...poolItems, ...regionItems, ...compatibilityItems]) },
    { id: "relations", label: "关系", groups: grouped(relationItems) },
    { id: "derived", label: "其他", groups: grouped(derivedItems) },
  ];
  return {
    domains: domains.filter((domain) => domain.id === "basic" || domain.groups.length > 0),
    findings: [],
    summary: { nodes: nodes.length, actors: profiles.length, resource_pools: pools.length, relations: relations.length, derived_states: array(document.derived_states).length, warnings: 0 },
  };
}

function previewWithRecoverableRows(preview: InitializationPreview | null, document: JsonObject, error: unknown): InitializationPreview {
  const fallback = fallbackProjection(document);
  if (!preview || (error && !preview.partial)) return { revision: preview?.revision ?? 0, projection: fallback, parity: { published: false, initialization_changes: [], design_changes: [] } };
  if (!preview.partial) return preview;
  const domains = preview.projection.domains.map((domain) => ({ ...domain, groups: domain.groups.map((group) => ({ ...group, items: [...group.items] })) }));
  for (const fallbackDomain of fallback.domains) {
    let domain = domains.find((candidate) => candidate.id === fallbackDomain.id);
    if (!domain) { domain = { ...fallbackDomain, groups: [] }; domains.push(domain); }
    for (const fallbackGroup of fallbackDomain.groups) {
      let group = domain.groups.find((candidate) => candidate.id === fallbackGroup.id);
      if (!group) { group = { ...fallbackGroup, items: [] }; domain.groups.push(group); }
      const existing = new Set(group.items.map((item) => item.id));
      group.items.push(...fallbackGroup.items.filter((item) => !existing.has(item.id)));
    }
  }
  return { ...preview, projection: { ...preview.projection, domains } };
}

export function InitializationWorkspace({ document, preview, loading, error, scenarioId, onRetry, onChange, onDeleteResourcePool, onDeleteRootCollectionItem = () => undefined, onCreateResourcePool = () => undefined, onAddRegionResourceKnowledge = () => undefined }: Props) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [overviewOpen, setOverviewOpen] = useState(false);
  const [issuesOpen, setIssuesOpen] = useState(false);
  const issueButtonRef = useRef<HTMLButtonElement>(null);
  const workspacePreview = useMemo(() => previewWithRecoverableRows(preview, document, error), [document, error, preview]);
  const domains = workspacePreview.projection.domains;
  const requestedDomainId = searchParams.get("domain") ?? "";
  const requestedGroupId = searchParams.get("group") ?? "";
  const requestedItemId = searchParams.get("item") ?? "";
  const requestedFactKey = searchParams.get("fact") ?? "";
  const requestedFocusPath = searchParams.get("focus_path") ?? "";
  const domain = domains.find((candidate) => candidate.id === requestedDomainId);
  const inferredGroup = domain && !requestedGroupId && requestedItemId ? domain.groups.find((candidate) => candidate.items.some((candidateItem) => projectionItemMatchesRoute(candidateItem, requestedItemId))) : undefined;
  const group = domain?.groups.find((candidate) => candidate.id === requestedGroupId) ?? inferredGroup;
  const item = group?.items.find((candidate) => projectionItemMatchesRoute(candidate, requestedItemId));
  const resolvedItemId = item?.id;
  const focusStatus = useEditorFocusActivation(requestedFocusPath || null, searchParams.toString(), document, Boolean(resolvedItemId), ".initialization-detail .initialization-panel-body[data-editor-focus-scope]");
  const filteredItems = useMemo(() => (group?.items ?? []).filter((candidate) => `${displayItemLabel(candidate, document)} ${candidate.label} ${candidate.id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [document, group, search]);
  const selectPath = (nextDomain: string, nextGroup = "", nextItem = "") => {
    const next = new URLSearchParams(searchParams);
    for (const key of ["domain", "group", "item", "fact", "focus_path"]) next.delete(key);
    if (nextDomain) next.set("domain", nextDomain);
    if (nextGroup) next.set("group", nextGroup);
    if (nextItem) next.set("item", nextItem);
    setSearchParams(next, { replace: true });
  };
  useEffect(() => {
    const normalizedDomain = domain?.id ?? "";
    const normalizedGroup = group?.id ?? "";
    const normalizedItem = item?.id ?? "";
    if (normalizedDomain === requestedDomainId && normalizedGroup === requestedGroupId && normalizedItem === requestedItemId) return;
    const next = new URLSearchParams(searchParams);
    for (const key of ["domain", "group", "item", "fact"]) next.delete(key);
    if (normalizedDomain) next.set("domain", normalizedDomain);
    if (normalizedGroup) next.set("group", normalizedGroup);
    if (normalizedItem) next.set("item", normalizedItem);
    setSearchParams(next, { replace: true });
  }, [domain?.id, group?.id, item?.id, workspacePreview, requestedDomainId, requestedGroupId, requestedItemId, searchParams, setSearchParams]);
  useEffect(() => setSearch(""), [domain?.id, group?.id]);
  const issueSource = preview ? preview.issues ?? [] : error;
  const presentations = initializationPreviewIssuePresentations(issueSource, workspaceEntity(item), document, scenarioId);
  const summary = workspacePreview.projection.summary;
  const differs = workspacePreview.parity.published && (workspacePreview.parity.initialization_changes.length > 0 || workspacePreview.parity.design_changes.length > 0);
  return <div className="initialization-workspace">
     <header className="initialization-workspace-header"><div className="initialization-workspace-toolbar"><nav className="initialization-breadcrumb" aria-label="初始化层级"><button onClick={() => selectPath("")}>类别</button>{domain && <><span>/</span><button onClick={() => selectPath(domain.id)}>{domainLabels[domain.id] ?? domain.label}</button></>}{group && <><span>/</span><button onClick={() => selectPath(domain!.id, group.id)}>{displayGroupLabel(group)}</button></>}{item && <><span>/</span><strong>{displayItemLabel(item, document)}</strong></>}</nav><div className="initialization-status"><span>{summary.warnings > 0 ? `⚠ ${summary.warnings}` : "0 项警告"}</span>{differs && <span>当前草稿与最新发布版本不同</span>}{!preview && loading && <span role="status">正在读取开局配置</span>}{!preview && !loading && Boolean(error) && presentations.length === 0 && <span className="initialization-preview-unavailable" role="status">预览暂不可用{onRetry && <AuthoringActionButton intent="view" onPress={onRetry}>重新读取</AuthoringActionButton>}</span>}{presentations.length > 0 && <button ref={issueButtonRef} type="button" className="initialization-issue-trigger" aria-haspopup="dialog" aria-expanded={issuesOpen} aria-controls="initialization-issue-dialog" onClick={() => setIssuesOpen(true)}>查看问题（{presentations.length}）</button>}<button type="button" aria-expanded={overviewOpen} onClick={() => setOverviewOpen((open) => !open)}>初始化概览</button></div></div><p>配置当前版本的开局状态</p></header>
    {overviewOpen && <aside className="initialization-overview" aria-label="初始化概览"><div className="initialization-overview-heading"><div><h4>初始化概览</h4><p>当前草稿的开局规模与发布差异</p></div><button type="button" className="editor-button editor-button-ghost" onClick={() => setOverviewOpen(false)}>关闭</button></div><div className="initialization-metrics"><span><strong>{summary.nodes}</strong>节点</span><span><strong>{summary.actors}</strong>参与者</span><span><strong>{summary.resource_pools}</strong>资源池</span><span><strong>{summary.relations}</strong>关系</span><span><strong>{summary.derived_states}</strong>派生状态</span></div><div className="initialization-overview-summary"><span>警告 <strong>{summary.warnings}</strong></span><span>开局变化 <strong>{workspacePreview.parity.initialization_changes.length}</strong></span><span>设计变化 <strong>{workspacePreview.parity.design_changes.length}</strong></span><span>{workspacePreview.parity.published ? "对比最新发布版本" : "尚无发布版本"}</span></div></aside>}
    {focusStatus === "stale" && <p className="editor-focus-notice" role="status">所定位的配置项已不存在，已带到当前初始化区域，请检查当前配置。</p>}
    <div className="initialization-panels" data-testid="initialization-four-panel-workspace">
       <section className={`initialization-panel initialization-column${!domain ? " mobile-active" : ""}`} aria-label="类别" onKeyDown={navigateColumn}><header><h4>类别</h4><span>选择领域</span></header><div className="initialization-panel-body">{domains.map((candidate) => <button aria-current={candidate.id === domain?.id ? "true" : undefined} className={candidate.id === domain?.id ? "selected" : ""} key={candidate.id} onClick={() => selectPath(candidate.id)}><span>{domainLabels[candidate.id] ?? candidate.label}</span><small>{candidate.groups.reduce((total, value) => total + value.items.length, 0)}</small></button>)}</div></section>
       <section className={`initialization-panel initialization-column${domain && !group ? " mobile-active" : ""}`} aria-label="分类" onKeyDown={navigateColumn}><header><h4>{domain ? subgroupHeadings[domain.id] ?? "分类" : "分类"}</h4><span>{domain ? domainLabels[domain.id] ?? domain.label : "等待选择"}</span></header><div className="initialization-panel-body">{domain ? domain.groups.map((candidate) => <button aria-current={candidate.id === group?.id ? "true" : undefined} className={candidate.id === group?.id ? "selected" : ""} key={candidate.id} onClick={() => selectPath(domain.id, candidate.id)}><span>{displayGroupLabel(candidate)}</span><small>{candidate.items.length}</small></button>) : <EmptyPanel>请选择一个类别</EmptyPanel>}</div></section>
       <section className={`initialization-panel initialization-column initialization-items${group && !item ? " mobile-active" : ""}`} aria-label="对象" onKeyDown={navigateColumn}><header><h4>{group ? domain?.id === "derived" && group.id === "derived-states" ? "具体派生状态" : displayGroupLabel(group) : "对象"}</h4><span>{group ? "选择具体对象" : "等待选择"}</span>{group && <input aria-label="搜索当前项目" placeholder="名称或稳定键" value={search} onChange={(event) => setSearch(event.target.value)} />}{group?.id === "resource-pools" && <button type="button" className="initialization-add" onClick={onCreateResourcePool}>＋ 新增资源池</button>}{group?.id === "region-resource-knowledge" && <button type="button" className="initialization-add" onClick={onAddRegionResourceKnowledge}>＋ 新增区域资源知识</button>}</header><div className="initialization-panel-body">{group ? filteredItems.map((candidate) => <button aria-current={candidate.id === item?.id ? "true" : undefined} className={candidate.id === item?.id ? "selected" : ""} key={candidate.id} onClick={() => selectPath(domain!.id, group.id, candidate.id)}><span>{displayItemLabel(candidate, document)}</span><code>{displayItemIdentity(candidate)}</code></button>) : <EmptyPanel>请选择一个分类</EmptyPanel>}{group && filteredItems.length === 0 && <EmptyPanel>没有匹配的对象</EmptyPanel>}</div></section>
      <section className={`initialization-panel initialization-detail${item ? " mobile-active" : ""}`} aria-label="初始化配置"><header><h4>初始化配置</h4><span>{item ? "编辑开局状态" : "等待选择"}</span></header><div className="initialization-panel-body" data-editor-focus-scope>{item ? <Detail item={item} preview={workspacePreview} document={document} scenarioId={scenarioId} focusFactKey={requestedFactKey || undefined} onChange={onChange} onDeleteResourcePool={onDeleteResourcePool} onDeleteRootCollectionItem={onDeleteRootCollectionItem} /> : <EmptyPanel>请选择一个对象以配置初始化状态</EmptyPanel>}</div></section>
    </div>
    {issuesOpen && <InitializationIssueDialog issues={presentations} onClose={() => { setIssuesOpen(false); window.requestAnimationFrame(() => issueButtonRef.current?.focus()); }} onNavigate={() => setIssuesOpen(false)} />}
  </div>;
}
