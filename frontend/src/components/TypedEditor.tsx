import { useRef, useState } from "react";
import { metadataForKind, rootFieldRegistry, type FieldMetadata } from "../editor-registry";
import type { RootOwnerSelection } from "../editor-collections";
import type { MoveDirection } from "../editor-order";
import { objectByKindAndKey, sectionForKind, type DraftObject, type EntityKind, type IdentityCreationIntent, type JsonObject } from "../editor";
import { displayEnumValue, editorLabel, fieldLabel, platformEnumLabel, type PlatformEnumDomain } from "../ui";
import {
  AdvancedSection,
  BooleanControl,
  EnumSelect,
  IdentityValue,
  MultiValuePicker,
  NumberInput,
  NestedCard,
  ReferencePicker,
  ScalarListEditor,
  TextArea,
  TextInput,
} from "./editor/FormPrimitives";
import { referenceOptions } from "./editor/ReferencePicker";
import { IdentityCreationDialog, type IdentityCreationValues } from "./editor/IdentityCreationDialog";
import { editorLocatorHref } from "../editor-locator";
import type { InitializationPreview, ReferenceEdge } from "../types";
import { AuthoringActionButton, FieldActionRow } from "./editor/AuthoringActionButton";
import { InitializationReadonlyPreview } from "./editor/InitializationReadonlyPreview";
import { ReferenceUsageSection } from "./editor/ReferenceUsageSection";
import { ValueLabelList } from "./editor/ValueLabelEditor";
import {
  ActionAuthorityPolicyEditor,
  ActionEditor,
  AuthorityPolicyEditor,
  DerivedStateEditor,
  DoctrineEditor,
  GoalResolutionEditor,
  InitializationEditor,
  MasterDetailInitializationEditor,
  MasterDetailPlanningEditor,
  PlanningEditor,
  PublicReferenceEditor,
  RuleEditor,
  ResourceSourceHintEditor,
  ScenarioOverviewEditor,
  type NestedDeleteHandler,
} from "./TypedScenarioAuthoring";

type Props = {
  section: string;
  scenarioId?: string;
  value: unknown;
  document: JsonObject;
  onChange: (value: unknown) => void;
  path?: string;
  focusPath?: string | null;
  collectionSelection?: RootOwnerSelection | null;
  onCollectionChange?: (value: JsonObject) => void;
  onCollectionRemove?: () => void;
  onCollectionRemoveSelection?: (selection: import("../editor-collections").RootCollectionSelection) => void;
  onCollectionMove?: (direction: MoveDirection) => void;
  onInstructionChange?: (index: number, content: string) => void;
  onInstructionRemove?: (index: number) => void;
  onInstructionMove?: (index: number, direction: MoveDirection) => void;
  onQuickInputChange?: (index: number, content: string) => void;
  onQuickInputRemove?: (index: number) => void;
  onQuickInputMove?: (index: number, direction: MoveDirection) => void;
  onDeleteFact?: (nodeKey: string, factKey: string) => void;
  onDeleteNested?: NestedDeleteHandler;
  references?: ReferenceEdge[];
  initializationPreview?: InitializationPreview | null;
  initializationPreviewLoading?: boolean;
  initializationPreviewError?: unknown;
  onRetryInitializationPreview?: () => void;
};

function cloneObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? structuredClone(value) as JsonObject : {};
}

function nestedValue(value: JsonObject, path: string): unknown {
  return path.split(".").reduce<unknown>((current, part) => {
    if (!current || typeof current !== "object" || Array.isArray(current)) return undefined;
    return (current as JsonObject)[part];
  }, value);
}

function withNestedValue(value: JsonObject, path: string, next: unknown): JsonObject {
  const copy = cloneObject(value);
  const parts = path.split(".");
  let current = copy;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) current[part] = structuredClone(next);
    else {
      if (!current[part] || typeof current[part] !== "object" || Array.isArray(current[part])) current[part] = {};
      current = current[part] as JsonObject;
    }
  });
  return copy;
}

function AdvancedJsonField({ value, onChange, path, label }: { value: unknown; onChange: (value: unknown) => void; path: string; label: string }) {
  return <AdvancedSection value={value} onChange={onChange} path={path} label={label} />;
}

function platformDomainForField(path: string): PlatformEnumDomain | undefined {
  const domains: Record<string, PlatformEnumDomain> = {
    allowed_actor_capabilities: "capability",
    behavior: "action_behavior",
    capabilities: "capability",
    execution_mode: "execution_mode",
    locality: "locality",
    phase: "rule_phase",
    presentation_role: "presentation_role",
    ref_type: "public_reference_type",
    target_kind: "target_kind",
    target_semantic_reference_type: "action_target_reference",
    trigger: "rule_trigger",
    value_type: "value_type",
  };
  return domains[path];
}

