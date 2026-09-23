import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { metadataForKind, rootFieldRegistry, type FieldMetadata } from "../editor-registry";
import type { RootOwnerSelection } from "../editor-collections";
import type { MoveDirection } from "../editor-order";
import type { DraftObject, JsonObject } from "../editor";
import { displayEnumValue, editorLabel, fieldLabel } from "../ui";
import {
  AdvancedSection,
  BooleanControl,
  EnumSelect,
  MultiValuePicker,
  NumberInput,
  NestedCard,
  ReferencePicker,
  ScalarListEditor,
  TextArea,
  TextInput,
} from "./editor/FormPrimitives";
import { referenceOptions } from "./editor/ReferencePicker";
import { fieldId } from "./editor/FormUtils";
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
  MasterDetailPublicKnowledgeEditor,
  PlanningEditor,
  PublicKnowledgeEditor,
  PublicReferenceEditor,
  RuleEditor,
  ScenarioOverviewEditor,
  type NestedDeleteHandler,
} from "./TypedScenarioAuthoring";

type Props = {
  section: string;
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
  onDeleteFact?: (nodeKey: string, factKey: string) => void;
  onDeleteNested?: NestedDeleteHandler;
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

function FieldRow({ metadata, value, document, path, onChange }: { metadata: FieldMetadata; value: JsonObject; document: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const current = nestedValue(value, metadata.path);
  const next = (updated: unknown) => onChange(withNestedValue(value, metadata.path, updated));
  const label = metadata.label ?? fieldLabel(metadata.path);
  const fieldPath = `${path}.${metadata.path}`;
  if (metadata.type === "json") return <AdvancedJsonField value={current} onChange={next} path={fieldPath} label={label} />;
  if (metadata.type === "enum") return <EnumSelect value={current} onChange={next as (value: string) => void} path={fieldPath} label={label} choices={metadata.enum ?? []} />;
  if (metadata.type === "multi-enum") return <MultiValuePicker value={current} onChange={next as (value: string[]) => void} path={fieldPath} label={label} options={(metadata.enum ?? []).map((item) => ({ key: item, name: displayEnumValue("generic", item) }))} />;
  if (metadata.type === "reference") return <ReferencePicker value={current} onChange={next as (value: string) => void} domain={metadata.referenceDomain!} document={document} path={fieldPath} label={label} />;
  if (metadata.type === "multi-reference") return <MultiValuePicker value={current} onChange={next as (value: string[]) => void} path={fieldPath} label={label} options={referenceOptions(document, metadata.referenceDomain!)} />;
  if (metadata.type === "boolean") return <BooleanControl value={current} onChange={next} path={fieldPath} label={label} />;
  if (metadata.type === "integer" || metadata.type === "number") return <NumberInput value={current} onChange={next} path={fieldPath} label={label} integer={metadata.type === "integer"} />;
  if (metadata.type === "textarea" || metadata.multiline) return <TextArea value={typeof current === "string" ? current : ""} onChange={next as (value: string) => void} path={fieldPath} label={label} />;
  const identityReadOnly = metadata.type === "text" && (metadata.path === "key" || metadata.path === "ref_key");
  return <TextInput value={typeof current === "string" || typeof current === "number" ? String(current) : ""} onChange={next as (value: string) => void} path={fieldPath} label={label} readOnly={identityReadOnly} />;
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

function FactEditor({ value, document, path, initializationHref, onChange, onRemove, expanded, onExpandedChange }: { value: JsonObject; document: JsonObject; path: string; initializationHref: string; onChange: (value: JsonObject) => void; onRemove?: () => void; expanded: boolean; onExpandedChange: (expanded: boolean) => void }) {
  const factType = typeof value.value_type === "string" ? value.value_type : "BOOLEAN";
  const allowed = Array.isArray(value.allowed_values) ? value.allowed_values : [];
return <NestedCard title="事实" typeLabel="事实" identity={String(value.name ?? value.key ?? "未命名事实")} summary={<><code>{String(value.key ?? "未命名")}</code> · {displayEnumValue("value_type", factType)}</>} expanded={expanded} onExpandedChange={onExpandedChange} onRemove={onRemove} removeLabel="删除事实"><div className="typed-grid">
    {(["key", "name", "description"] as const).map((field) => <FieldRow key={field} metadata={{ path: field, type: field === "description" ? "textarea" : "text" }} value={value} document={document} path={path} onChange={onChange} />)}
    <FieldRow metadata={{ path: "value_type", type: "enum", enum: ["STRING", "ENUM", "INTEGER", "BOOLEAN"] }} value={value} document={document} path={path} onChange={onChange} />
<div className="bootstrap-handoff form-field-full"><span>开局真实状态 / 玩家知识</span><strong>{String(value.initial_value)} · {displayEnumValue("visibility", String(value.initial_visibility ?? ""))}</strong><Link to={`${initializationHref}&fact=${encodeURIComponent(String(value.key ?? ""))}`}>前往当前事实的初始化配置</Link></div>
    <FieldRow metadata={{ path: "presentation_role", type: "enum", enum: ["HEADER_PRIMARY", "HEADER_SECONDARY", "BODY_MAIN", "SUPPORTING", "REQUIREMENT_ONLY"] }} value={value} document={document} path={path} onChange={onChange} />
    <>{factType === "ENUM" && <ScalarListEditor value={value.allowed_values ?? []} onChange={(next) => onChange({ ...value, allowed_values: next })} path={`${path}.allowed_values`} label="Allowed values" />}<AdvancedJsonField value={value.allowed_values ?? []} onChange={(next) => onChange({ ...value, allowed_values: next })} path={`${path}.allowed_values`} label="Allowed values (Advanced JSON)" /></>
    <ValueLabelList value={value.value_labels} valueType={factType} allowedValues={allowed} path={`${path}.value_labels`} onChange={(next) => onChange({ ...value, value_labels: next })} />
    <section className="nested-list fact-goal-metadata"><h4>{editorLabel("Goal metadata")}</h4><BooleanControl value={value.goal_addressable ?? false} onChange={(next) => onChange({ ...value, goal_addressable: next })} path={`${path}.goal_addressable`} label="Goal addressable" /><StringArrayEditor value={value.goal_aliases ?? []} onChange={(next) => onChange({ ...value, goal_aliases: next })} path={`${path}.goal_aliases`} label="Goal aliases" /><StringArrayEditor value={value.goal_examples ?? []} onChange={(next) => onChange({ ...value, goal_examples: next })} path={`${path}.goal_examples`} label="Goal examples" /><ScalarListEditor value={value.goal_target_values ?? []} onChange={(next) => onChange({ ...value, goal_target_values: next })} path={`${path}.goal_target_values`} label="Goal target values" /><AdvancedJsonField value={{ goal_addressable: value.goal_addressable ?? false, goal_aliases: value.goal_aliases ?? [], goal_examples: value.goal_examples ?? [], goal_target_values: value.goal_target_values ?? [] }} onChange={(next) => onChange({ ...value, ...(cloneObject(next)) })} path={`${path}.goal_metadata`} label="Goal metadata" /></section>
  </div></NestedCard>;
}

function FactList({ value, document, path, initializationHref, onChange, onDeleteFact, nodeKey, focusPath }: { value: unknown; document: JsonObject; path: string; initializationHref: string; onChange: (value: unknown) => void; onDeleteFact?: (nodeKey: string, factKey: string) => void; nodeKey: string; focusPath?: string | null }) {
  const facts = Array.isArray(value) ? value : [];
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());
  const factKeyAt = (index: number) => String((facts[index] as JsonObject | undefined)?.key ?? `index-${index}`);
  const focusedIndex = facts.findIndex((fact, index) => {
    if (!focusPath || !fact || typeof fact !== "object" || Array.isArray(fact)) return false;
    const key = String((fact as JsonObject).key ?? "");
    return focusPath.startsWith(`${path}.${index}`) || Boolean(key && focusPath.includes(`facts.${key}`));
  });
  const focusedKey = focusedIndex >= 0 ? factKeyAt(focusedIndex) : null;
  useEffect(() => {
    if (!focusedKey) return;
    setExpandedKeys((current) => current.has(focusedKey) ? current : new Set([...current, focusedKey]));
  }, [focusedKey]);
  useEffect(() => {
    if (!focusedKey || !expandedKeys.has(focusedKey)) return;
    const frame = window.requestAnimationFrame(() => {
      const exact = focusPath ? window.document.getElementById(fieldId(focusPath)) : null;
      const fallback = window.document.getElementById(`fact-card-${nodeKey}-${focusedKey}`);
      const target = exact ?? fallback;
      if (target && typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "center" });
      if (exact instanceof HTMLElement) exact.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [expandedKeys, focusPath, focusedKey, nodeKey]);
  const addFact = () => {
    const keys = new Set(facts.flatMap((fact) => fact && typeof fact === "object" && !Array.isArray(fact) && typeof (fact as JsonObject).key === "string" ? [String((fact as JsonObject).key)] : []));
    let key = "new_fact";
    for (let index = 2; keys.has(key); index += 1) key = `new_fact_${index}`;
    setExpandedKeys((current) => new Set([...current, key]));
    onChange([...facts, { key, name: "新事实", description: "", value_type: "BOOLEAN", initial_value: false, initial_visibility: "KNOWN", allowed_values: [] }]);
    window.requestAnimationFrame(() => {
      const target = window.document.getElementById(`fact-card-${nodeKey}-${key}`);
      if (target && typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "center" });
    });
  };
  const keys = facts.map((_, index) => factKeyAt(index));
  return <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Facts")}</h4><div className="button-row"><button type="button" className="small" onClick={() => setExpandedKeys(new Set(keys))}>全部展开</button><button type="button" className="small" onClick={() => setExpandedKeys(new Set())}>全部折叠</button><button type="button" className="small" onClick={addFact}>＋ {editorLabel("Add fact")}</button></div></div>{facts.map((fact, index) => fact && typeof fact === "object" && !Array.isArray(fact) ? <div id={`fact-card-${nodeKey}-${factKeyAt(index)}`} key={factKeyAt(index)}><FactEditor value={fact as JsonObject} document={document} path={`${path}.${index}`} initializationHref={initializationHref} expanded={expandedKeys.has(factKeyAt(index))} onExpandedChange={(expanded) => setExpandedKeys((current) => { const next = new Set(current); if (expanded) next.add(factKeyAt(index)); else next.delete(factKeyAt(index)); return next; })} onChange={(next) => onChange(facts.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={typeof (fact as JsonObject).key === "string" && onDeleteFact ? () => onDeleteFact(nodeKey, String((fact as JsonObject).key)) : undefined} /></div> : null)}</section>;
}

function EntityEditor({ entity, document, initializationHref, onChange, focusPath, onDeleteFact, onDeleteNested, scenarioId }: { entity: DraftObject; document: JsonObject; initializationHref: string; onChange: (value: JsonObject) => void; focusPath?: string | null; onDeleteFact?: (nodeKey: string, factKey: string) => void; onDeleteNested?: NestedDeleteHandler; scenarioId?: string }) {
  const metadata = metadataForKind(entity.kind);
  const value = entity.value;
  useEffect(() => {
    if (!focusPath) return;
    const element = window.document.getElementById(fieldId(focusPath));
    if (element && typeof element.scrollIntoView === "function") element.scrollIntoView({ block: "center" });
    if (element instanceof HTMLElement && typeof element.focus === "function") element.focus();
  }, [focusPath]);
  if (entity.kind === "action") return <><ActionEditor entity={entity} document={document} onChange={onChange} onDeleteNested={onDeleteNested} /><ActionAuthorityPolicyEditor value={value.authority_policy ?? {}} path={`${entity.kind}.${entity.key}.authority_policy`} parameterKeys={Array.isArray(value.parameters) ? value.parameters.flatMap((item) => item && typeof item === "object" && !Array.isArray(item) && typeof (item as JsonObject).key === "string" ? [String((item as JsonObject).key)] : []) : []} onChange={(next) => onChange({ ...value, authority_policy: next })} onDeleteNested={onDeleteNested} parentKey={entity.key} /></>;
  if (entity.kind === "rule") return <RuleEditor entity={entity} document={document} onChange={onChange} />;
  if (entity.kind === "derived_state") return <DerivedStateEditor entity={entity} document={document} onChange={onChange} />;
  if (entity.kind === "public_reference") return <PublicReferenceEditor entity={entity} document={document} onChange={onChange} />;
  return <div className="typed-entity-editor"><div className="typed-grid">
    {metadata.fields.map((field) => entity.kind === "node" && field.path === "node_type_key"
      ? <div className="node-type-required-field" key={field.path}><ReferencePicker value={value.node_type_key} onChange={(next) => onChange({ ...value, node_type_key: next })} domain="node_type" document={document} path={`${entity.kind}.${entity.key}.node_type_key`} label="节点类型 *" error={value.node_type_key ? undefined : "请选择节点类型。"} /><Link className="node-type-owner-link" to={scenarioId ? `/scenarios/${scenarioId}/edit/node-types` : "../node-types"}>前往节点类型</Link></div>
      : field.type === "text" && Array.isArray(nestedValue(value, field.path))
      ? <StringArrayEditor key={field.path} value={nestedValue(value, field.path)} label={fieldLabel(field.path)} path={`${entity.kind}.${entity.key}.${field.path}`} onChange={(next) => onChange(withNestedValue(value, field.path, next))} />
      : <FieldRow key={field.path} metadata={field} value={value} document={document} path={`${entity.kind}.${entity.key}`} onChange={onChange} />)}
    {entity.kind === "node_type" && <div className="form-field node-type-semantic-field"><div className="form-field-heading"><span className="typed-field-label">空间角色</span></div><div className="readonly-field"><strong>{nodeTypeSemanticLabel(nodeTypeSemanticStatus(document, entity.key))}</strong><Link to={scenarioId ? `/scenarios/${scenarioId}/edit/overview?focus_path=locality` : "../overview?focus_path=locality"}>配置空间语义</Link></div><small className="typed-help">由概览中的空间语义配置决定。</small></div>}
   </div>
{["node", "actor", "relation", "resource"].includes(entity.kind) && <div className="bootstrap-handoff"><span>开局配置由初始化工作区统一编辑</span><strong>{entity.kind === "node" ? `${displayEnumValue("access", String(value.initial_access ?? ""))} · ${displayEnumValue("visibility", String(value.initial_visibility ?? ""))}` : entity.kind === "actor" ? `${String(value.initial_node_key)} · ${displayEnumValue("reachability", String(value.command_reachability ?? "ONLINE"))}` : entity.kind === "relation" ? displayEnumValue("visibility", String(value.initial_visibility ?? "VISIBLE")) : `兼容回退 · ${String(value.initial_value)}`}</strong><Link to={`${initializationHref}?domain=${entity.kind === "node" ? "nodes" : entity.kind === "actor" ? "actors" : entity.kind === "relation" ? "relations" : "resources"}${entity.kind === "resource" ? "" : `&item=${encodeURIComponent(`${entity.kind}:${entity.key}`)}`}`}>{entity.kind === "node" ? "前往初始化" : "前往初始化配置"}</Link></div>}
    {entity.kind === "node" && <FactList value={value.facts} document={document} path={`${entity.kind}.${entity.key}.facts`} initializationHref={`${initializationHref}?domain=nodes&item=${encodeURIComponent(`node:${entity.key}`)}`} onChange={(next) => onChange({ ...value, facts: next })} onDeleteFact={onDeleteFact} nodeKey={entity.key} focusPath={focusPath} />}
    {entity.kind === "actor" && <><DoctrineEditor value={value.doctrine} path={`${entity.kind}.${entity.key}.doctrine`} parentKey={entity.key} onDeleteNested={onDeleteNested} onChange={(next) => onChange({ ...value, doctrine: next })} /><AuthorityPolicyEditor value={value.authority_policy ?? {}} path={`${entity.kind}.${entity.key}.authority_policy`} parentKind="actor" parentKey={entity.key} onDeleteNested={onDeleteNested} onChange={(next) => onChange({ ...value, authority_policy: next })} /></>}
    {metadata.nested?.filter((nested) => !(entity.kind === "actor" && (nested === "doctrine" || nested === "authority_policy"))).map((nested) => <AdvancedJsonField key={nested} value={value[nested]} onChange={(next) => onChange({ ...value, [nested]: next })} path={`${entity.kind}.${entity.key}.${nested}`} label={fieldLabel(nested)} />)}
    <p className="typed-help">未在基础表单中展开的合法字段会保留在原始草稿中；复杂结构当前标记为高级结构，不会静默删除。</p>
  </div>;
}

function RootEditor({ rootKey, value, document, onChange }: { rootKey: string; value: unknown; document: JsonObject; onChange: (value: unknown) => void }) {
  const object = cloneObject(value);
  const fields = rootFieldRegistry[rootKey] ?? [];
  return <div className="typed-root-editor"><div className="typed-grid">{fields.map((metadata) => <FieldRow key={metadata.path} metadata={metadata} value={object} document={document} path={rootKey} onChange={onChange as (value: JsonObject) => void} />)}</div>{rootKey === "initialization" && <p className="typed-help">资源定义在世界模型中维护；资源初始状态、资源池和区域资源知识属于初始化数据，当前以高级结构保留。</p>}{rootKey === "planning" && <p className="typed-help">全局规划指引已提供结构化文本数组入口；失败恢复策略会在规则与规划表单中展开。</p>}</div>;
}

export function TypedEditor({ section, value, document, onChange, path = section, focusPath, collectionSelection = null, onCollectionChange, onCollectionRemove, onCollectionMove, onInstructionChange, onInstructionRemove, onInstructionMove, onDeleteNested }: Props) {
  if (section === "overview" || section === "initialization" || section === "goal-resolution" || section === "planning" || section === "planning-instructions" || section === "planning-recovery" || section === "public-knowledge") {
    const rootKey = section === "overview" ? "metadata" : section === "goal-resolution" ? "goal_resolution" : section === "public-knowledge" ? "public_knowledge" : section === "planning-instructions" || section === "planning-recovery" ? "planning" : section;
    if (rootKey === "metadata" && value && typeof value === "object" && !Array.isArray(value)) return <ScenarioOverviewEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "initialization" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailInitializationEditor value={value as JsonObject} document={document} selection={collectionSelection} onChange={onChange as (value: JsonObject) => void} onCollectionChange={onCollectionChange} onCollectionRemove={onCollectionRemove} onCollectionMove={onCollectionMove} /> : <InitializationEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "planning" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailPlanningEditor page={section === "planning-recovery" ? "recovery" : "instructions"} value={value as JsonObject} document={document} selection={collectionSelection} onChange={onChange as (value: JsonObject) => void} onCollectionChange={onCollectionChange} onCollectionRemove={onCollectionRemove} onCollectionMove={onCollectionMove} onInstructionChange={onInstructionChange} onInstructionRemove={onInstructionRemove} onInstructionMove={onInstructionMove} /> : <PlanningEditor value={value as JsonObject} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "goal_resolution" && value && typeof value === "object" && !Array.isArray(value)) return <GoalResolutionEditor value={value as JsonObject} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "public_knowledge" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailPublicKnowledgeEditor value={value as JsonObject} document={document} selection={collectionSelection} onCollectionChange={onCollectionChange} onCollectionRemove={onCollectionRemove} onCollectionMove={onCollectionMove} /> : <PublicKnowledgeEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    return <RootEditor rootKey={rootKey} value={value} document={document} onChange={onChange} />;
  }
  if (value && typeof value === "object" && !Array.isArray(value) && "kind" in value && typeof (value as JsonObject).kind === "string") {
    return <EntityEditor entity={value as unknown as DraftObject} document={document} initializationHref="../initialization" onChange={onChange as (value: JsonObject) => void} focusPath={focusPath} onDeleteNested={onDeleteNested} />;
  }
  return <AdvancedJsonField value={value} onChange={onChange} path={path} label="Advanced structure" />;
}

export function TypedEntityEditor({ entity, document, initializationHref = "../initialization", onChange, focusPath, onDeleteFact, onDeleteNested, scenarioId }: { entity: DraftObject; document: JsonObject; initializationHref?: string; onChange: (value: JsonObject) => void; focusPath?: string | null; onDeleteFact?: (nodeKey: string, factKey: string) => void; onDeleteNested?: NestedDeleteHandler; scenarioId?: string }) {
  return <EntityEditor entity={entity} document={document} initializationHref={initializationHref} onChange={onChange} focusPath={focusPath} onDeleteFact={onDeleteFact} onDeleteNested={onDeleteNested} scenarioId={scenarioId} />;
}
