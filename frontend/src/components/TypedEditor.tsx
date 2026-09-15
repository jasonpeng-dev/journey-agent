import { useEffect, useState } from "react";

import { entityRegistry, factInitialValueMetadata, metadataForKind, rootFieldRegistry, type FieldMetadata, type ReferenceDomain } from "../editor-registry";
import type { DraftObject, EntityKind, JsonObject } from "../editor";
import { sectionObjects } from "../editor";
import { fieldLabel, uiLabel } from "../ui";

type Props = {
  section: string;
  value: unknown;
  document: JsonObject;
  onChange: (value: unknown) => void;
  path?: string;
  focusPath?: string | null;
};

type Option = { key: string; name: string };

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

function keyName(value: JsonObject, key: string, fallback = key): Option {
  return { key, name: typeof value.name === "string" && value.name.trim() ? value.name : fallback };
}

function referenceOptions(document: JsonObject, domain: ReferenceDomain): Option[] {
  const objects = (kind: EntityKind) => sectionObjects(document, entityRegistry[kind].section).filter((item) => item.kind === kind).map((item) => ({ key: item.key, name: item.name }));
  if (domain === "fact") {
    const nodes = sectionObjects(document, "world").filter((item) => item.kind === "node");
    return nodes.flatMap((node) => Array.isArray(node.value.facts)
      ? (node.value.facts as unknown[]).flatMap((fact) => fact && typeof fact === "object" && !Array.isArray(fact) && typeof (fact as JsonObject).key === "string"
        ? [keyName(fact as JsonObject, `${node.key}.${String((fact as JsonObject).key)}`, `${node.name} · ${String((fact as JsonObject).name ?? (fact as JsonObject).key)}`)]
        : [])
      : []);
  }
  if (domain === "resource_pool") {
    const pools = nestedValue(document, "initialization.resource_pools");
    return Array.isArray(pools) ? pools.flatMap((pool) => pool && typeof pool === "object" && typeof (pool as JsonObject).pool_key === "string"
      ? [keyName(pool as JsonObject, String((pool as JsonObject).pool_key))]
      : []) : [];
  }
  const entityKind = domain as EntityKind;
  if (entityRegistry[entityKind]) return objects(entityKind);
  return [];
}