function FieldRow({ metadata, value, document, path, onChange, scenarioId }: { metadata: FieldMetadata; value: JsonObject; document: JsonObject; path: string; onChange: (value: JsonObject) => void; scenarioId?: string }) {
  const current = nestedValue(value, metadata.path);
  const next = (updated: unknown) => onChange(withNestedValue(value, metadata.path, updated));
  const label = metadata.label ?? fieldLabel(metadata.path);
  const fieldPath = `${path}.${metadata.path}`;
  const conditionalActive = metadata.requiredness !== "VARIANT_REQUIRED" || (path.startsWith("rule.") && metadata.path === "action_key" && value.trigger === "ACTION");
  const required = conditionalActive && ["SCHEMA_REQUIRED", "VARIANT_REQUIRED", "REFERENCE_REQUIRED"].includes(metadata.requiredness ?? "");
  const missing = current === null || current === undefined || (typeof current === "string" && !current.trim()) || (Array.isArray(current) && metadata.minItems !== undefined && current.length < metadata.minItems);
  const referenceOptionsForField = metadata.type === "reference" && metadata.referenceDomain ? referenceOptions(document, metadata.referenceDomain) : [];
  const invalidReference = metadata.type === "reference" && typeof current === "string" && current.trim().length > 0 && !referenceOptionsForField.some((option) => option.key === current);
  const error = required && missing ? "此项为必填内容。" : invalidReference ? "请选择现有目标。" : undefined;
  const ownerKindByDomain: Record<string, EntityKind> = { node_type: "node_type", node: "node", region: "node", relation_type: "relation_type", fact: "node", resource: "resource", resource_pool: "resource", role: "role", actor: "actor", interaction: "interaction", action: "action", derived_state: "derived_state", relation: "relation" };
  const targetKey = typeof current === "string" ? current : "";
  const targetKind = metadata.referenceDomain ? ownerKindByDomain[metadata.referenceDomain] : undefined;
  const selectedReferenceKeys = metadata.type === "multi-reference" && Array.isArray(current)
    ? current.filter((item): item is string => typeof item === "string")
    : [];
  let targetHref: string | null = null;
  let ownerHref: string | null = null;
  if (scenarioId && metadata.referenceDomain && targetKind) {
    ownerHref = editorLocatorHref({ owner: "singleton", section: metadata.referenceDomain === "resource_pool" ? "initialization" : metadata.referenceDomain === "fact" || metadata.referenceDomain === "region" ? "world-entities" : metadata.referenceDomain === "node_type" ? "node-types" : sectionForKind(targetKind), fieldPath: null }, scenarioId);
    if (metadata.referenceDomain === "fact") {
      const [nodeKey, factKey] = targetKey.split(".");
      if (nodeKey && factKey) targetHref = editorLocatorHref({ owner: "entity", section: "world-entities", kind: "node", objectKey: nodeKey, fieldPath: `facts.${factKey}` }, scenarioId);
    } else if (metadata.referenceDomain === "resource_pool" && targetKey) {
      const query = new URLSearchParams({ domain: "resources", group: "resource-pools", item: `pool:${targetKey}` });
      targetHref = `/scenarios/${scenarioId}/edit/initialization?${query}`;
    } else if (targetKey) {
      const exact = metadata.referenceDomain === "region"
        ? objectByKindAndKey(document, "node", targetKey)
        : objectByKindAndKey(document, targetKind, targetKey);
      if (exact) targetHref = editorLocatorHref({ owner: "entity", section: metadata.referenceDomain === "region" ? "world-entities" : sectionForKind(targetKind), kind: targetKind, objectKey: targetKey, fieldPath: null }, scenarioId);
    }
  }
  const selectedReferenceLinks = selectedReferenceKeys.flatMap((key) => {
    if (!scenarioId || !metadata.referenceDomain || !targetKind) return [];
    let href: string | null = null;
    if (metadata.referenceDomain === "fact") {
      const [nodeKey, factKey] = key.split(".");
      if (nodeKey && factKey) href = editorLocatorHref({ owner: "entity", section: "world-entities", kind: "node", objectKey: nodeKey, fieldPath: `facts.${factKey}` }, scenarioId);
    } else if (metadata.referenceDomain === "resource_pool") {
      const query = new URLSearchParams({ domain: "resources", group: "resource-pools", item: `pool:${key}` });
      href = `/scenarios/${scenarioId}/edit/initialization?${query}`;
    } else {
      const exact = metadata.referenceDomain === "region"
        ? objectByKindAndKey(document, "node", key)
        : objectByKindAndKey(document, targetKind, key);
      if (exact) href = editorLocatorHref({ owner: "entity", section: metadata.referenceDomain === "region" ? "world-entities" : sectionForKind(targetKind), kind: targetKind, objectKey: key, fieldPath: null }, scenarioId);
    }
    const option = referenceOptions(document, metadata.referenceDomain).find((candidate) => candidate.key === key);
    return href ? [{ key, href, label: option?.name ?? key }] : [];
  });
  const ownerLabels: Record<string, string> = { node_type: "节点类型", node: "世界实体", region: "区域", relation_type: "关系类型", fact: "事实", resource: "资源定义", resource_pool: "资源池", role: "角色", actor: "参与者", interaction: "交互", action: "行动", derived_state: "派生状态", relation: "关系实例" };
  const selectedLabel = (key: string) => referenceOptions(document, metadata.referenceDomain ?? "node").find((option) => option.key === key)?.name ?? key;
  const headingAddon = metadata.navigation?.startsWith("FORWARD") && (targetHref || ownerHref)
    ? metadata.type === "multi-reference"
      ? <span className="field-owner-links">{selectedReferenceLinks.map((item) => <AuthoringActionButton intent="navigate" to={item.href} key={item.key}>前往{item.label}</AuthoringActionButton>)}<AuthoringActionButton intent="navigate" to={ownerHref!}>前往{ownerLabels[metadata.referenceDomain ?? ""] ?? "对象集合"}</AuthoringActionButton></span>
      : <AuthoringActionButton intent="navigate" to={targetHref ?? ownerHref!}>{targetHref ? `前往${selectedLabel(targetKey)}` : `前往${ownerLabels[metadata.referenceDomain ?? ""] ?? "对象集合"}`}</AuthoringActionButton>
    : undefined;
  if (metadata.type === "json") return <AdvancedJsonField value={current} onChange={next} path={fieldPath} label={label} />;
  if (metadata.type === "enum") return <EnumSelect value={current} onChange={next as (value: string) => void} path={fieldPath} label={label} choices={metadata.enum ?? []} enumDomain={platformDomainForField(metadata.path)} required={required} error={error} />;
  if (metadata.type === "multi-enum") return <MultiValuePicker value={current} onChange={next as (value: string[]) => void} path={fieldPath} label={label} required={required} minItems={metadata.minItems} error={error} options={(metadata.enum ?? []).map((item) => ({ key: item, name: platformEnumLabel(platformDomainForField(metadata.path), item) }))} />;
  const identityField = metadata.type === "text" && (metadata.path === "key" || metadata.path === "ref_key");
  if (identityField) {
    return <IdentityValue value={current} path={fieldPath} label={label} />;
  }
  if (metadata.type === "reference") return <ReferencePicker value={current} onChange={next as (value: string) => void} domain={metadata.referenceDomain!} document={document} path={fieldPath} label={label} required={required} error={error} headingAddon={headingAddon} />;
  if (metadata.type === "multi-reference") return <MultiValuePicker value={current} onChange={next as (value: string[]) => void} path={fieldPath} label={label} options={referenceOptions(document, metadata.referenceDomain!)} required={required} minItems={metadata.minItems} error={error} headingAddon={headingAddon} />;
  if (metadata.type === "boolean") return <BooleanControl value={current} onChange={next} path={fieldPath} label={label} required={required} error={error} />;
  if (metadata.type === "integer" || metadata.type === "number") return <NumberInput value={current} onChange={next} path={fieldPath} label={label} integer={metadata.type === "integer"} required={required} error={error} />;
  if (metadata.type === "textarea" || metadata.multiline) return <TextArea value={typeof current === "string" ? current : ""} onChange={next as (value: string) => void} path={fieldPath} label={label} required={required} error={error} />;
  const identityReadOnly = metadata.type === "text" && (metadata.path === "key" || metadata.path === "ref_key");
  return <TextInput value={typeof current === "string" || typeof current === "number" ? String(current) : ""} onChange={next as (value: string) => void} path={fieldPath} label={label} required={required} error={error} headingAddon={headingAddon} readOnly={identityReadOnly} />;
}

