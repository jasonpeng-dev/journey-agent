import { useEffect } from "react";

import { factInitialValueMetadata, metadataForKind, rootFieldRegistry, type FieldMetadata } from "../editor-registry";
import type { RootOwnerSelection } from "../editor-collections";
import type { DraftObject, JsonObject } from "../editor";
import { editorLabel, fieldLabel } from "../ui";
import {
  AdvancedSection,
  BooleanControl,
  EnumSelect,
  MultiValuePicker,
  NumberInput,
  NestedObjectHeader,
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
  DerivedStateEditor,
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
  if (metadata.type === "multi-enum") return <MultiValuePicker value={current} onChange={next as (value: string[]) => void} path={fieldPath} label={label} options={(metadata.enum ?? []).map((item) => ({ key: item, name: item }))} />;
  if (metadata.type === "reference") return <ReferencePicker value={current} onChange={next as (value: string) => void} domain={metadata.referenceDomain!} document={document} path={fieldPath} label={label} />;
  if (metadata.type === "multi-reference") return <MultiValuePicker value={current} onChange={next as (value: string[]) => void} path={fieldPath} label={label} options={referenceOptions(document, metadata.referenceDomain!)} />;
  if (metadata.type === "boolean") return <BooleanControl value={current} onChange={next} path={fieldPath} label={label} />;
  if (metadata.type === "integer" || metadata.type === "number") return <NumberInput value={current} onChange={next} path={fieldPath} label={label} integer={metadata.type === "integer"} />;
  if (metadata.type === "textarea" || metadata.multiline) return <TextArea value={typeof current === "string" ? current : ""} onChange={next as (value: string) => void} path={fieldPath} label={label} />;
  return <TextInput value={typeof current === "string" || typeof current === "number" ? String(current) : ""} onChange={next as (value: string) => void} path={fieldPath} label={label} />;
}

function StringArrayEditor({ value, onChange, label, path }: { value: unknown; onChange: (value: string[]) => void; label: string; path: string }) {
  const items = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  return <ScalarListEditor value={items} onChange={(next) => onChange(next.filter((item): item is string => typeof item === "string"))} path={path} label={label} />;
}