function pathId(path: string): string {
  return `editor-field-${path.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function FieldLabel({ metadata }: { metadata: FieldMetadata }) {
  return <span className="typed-field-label">{metadata.label ?? fieldLabel(metadata.path)}</span>;
}

function AdvancedJsonField({ value, onChange, path, label }: { value: unknown; onChange: (value: unknown) => void; path: string; label: string }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText(JSON.stringify(value ?? null, null, 2));
  }, [value]);
  return <div className="advanced-json-field">
    <label htmlFor={pathId(path)}><span>{label} <em>Advanced</em></span></label>
    <textarea id={pathId(path)} value={text} onChange={(event) => setText(event.target.value)} onBlur={() => {
      try { const parsed = JSON.parse(text); setError(null); onChange(parsed); }
      catch { setError("JSON 结构暂时无法解析，当前内容未写回草稿。"); }
    }} rows={Math.min(12, Math.max(4, text.split("\n").length))} />
    {error && <small className="field-error">{error}</small>}
  </div>;
}

function ScalarControl({ value, onChange, metadata, document, path }: { value: unknown; onChange: (value: unknown) => void; metadata: FieldMetadata; document: JsonObject; path: string }) {
  if (metadata.type === "json") return <AdvancedJsonField value={value} onChange={onChange} path={path} label={metadata.label ?? fieldLabel(metadata.path)} />;
  if (metadata.type === "enum") return <select id={pathId(path)} value={typeof value === "string" ? value : ""} onChange={(event) => onChange(event.target.value)}><option value="">请选择……</option>{(metadata.enum ?? []).map((item) => <option key={item} value={item}>{uiLabel(item)}</option>)}</select>;
  if (metadata.type === "multi-enum") {
    const selected = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
    return <select id={pathId(path)} multiple value={selected} onChange={(event) => onChange(Array.from(event.target.selectedOptions, (option) => option.value))}>{(metadata.enum ?? []).map((item) => <option key={item} value={item}>{uiLabel(item)}</option>)}</select>;
  }
  if (metadata.type === "reference") return <ReferenceSelect value={value} onChange={onChange} domain={metadata.referenceDomain!} document={document} path={path} />;
  if (metadata.type === "multi-reference") return <MultiReferenceSelect value={value} onChange={onChange} domain={metadata.referenceDomain!} document={document} path={path} />;
  if (metadata.type === "boolean") return <input id={pathId(path)} type="checkbox" checked={value === true} onChange={(event) => onChange(event.target.checked)} />;
  if (metadata.type === "integer" || metadata.type === "number") return <input id={pathId(path)} type="number" value={typeof value === "number" ? value : ""} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />;
  if (metadata.type === "textarea" || metadata.multiline) return <textarea id={pathId(path)} value={typeof value === "string" ? value : ""} onChange={(event) => onChange(event.target.value)} rows={4} />;
  return <input id={pathId(path)} value={typeof value === "string" || typeof value === "number" ? String(value) : ""} onChange={(event) => onChange(event.target.value)} />;
}

function ReferenceSelect({ value, onChange, domain, document, path }: { value: unknown; onChange: (value: unknown) => void; domain: ReferenceDomain; document: JsonObject; path: string }) {
  const options = referenceOptions(document, domain);
  const selected = typeof value === "string" ? value : "";
  return <select id={pathId(path)} value={selected} onChange={(event) => onChange(event.target.value)}><option value="">未选择</option>{options.map((option) => <option key={option.key} value={option.key}>{option.name} · {option.key}</option>)}{selected && !options.some((option) => option.key === selected) && <option value={selected}>{selected}（当前引用无法解析）</option>}</select>;
}

function MultiReferenceSelect({ value, onChange, domain, document, path }: { value: unknown; onChange: (value: unknown) => void; domain: ReferenceDomain; document: JsonObject; path: string }) {
  const options = referenceOptions(document, domain);
  const selected = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  return <select id={pathId(path)} multiple value={selected} onChange={(event) => onChange(Array.from(event.target.selectedOptions, (option) => option.value))}>{options.map((option) => <option key={option.key} value={option.key}>{option.name} · {option.key}</option>)}</select>;
}

function FieldRow({ metadata, value, document, path, onChange }: { metadata: FieldMetadata; value: JsonObject; document: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const current = nestedValue(value, metadata.path);
  return <div className="typed-field" data-field-path={path}>
    <FieldLabel metadata={metadata} />
    <ScalarControl metadata={metadata} value={current} document={document} path={`${path}.${metadata.path}`} onChange={(next) => onChange(withNestedValue(value, metadata.path, next))} />
  </div>;
}

function StringArrayEditor({ value, onChange, label, path }: { value: unknown; onChange: (value: string[]) => void; label: string; path: string }) {
  const items = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  return <div className="typed-array"><div className="typed-array-heading"><span>{label}</span><button type="button" className="small" onClick={() => onChange([...items, ""])}>＋ 添加</button></div>{items.map((item, index) => <div className="typed-array-row" key={`${path}.${index}`}><input id={pathId(`${path}.${index}`)} value={item} onChange={(event) => onChange(items.map((old, oldIndex) => oldIndex === index ? event.target.value : old))} /><button type="button" className="small danger" onClick={() => onChange(items.filter((_, oldIndex) => oldIndex !== index))}>移除</button></div>)}</div>;
}

function FactEditor({ value, document, path, onChange }: { value: JsonObject; document: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const factType = typeof value.value_type === "string" ? value.value_type : "BOOLEAN";
  const allowed = Array.isArray(value.allowed_values) ? value.allowed_values : [];
  const typedMetadata = factInitialValueMetadata(factType, allowed);
  return <article className="nested-editor"><header><strong>Fact</strong><code>{String(value.key ?? "")}</code></header><div className="typed-grid">
    {(["key", "name", "description"] as const).map((field) => <FieldRow key={field} metadata={{ path: field, type: field === "description" ? "textarea" : "text" }} value={value} document={document} path={path} onChange={onChange} />)}
    <FieldRow metadata={{ path: "value_type", type: "enum", enum: ["STRING", "ENUM", "INTEGER", "BOOLEAN"] }} value={value} document={document} path={path} onChange={onChange} />
    <FieldRow metadata={typedMetadata} value={value} document={document} path={path} onChange={onChange} />
    <FieldRow metadata={{ path: "initial_visibility", type: "enum", enum: ["KNOWN", "HIDDEN"] }} value={value} document={document} path={path} onChange={onChange} />
    {factType === "ENUM" && <AdvancedJsonField value={value.allowed_values ?? []} onChange={(next) => onChange({ ...value, allowed_values: next })} path={`${path}.allowed_values`} label="Allowed values" />}
    <AdvancedJsonField value={{ goal_addressable: value.goal_addressable ?? false, goal_aliases: value.goal_aliases ?? [], goal_examples: value.goal_examples ?? [], goal_target_values: value.goal_target_values ?? [] }} onChange={(next) => onChange({ ...value, ...(cloneObject(next)) })} path={`${path}.goal_metadata`} label="Goal metadata" />
  </div></article>;
}

function FactList({ value, document, path, onChange }: { value: unknown; document: JsonObject; path: string; onChange: (value: unknown) => void }) {
  const facts = Array.isArray(value) ? value : [];
  return <section className="nested-list"><div className="typed-array-heading"><h4>Facts</h4><button type="button" className="small" onClick={() => onChange([...facts, { key: "new_fact", name: "New fact", description: "", value_type: "BOOLEAN", initial_value: false, initial_visibility: "KNOWN", allowed_values: [] }])}>＋ 添加 Fact</button></div>{facts.map((fact, index) => fact && typeof fact === "object" && !Array.isArray(fact) ? <FactEditor key={`${path}.${index}`} value={fact as JsonObject} document={document} path={`${path}.${index}`} onChange={(next) => onChange(facts.map((old, oldIndex) => oldIndex === index ? next : old))} /> : null)}</section>;
}

function EntityEditor({ entity, document, onChange, focusPath }: { entity: DraftObject; document: JsonObject; onChange: (value: JsonObject) => void; focusPath?: string | null }) {
  const metadata = metadataForKind(entity.kind);
  const value = entity.value;
  useEffect(() => {
    if (!focusPath) return;
    const element = window.document.getElementById(pathId(focusPath));
    element?.scrollIntoView({ block: "center" });
    if (element instanceof HTMLElement && typeof element.focus === "function") element.focus();
  }, [focusPath]);
  return <div className="typed-entity-editor"><div className="typed-grid">
    {metadata.fields.map((field) => field.type === "text" && Array.isArray(nestedValue(value, field.path))
      ? <StringArrayEditor key={field.path} value={nestedValue(value, field.path)} label={fieldLabel(field.path)} path={`${entity.kind}.${entity.key}.${field.path}`} onChange={(next) => onChange(withNestedValue(value, field.path, next))} />
      : <FieldRow key={field.path} metadata={field} value={value} document={document} path={`${entity.kind}.${entity.key}`} onChange={onChange} />)}
  </div>
    {entity.kind === "node" && <FactList value={value.facts} document={document} path={`${entity.kind}.${entity.key}.facts`} onChange={(next) => onChange({ ...value, facts: next })} />}
    {metadata.nested?.map((nested) => <AdvancedJsonField key={nested} value={value[nested]} onChange={(next) => onChange({ ...value, [nested]: next })} path={`${entity.kind}.${entity.key}.${nested}`} label={fieldLabel(nested)} />)}
    <p className="typed-help">未在基础表单中展开的合法字段会保留在原 Draft；复杂结构当前标记为 Advanced，不会静默删除。</p>
  </div>;
}

function RootEditor({ rootKey, value, document, onChange }: { rootKey: string; value: unknown; document: JsonObject; onChange: (value: unknown) => void }) {
  const object = cloneObject(value);
  const fields = rootFieldRegistry[rootKey] ?? [];
  return <div className="typed-root-editor"><div className="typed-grid">{fields.map((metadata) => <FieldRow key={metadata.path} metadata={metadata} value={object} document={document} path={rootKey} onChange={onChange as (value: JsonObject) => void} />)}</div>{rootKey === "initialization" && <p className="typed-help">Resource definitions 在 World 中维护；resource initial states、pools 和 region knowledge 属于初始化数据，当前保留为 Advanced 结构。</p>}{rootKey === "planning" && <p className="typed-help">Planning instructions 已提供 typed 文本数组入口；recovery hints 在 Phase 3 Rule/Planning 表单中展开。</p>}</div>;
}

export function TypedEditor({ section, value, document, onChange, path = section, focusPath }: Props) {
  if (section === "overview" || section === "initialization" || section === "goal-resolution" || section === "planning" || section === "public-knowledge") {
    const rootKey = section === "overview" ? "metadata" : section === "goal-resolution" ? "goal_resolution" : section === "public-knowledge" ? "public_knowledge" : section;
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