function RelationIdentitySummary({ entity, document, scenarioId }: { entity: DraftObject; document: JsonObject; scenarioId?: string }) {
  const { value } = entity;
  const sourceKey = typeof value.source_node_key === "string" ? value.source_node_key : "";
  const relationTypeKey = typeof value.relation_type_key === "string" ? value.relation_type_key : "";
  const targetKey = typeof value.target_node_key === "string" ? value.target_node_key : "";
  const compositeIdentity = [sourceKey, relationTypeKey, targetKey].join("__");
  const source = objectByKindAndKey(document, "node", sourceKey);
  const relationType = objectByKindAndKey(document, "relation_type", relationTypeKey);
  const target = objectByKindAndKey(document, "node", targetKey);
  const sourceName = source?.name || sourceKey || "未选择来源节点";
  const relationTypeName = relationType?.name || relationTypeKey || "未选择关系类型";
  const targetName = target?.name || targetKey || "未选择目标节点";
  const hrefFor = (kind: EntityKind, key: string, exists: boolean) => exists
    ? editorLocatorHref({ owner: "entity", section: sectionForKind(kind), kind, objectKey: key, fieldPath: null }, scenarioId ?? "")
    : editorLocatorHref({ owner: "singleton", section: sectionForKind(kind), fieldPath: null }, scenarioId ?? "");
  return <section className="relation-composite-identity">
    <IdentityValue value={`${sourceName} → ${relationTypeName} → ${targetName}`} path={`relation.${entity.key}`} label="关系身份" secondaryLabel="稳定复合键" secondaryValue={compositeIdentity} help="来源、关系类型和目标共同构成身份；创建后不能单独修改。" />
    {scenarioId && <FieldActionRow>
      <AuthoringActionButton intent="navigate" to={hrefFor("node", sourceKey, Boolean(source))}>{`前往来源节点${source ? `：${sourceName}` : ""}`}</AuthoringActionButton>
      <AuthoringActionButton intent="navigate" to={hrefFor("relation_type", relationTypeKey, Boolean(relationType))}>{`前往关系类型${relationType ? `：${relationTypeName}` : ""}`}</AuthoringActionButton>
      <AuthoringActionButton intent="navigate" to={hrefFor("node", targetKey, Boolean(target))}>{`前往目标节点${target ? `：${targetName}` : ""}`}</AuthoringActionButton>
    </FieldActionRow>}
  </section>;
}