function FactEditor({ value, document, path, onChange }: { value: JsonObject; document: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const factType = typeof value.value_type === "string" ? value.value_type : "BOOLEAN";
  const allowed = Array.isArray(value.allowed_values) ? value.allowed_values : [];
  const typedMetadata = factInitialValueMetadata(factType, allowed);
  return <article className="nested-editor"><header className="nested-object-header"><NestedObjectHeader typeLabel="FACT" identity={String(value.key ?? "未命名事实")} /></header><div className="typed-grid">
    {(["key", "name", "description"] as const).map((field) => <FieldRow key={field} metadata={{ path: field, type: field === "description" ? "textarea" : "text" }} value={value} document={document} path={path} onChange={onChange} />)}
    <FieldRow metadata={{ path: "value_type", type: "enum", enum: ["STRING", "ENUM", "INTEGER", "BOOLEAN"] }} value={value} document={document} path={path} onChange={onChange} />
    <FieldRow metadata={typedMetadata} value={value} document={document} path={path} onChange={onChange} />
    <FieldRow metadata={{ path: "initial_visibility", type: "enum", enum: ["KNOWN", "HIDDEN"] }} value={value} document={document} path={path} onChange={onChange} />
    <FieldRow metadata={{ path: "presentation_role", type: "enum", enum: ["HEADER_PRIMARY", "HEADER_SECONDARY", "BODY_MAIN", "SUPPORTING", "REQUIREMENT_ONLY"] }} value={value} document={document} path={path} onChange={onChange} />
    {factType === "ENUM" && <AdvancedJsonField value={value.allowed_values ?? []} onChange={(next) => onChange({ ...value, allowed_values: next })} path={`${path}.allowed_values`} label="Allowed values" />}
    <ValueLabelList value={value.value_labels} valueType={factType} allowedValues={allowed} path={`${path}.value_labels`} onChange={(next) => onChange({ ...value, value_labels: next })} />
    <AdvancedJsonField value={{ goal_addressable: value.goal_addressable ?? false, goal_aliases: value.goal_aliases ?? [], goal_examples: value.goal_examples ?? [], goal_target_values: value.goal_target_values ?? [] }} onChange={(next) => onChange({ ...value, ...(cloneObject(next)) })} path={`${path}.goal_metadata`} label="Goal metadata" />
  </div></article>;
}

function FactList({ value, document, path, onChange }: { value: unknown; document: JsonObject; path: string; onChange: (value: unknown) => void }) {
  const facts = Array.isArray(value) ? value : [];
  return <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Facts")}</h4><button type="button" className="small" onClick={() => onChange([...facts, { key: "new_fact", name: "新事实", description: "", value_type: "BOOLEAN", initial_value: false, initial_visibility: "KNOWN", allowed_values: [] }])}>＋ {editorLabel("Add fact")}</button></div>{facts.map((fact, index) => fact && typeof fact === "object" && !Array.isArray(fact) ? <FactEditor key={`${path}.${index}`} value={fact as JsonObject} document={document} path={`${path}.${index}`} onChange={(next) => onChange(facts.map((old, oldIndex) => oldIndex === index ? next : old))} /> : null)}</section>;
}

function EntityEditor({ entity, document, onChange, focusPath }: { entity: DraftObject; document: JsonObject; onChange: (value: JsonObject) => void; focusPath?: string | null }) {
  const metadata = metadataForKind(entity.kind);
  const value = entity.value;
  useEffect(() => {
    if (!focusPath) return;
    const element = window.document.getElementById(fieldId(focusPath));
    element?.scrollIntoView({ block: "center" });
    if (element instanceof HTMLElement && typeof element.focus === "function") element.focus();
  }, [focusPath]);
  if (entity.kind === "action") return <><ActionEditor entity={entity} document={document} onChange={onChange} /><ActionAuthorityPolicyEditor value={value.authority_policy ?? {}} path={`${entity.kind}.${entity.key}.authority_policy`} onChange={(next) => onChange({ ...value, authority_policy: next })} /></>;
  if (entity.kind === "rule") return <RuleEditor entity={entity} document={document} onChange={onChange} />;
  if (entity.kind === "derived_state") return <DerivedStateEditor entity={entity} document={document} onChange={onChange} />;
  if (entity.kind === "public_reference") return <PublicReferenceEditor entity={entity} document={document} onChange={onChange} />;
  return <div className="typed-entity-editor"><div className="typed-grid">
    {metadata.fields.map((field) => field.type === "text" && Array.isArray(nestedValue(value, field.path))
      ? <StringArrayEditor key={field.path} value={nestedValue(value, field.path)} label={fieldLabel(field.path)} path={`${entity.kind}.${entity.key}.${field.path}`} onChange={(next) => onChange(withNestedValue(value, field.path, next))} />
      : <FieldRow key={field.path} metadata={field} value={value} document={document} path={`${entity.kind}.${entity.key}`} onChange={onChange} />)}
  </div>
    {entity.kind === "node" && <FactList value={value.facts} document={document} path={`${entity.kind}.${entity.key}.facts`} onChange={(next) => onChange({ ...value, facts: next })} />}
    {metadata.nested?.map((nested) => <AdvancedJsonField key={nested} value={value[nested]} onChange={(next) => onChange({ ...value, [nested]: next })} path={`${entity.kind}.${entity.key}.${nested}`} label={fieldLabel(nested)} />)}
    <p className="typed-help">未在基础表单中展开的合法字段会保留在原始草稿中；复杂结构当前标记为高级结构，不会静默删除。</p>
  </div>;
}

function RootEditor({ rootKey, value, document, onChange }: { rootKey: string; value: unknown; document: JsonObject; onChange: (value: unknown) => void }) {
  const object = cloneObject(value);
  const fields = rootFieldRegistry[rootKey] ?? [];
  return <div className="typed-root-editor"><div className="typed-grid">{fields.map((metadata) => <FieldRow key={metadata.path} metadata={metadata} value={object} document={document} path={rootKey} onChange={onChange as (value: JsonObject) => void} />)}</div>{rootKey === "initialization" && <p className="typed-help">资源定义在世界模型中维护；资源初始状态、资源池和区域资源知识属于初始化数据，当前以高级结构保留。</p>}{rootKey === "planning" && <p className="typed-help">规划指引已提供结构化文本数组入口；恢复提示会在规则与规划表单中展开。</p>}</div>;
}

export function TypedEditor({ section, value, document, onChange, path = section, focusPath, collectionSelection = null, onCollectionChange, onCollectionRemove }: Props) {
  if (section === "overview" || section === "initialization" || section === "goal-resolution" || section === "planning" || section === "public-knowledge") {
    const rootKey = section === "overview" ? "metadata" : section === "goal-resolution" ? "goal_resolution" : section === "public-knowledge" ? "public_knowledge" : section;
    if (rootKey === "metadata" && value && typeof value === "object" && !Array.isArray(value)) return <ScenarioOverviewEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "initialization" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailInitializationEditor value={value as JsonObject} document={document} selection={collectionSelection} onChange={onChange as (value: JsonObject) => void} onCollectionChange={onCollectionChange} onCollectionRemove={onCollectionRemove} /> : <InitializationEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "planning" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailPlanningEditor value={value as JsonObject} document={document} selection={collectionSelection} onChange={onChange as (value: JsonObject) => void} onCollectionChange={onCollectionChange} onCollectionRemove={onCollectionRemove} /> : <PlanningEditor value={value as JsonObject} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "goal_resolution" && value && typeof value === "object" && !Array.isArray(value)) return <GoalResolutionEditor value={value as JsonObject} onChange={onChange as (value: JsonObject) => void} />;
    if (rootKey === "public_knowledge" && value && typeof value === "object" && !Array.isArray(value)) return onCollectionChange && onCollectionRemove ? <MasterDetailPublicKnowledgeEditor value={value as JsonObject} document={document} selection={collectionSelection} onCollectionChange={onCollectionChange} onCollectionRemove={onCollectionRemove} /> : <PublicKnowledgeEditor value={value as JsonObject} document={document} onChange={onChange as (value: JsonObject) => void} />;
    return <RootEditor rootKey={rootKey} value={value} document={document} onChange={onChange} />;
  }
  if (value && typeof value === "object" && !Array.isArray(value) && "kind" in value && typeof (value as JsonObject).kind === "string") {
    return <EntityEditor entity={value as unknown as DraftObject} document={document} onChange={onChange as (value: JsonObject) => void} focusPath={focusPath} />;
  }
  return <AdvancedJsonField value={value} onChange={onChange} path={path} label="Advanced structure" />;
}

export function TypedEntityEditor({ entity, document, onChange, focusPath }: { entity: DraftObject; document: JsonObject; onChange: (value: JsonObject) => void; focusPath?: string | null }) {
  return <EntityEditor entity={entity} document={document} onChange={onChange} focusPath={focusPath} />;
}