function StringArrayEditor({ value, onChange, label, path }: { value: unknown; onChange: (value: string[]) => void; label: string; path: string }) {
  const items = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  return <ScalarListEditor value={items} onChange={(next) => onChange(next.filter((item): item is string => typeof item === "string"))} path={path} label={label} />;
}

function nodeTypeSemanticStatus(document: JsonObject, nodeTypeKey: string): "Generic" | "Region" | "Facility" | "Transport" {
  const metadata = document.metadata && typeof document.metadata === "object" && !Array.isArray(document.metadata) ? document.metadata as JsonObject : {};
  const locality = metadata.locality && typeof metadata.locality === "object" && !Array.isArray(metadata.locality) ? metadata.locality as JsonObject : {};
  const matches = [
    ["region_node_type_key", "Region"],
    ["facility_node_type_key", "Facility"],
    ["transport_node_type_key", "Transport"],
  ].filter(([field]) => locality[field] === nodeTypeKey).map(([, label]) => label as "Region" | "Facility" | "Transport");
  return matches.length === 1 ? matches[0] : "Generic";
}

function nodeTypeSemanticLabel(status: ReturnType<typeof nodeTypeSemanticStatus>): string {
  return { Generic: "通用节点", Region: "区域", Facility: "设施", Transport: "交通节点" }[status];
}

function FactEditor({ value, document, path, nodeEntity, references, initializationPreview, initializationPreviewLoading, initializationPreviewError, onRetryInitializationPreview, scenarioId, onChange, onRemove, expanded, onExpandedChange }: { value: JsonObject; document: JsonObject; path: string; nodeEntity?: DraftObject; references: ReferenceEdge[]; initializationPreview: InitializationPreview | null; initializationPreviewLoading: boolean; initializationPreviewError?: unknown; onRetryInitializationPreview?: () => void; scenarioId?: string; onChange: (value: JsonObject, intent?: IdentityCreationIntent) => void; onRemove?: () => void; expanded: boolean; onExpandedChange: (expanded: boolean) => void }) {
  const factType = typeof value.value_type === "string" ? value.value_type : "";
  const allowed = Array.isArray(value.allowed_values) ? value.allowed_values : [];
return <NestedCard title="事实" typeLabel="事实" identity={String(value.name ?? value.key ?? "未命名事实")} focusPath={nodeEntity && typeof value.key === "string" ? `node.${nodeEntity.key}.facts.${value.key}` : undefined} summary={<><code>{String(value.key ?? "未命名")}</code> · {displayEnumValue("value_type", factType)}</>} expanded={expanded} onExpandedChange={onExpandedChange} onRemove={onRemove} removeLabel="删除事实"><div className="typed-grid">
    {(["key", "name", "description"] as const).map((field) => <FieldRow key={field} metadata={{ path: field, type: field === "description" ? "textarea" : "text", ...(field === "name" ? { requiredness: "SCHEMA_REQUIRED" as const } : {}) }} value={value} document={document} path={path} onChange={onChange} />)}
    <FieldRow metadata={{ path: "value_type", type: "enum", enum: ["STRING", "ENUM", "INTEGER", "BOOLEAN"], requiredness: "SCHEMA_REQUIRED" }} value={value} document={document} path={path} onChange={onChange} />
    {nodeEntity && <InitializationReadonlyPreview entity={nodeEntity} fact={value} document={document} preview={initializationPreview} loading={initializationPreviewLoading} scenarioId={scenarioId ?? ""} previewError={initializationPreviewError} onRetry={onRetryInitializationPreview} />}
    {nodeEntity && scenarioId && typeof value.key === "string" && <ReferenceUsageSection references={references} target={{ object_kind: "node", object_key: nodeEntity.key, field_path: `facts.${value.key}` }} document={document} scenarioId={scenarioId} />}
    <FieldRow metadata={{ path: "presentation_role", type: "enum", enum: ["HEADER_PRIMARY", "HEADER_SECONDARY", "BODY_MAIN", "SUPPORTING", "REQUIREMENT_ONLY"] }} value={value} document={document} path={path} onChange={onChange} />
    <>{factType === "ENUM" && <ScalarListEditor value={value.allowed_values ?? []} onChange={(next) => onChange({ ...value, allowed_values: next })} path={`${path}.allowed_values`} label="Allowed values" required={factType === "ENUM"} />}</>
    <ValueLabelList value={value.value_labels} valueType={factType} allowedValues={allowed} path={`${path}.value_labels`} onChange={(next, intent) => onChange({ ...value, value_labels: next }, intent)} />
    <section className="nested-list fact-goal-metadata"><h4>{editorLabel("Goal metadata")}</h4><BooleanControl value={value.goal_addressable ?? false} onChange={(next) => onChange({ ...value, goal_addressable: next })} path={`${path}.goal_addressable`} label="Goal addressable" /><StringArrayEditor value={value.goal_aliases ?? []} onChange={(next) => onChange({ ...value, goal_aliases: next })} path={`${path}.goal_aliases`} label="Goal aliases" /><StringArrayEditor value={value.goal_examples ?? []} onChange={(next) => onChange({ ...value, goal_examples: next })} path={`${path}.goal_examples`} label="Goal examples" /><ScalarListEditor value={value.goal_target_values ?? []} onChange={(next) => onChange({ ...value, goal_target_values: next })} path={`${path}.goal_target_values`} label="Goal target values" /></section>
  </div></NestedCard>;
}

function FactList({ value, document, path, onChange, onDeleteFact, nodeKey, references, initializationPreview, initializationPreviewLoading, initializationPreviewError, onRetryInitializationPreview, scenarioId }: { value: unknown; document: JsonObject; path: string; onChange: (value: unknown, intent?: IdentityCreationIntent) => void; onDeleteFact?: (nodeKey: string, factKey: string) => void; nodeKey: string; references: ReferenceEdge[]; initializationPreview: InitializationPreview | null; initializationPreviewLoading: boolean; initializationPreviewError?: unknown; onRetryInitializationPreview?: () => void; scenarioId?: string }) {
  const facts = Array.isArray(value) ? value : [];
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());
  const [creationOpen, setCreationOpen] = useState(false);
  const fallbackIds = useRef(new WeakMap<object, string>());
  const nextFallbackId = useRef(0);
  const factKeyAt = (index: number) => {
    const fact = facts[index];
    if (!fact || typeof fact !== "object") return "invalid-fact";
    const key = (fact as JsonObject).key;
    if (typeof key === "string" && key.trim()) return `key:${key}`;
    const existing = fallbackIds.current.get(fact as object);
    if (existing) return existing;
    const generated = `legacy-fact-${++nextFallbackId.current}`;
    fallbackIds.current.set(fact as object, generated);
    return generated;
  };
  const createFact = (values: IdentityCreationValues): string | null => {
    const key = values.key?.trim() ?? "";
    if (!/^[a-z][a-z0-9_]{0,79}$/.test(key)) return "事实键必须是小写稳定键。";
    if (facts.some((fact) => fact && typeof fact === "object" && !Array.isArray(fact) && (fact as JsonObject).key === key)) return "当前节点已经存在相同事实键。";
    setExpandedKeys((current) => new Set([...current, `key:${key}`]));
    onChange([...facts, { key, name: "" }], "identity-create");
    setCreationOpen(false);
    window.requestAnimationFrame(() => {
      const target = window.document.getElementById(`fact-card-${nodeKey}-${key}`);
      if (target && typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "center" });
    });
    return null;
  };
  const keys = facts.map((_, index) => factKeyAt(index));
  const nodeEntity = objectByKindAndKey(document, "node", nodeKey);
  return <><section className="nested-list" data-focus-path={path}><div className="typed-array-heading"><h4>{editorLabel("Facts")}</h4><div className="button-row"><button type="button" className="small" onClick={() => setExpandedKeys(new Set(keys))}>全部展开</button><button type="button" className="small" onClick={() => setExpandedKeys(new Set())}>全部折叠</button><button type="button" className="small" onClick={() => setCreationOpen(true)}>＋ {editorLabel("Add fact")}</button></div></div>{facts.map((fact, index) => fact && typeof fact === "object" && !Array.isArray(fact) ? <div id={`fact-card-${nodeKey}-${factKeyAt(index)}`} key={factKeyAt(index)}><FactEditor value={fact as JsonObject} document={document} path={`${path}.${index}`} nodeEntity={nodeEntity ?? undefined} references={references} initializationPreview={initializationPreview} initializationPreviewLoading={initializationPreviewLoading} initializationPreviewError={initializationPreviewError} onRetryInitializationPreview={onRetryInitializationPreview} scenarioId={scenarioId} expanded={expandedKeys.has(factKeyAt(index))} onExpandedChange={(expanded) => setExpandedKeys((current) => { const next = new Set(current); if (expanded) next.add(factKeyAt(index)); else next.delete(factKeyAt(index)); return next; })} onChange={(next) => onChange(facts.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={typeof (fact as JsonObject).key === "string" && onDeleteFact ? () => onDeleteFact(nodeKey, String((fact as JsonObject).key)) : undefined} /></div> : null)}</section>{creationOpen && <IdentityCreationDialog title="创建事实" fields={[{ key: "key", label: "事实键", required: true }]} onCancel={() => setCreationOpen(false)} onCreate={createFact} />}</>;
}

function EntityEditor({ entity, document, onChange, focusPath, onDeleteFact, onDeleteNested, scenarioId, references = [], initializationPreview = null, initializationPreviewLoading = false, initializationPreviewError, onRetryInitializationPreview }: { entity: DraftObject; document: JsonObject; initializationHref: string; onChange: (value: JsonObject, intent?: IdentityCreationIntent) => void; focusPath?: string | null; onDeleteFact?: (nodeKey: string, factKey: string) => void; onDeleteNested?: NestedDeleteHandler; scenarioId?: string; references?: ReferenceEdge[]; initializationPreview?: InitializationPreview | null; initializationPreviewLoading?: boolean; initializationPreviewError?: unknown; onRetryInitializationPreview?: () => void }) {
  const metadata = metadataForKind(entity.kind);
  const value = entity.value;
  const selectedNodeTypeKey = typeof value.node_type_key === "string" ? value.node_type_key : "";
  const selectedNodeType = referenceOptions(document, "node_type").find((option) => option.key === selectedNodeTypeKey);
  const nodeTypeHref = scenarioId && selectedNodeType
    ? `/scenarios/${scenarioId}/edit/node-types/${encodeURIComponent(selectedNodeTypeKey)}`
    : scenarioId ? `/scenarios/${scenarioId}/edit/node-types` : "../node-types";
  if (entity.kind === "action") return <><ActionEditor entity={entity} document={document} onChange={onChange} onDeleteNested={onDeleteNested} focusPath={focusPath} references={references} scenarioId={scenarioId} /><ActionAuthorityPolicyEditor value={value.authority_policy ?? {}} path={`${entity.kind}.${entity.key}.authority_policy`} document={document} onChange={(next, intent) => onChange({ ...value, authority_policy: next as JsonObject }, intent)} onDeleteNested={onDeleteNested} parentKey={entity.key} scenarioId={scenarioId} focusPath={focusPath} /></>;
  if (entity.kind === "rule") return <RuleEditor entity={entity} document={document} onChange={onChange} />;
  if (entity.kind === "derived_state") return <DerivedStateEditor entity={entity} document={document} onChange={onChange} focusPath={focusPath} />;
  if (entity.kind === "public_reference") return <PublicReferenceEditor entity={entity} document={document} scenarioId={scenarioId} />;
  const relationIdentityPaths = new Set(["key", "source_node_key", "relation_type_key", "target_node_key"]);
  const visibleFields = entity.kind === "relation" ? metadata.fields.filter((field) => !relationIdentityPaths.has(field.path)) : metadata.fields;
  return <div className="typed-entity-editor"><div className="typed-grid">
    {entity.kind === "relation" && <RelationIdentitySummary entity={entity} document={document} scenarioId={scenarioId} />}
    {visibleFields.map((field) => entity.kind === "node" && field.path === "node_type_key"
      ? <div className="node-type-required-field" key={field.path}><ReferencePicker value={value.node_type_key} onChange={(next) => onChange({ ...value, node_type_key: next })} domain="node_type" document={document} path={`${entity.kind}.${entity.key}.node_type_key`} label="节点类型 *" error={value.node_type_key ? undefined : "请选择节点类型。"} headingAddon={<AuthoringActionButton intent="navigate" to={nodeTypeHref}>{selectedNodeType ? `前往${selectedNodeType.name}` : "前往节点类型"}</AuthoringActionButton>} /></div>
      : field.type === "text" && Array.isArray(nestedValue(value, field.path))
      ? <StringArrayEditor key={field.path} value={nestedValue(value, field.path)} label={fieldLabel(field.path)} path={`${entity.kind}.${entity.key}.${field.path}`} onChange={(next) => onChange(withNestedValue(value, field.path, next))} />
      : <FieldRow key={field.path} metadata={field} value={value} document={document} path={`${entity.kind}.${entity.key}`} onChange={onChange} scenarioId={scenarioId} />)}
    {entity.kind === "node_type" && <div className="form-field node-type-semantic-field"><div className="form-field-heading"><span className="typed-field-label">空间角色</span></div><div className="readonly-field"><strong>{nodeTypeSemanticLabel(nodeTypeSemanticStatus(document, entity.key))}</strong></div><small className="typed-help">由概览中的空间语义配置决定。</small><FieldActionRow><AuthoringActionButton intent="navigate" to={scenarioId ? `/scenarios/${scenarioId}/edit/overview?focus_path=locality` : "../overview?focus_path=locality"}>配置空间语义</AuthoringActionButton></FieldActionRow></div>}
   </div>
    {entity.kind === "resource" && <ResourceSourceHintEditor value={value.source_hint} document={document} path={`${entity.kind}.${entity.key}.source_hint`} onChange={(sourceHint) => {
      const next = { ...value };
      if (sourceHint) next.source_hint = sourceHint;
      else delete next.source_hint;
      onChange(next);
    }} />}

{["node", "actor", "relation", "resource"].includes(entity.kind) && <InitializationReadonlyPreview entity={entity} document={document} preview={initializationPreview} loading={initializationPreviewLoading} scenarioId={scenarioId ?? ""} previewError={initializationPreviewError} onRetry={onRetryInitializationPreview} />}
    {entity.kind === "node" && <FactList value={value.facts} document={document} path={`${entity.kind}.${entity.key}.facts`} onChange={(next, intent) => onChange({ ...value, facts: next as JsonObject[] }, intent)} onDeleteFact={onDeleteFact} nodeKey={entity.key} references={references} initializationPreview={initializationPreview} initializationPreviewLoading={initializationPreviewLoading} initializationPreviewError={initializationPreviewError} onRetryInitializationPreview={onRetryInitializationPreview} scenarioId={scenarioId} />}
    {entity.kind === "actor" && <><DoctrineEditor value={value.doctrine} path={`${entity.kind}.${entity.key}.doctrine`} parentKey={entity.key} onDeleteNested={onDeleteNested} onChange={(next, intent) => onChange({ ...value, doctrine: next }, intent)} /><AuthorityPolicyEditor value={value.authority_policy ?? {}} path={`${entity.kind}.${entity.key}.authority_policy`} document={document} parentKind="actor" parentKey={entity.key} allowedActionKeys={Array.isArray(value.allowed_action_keys) ? value.allowed_action_keys.filter((item): item is string => typeof item === "string") : []} scenarioId={scenarioId} onDeleteNested={onDeleteNested} onChange={(next, intent) => onChange({ ...value, authority_policy: next as JsonObject }, intent)} focusPath={focusPath} /></>}
    <p className="typed-help">已识别字段由结构化控件维护；其他数据会保留在工作副本中。</p>
  </div>;
}

function RootEditor({ rootKey, value, document, onChange, scenarioId }: { rootKey: string; value: unknown; document: JsonObject; onChange: (value: unknown) => void; scenarioId?: string }) {
  const object = cloneObject(value);
  const fields = rootFieldRegistry[rootKey] ?? [];
  return <div className="typed-root-editor"><div className="typed-grid">{fields.map((metadata) => <FieldRow key={metadata.path} metadata={metadata} value={object} document={document} path={rootKey} onChange={onChange as (value: JsonObject) => void} scenarioId={scenarioId} />)}</div>{rootKey === "initialization" && <p className="typed-help">资源定义在世界模型中维护；资源初始状态、资源池和区域资源知识属于初始化数据，当前以高级结构保留。</p>}{rootKey === "planning" && <p className="typed-help">这些是提供给规划智能体的场景级软指引。不会覆盖行动约束、知识边界或后端验证规则。</p>}</div>;
}

export function TypedEditor({ section, scenarioId, value, document, onChange, path = section, focusPath, collectionSelection = null, onCollectionChange, onCollectionRemove, onCollectionMove, onInstructionChange, onInstructionRemove, onInstructionMove, onQuickInputChange, onQuickInputRemove, onQuickInputMove, onDeleteNested }: Props) {
  if (section === "overview" || section === "initialization" || section === "goal-resolution" || section === "planning" || section === "planning-instructions" || section === "planning-recovery") {
    const rootKey = section === "overview" ? "metadata" : section === "goal-resolution" ? "goal_resolution" : section === "planning-instructions" || section === "planning-recovery" ? "planning" : section;
    if (rootKey === "metadata" && value && typeof value === "object" && !Array.isArray(value)) return <ScenarioOverviewEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "initialization" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailInitializationEditor value={value as JsonObject} document={document} selection={collectionSelection} onChange={onChange as (value: JsonObject) => void} onCollectionRemove={onCollectionRemove} onCollectionMove={onCollectionMove} /> : <InitializationEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "planning" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailPlanningEditor page="instructions" value={value as JsonObject} document={document} selection={collectionSelection} onChange={onChange as (value: JsonObject) => void} onCollectionChange={onCollectionChange} onCollectionRemove={onCollectionRemove} onCollectionMove={onCollectionMove} onInstructionChange={onInstructionChange} onInstructionRemove={onInstructionRemove} onInstructionMove={onInstructionMove} /> : <PlanningEditor value={value as JsonObject} />;
    if (rootKey === "goal_resolution" && value && typeof value === "object" && !Array.isArray(value)) return <GoalResolutionEditor value={value as JsonObject} selection={collectionSelection} onQuickInputChange={onQuickInputChange} onQuickInputRemove={onQuickInputRemove} onQuickInputMove={onQuickInputMove} />;
    return <RootEditor rootKey={rootKey} value={value} document={document} onChange={onChange} scenarioId={scenarioId} />;
  }
  if (value && typeof value === "object" && !Array.isArray(value) && "kind" in value && typeof (value as JsonObject).kind === "string") {
    return <EntityEditor entity={value as unknown as DraftObject} document={document} initializationHref="../initialization" onChange={onChange as (value: JsonObject) => void} focusPath={focusPath} onDeleteNested={onDeleteNested} scenarioId={scenarioId} />;
  }
  return <AdvancedJsonField value={value} onChange={onChange} path={path} label="Advanced structure" />;
}

export function TypedEntityEditor({ entity, document, initializationHref = "../initialization", onChange, focusPath, onDeleteFact, onDeleteNested, scenarioId, references = [], initializationPreview = null, initializationPreviewLoading = false, initializationPreviewError, onRetryInitializationPreview }: { entity: DraftObject; document: JsonObject; initializationHref?: string; onChange: (value: JsonObject, intent?: IdentityCreationIntent) => void; focusPath?: string | null; onDeleteFact?: (nodeKey: string, factKey: string) => void; onDeleteNested?: NestedDeleteHandler; scenarioId?: string; references?: ReferenceEdge[]; initializationPreview?: InitializationPreview | null; initializationPreviewLoading?: boolean; initializationPreviewError?: unknown; onRetryInitializationPreview?: () => void }) {
  return <EntityEditor entity={entity} document={document} initializationHref={initializationHref} onChange={onChange} focusPath={focusPath} onDeleteFact={onDeleteFact} onDeleteNested={onDeleteNested} scenarioId={scenarioId} references={references} initializationPreview={initializationPreview} initializationPreviewLoading={initializationPreviewLoading} initializationPreviewError={initializationPreviewError} onRetryInitializationPreview={onRetryInitializationPreview} />;
}
