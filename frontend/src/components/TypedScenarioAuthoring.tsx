import { createContext, useContext, type ReactNode } from "react";

import { V2_ENUMS, type ReferenceDomain } from "../editor-registry";
import { rootCollectionItem, rootCollectionLabel, type RootCollectionSelection, type RootOwnerSelection } from "../editor-collections";
import { type DraftObject, type JsonObject } from "../editor";
import {
  AdvancedSection,
  BooleanControl,
  EnumSelect,
  MultiValuePicker,
  NestedCard,
  NestedObjectHeader,
  NumberInput,
  OptionSelect,
  ReferencePicker,
  ScalarListEditor,
  TextArea,
  TextInput,
} from "./editor/FormPrimitives";
import { referenceOptions } from "./editor/ReferencePicker";
import { ValueLabelList } from "./editor/ValueLabelEditor";
import { editorLabel, uiLabel } from "../ui";

type Change = (value: JsonObject) => void;
const AuthoringDocumentContext = createContext<JsonObject>({});

const EFFECT_KINDS = V2_ENUMS.effectKind;
const CONDITION_KINDS = V2_ENUMS.conditionKind;

function clone(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value)
    ? structuredClone(value) as JsonObject
    : {};
}

function setField(value: JsonObject, key: string, next: unknown): JsonObject {
  return { ...value, [key]: structuredClone(next) };
}

function arrayOf(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function stringsOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function cardSummary(...values: unknown[]): string {
  const parts = values.filter((value): value is string | number => (typeof value === "string" && value.trim().length > 0) || typeof value === "number").map(String);
  return parts.length > 0 ? parts.join(" · ") : "展开编辑";
}

function TextField({ value, onChange, path, label, multiline = false }: { value: unknown; onChange: (value: string) => void; path: string; label: string; multiline?: boolean }) {
  if (label === "Source relation type") return null;
  return multiline ? <TextArea value={value} onChange={onChange} path={path} label={label} /> : <TextInput value={value} onChange={onChange} path={path} label={label} />;
}

function NumberField({ value, onChange, path, label, integer = true }: { value: unknown; onChange: (value: number | null) => void; path: string; label: string; integer?: boolean }) {
  return <NumberInput value={value} onChange={onChange} path={path} label={label} integer={integer} />;
}

function BooleanField({ value, onChange, path, label }: { value: unknown; onChange: (value: boolean) => void; path: string; label: string }) {
  return <BooleanControl value={value} onChange={onChange} path={path} label={label} />;
}

function EnumField({ value, onChange, path, label, choices }: { value: unknown; onChange: (value: string) => void; path: string; label: string; choices: readonly string[] }) {
  return <EnumSelect value={value} onChange={onChange} path={path} label={label} choices={choices} />;
}

function ReferenceField({ value, onChange, path, label, domain, document }: { value: unknown; onChange: (value: string) => void; path: string; label: string; domain: ReferenceDomain; document: JsonObject }) {
  return <ReferencePicker value={value} onChange={onChange} path={path} label={label} domain={domain} document={document} />;
}

function MultiReferenceField({ value, onChange, path, label, domain, document }: { value: unknown; onChange: (value: string[]) => void; path: string; label: string; domain: ReferenceDomain; document: JsonObject }) {
  const options = path.endsWith("allowed_actor_capabilities")
    ? V2_ENUMS.capabilities.map((item) => ({ key: item, name: uiLabel(item) }))
    : referenceOptions(document, domain);
  return <MultiValuePicker value={value} onChange={onChange} path={path} label={label} options={options} />;
}

function ScalarField({ value, onChange, path, label }: { value: unknown; onChange: (value: string | number | boolean | null) => void; path: string; label: string }) {
  if (typeof value === "boolean") return <BooleanField value={value} onChange={onChange as (value: boolean) => void} path={path} label={label} />;
  if (typeof value === "number") return <NumberField value={value} onChange={onChange as (value: number | null) => void} path={path} label={label} />;
  return <TextField value={value} onChange={onChange as (value: string) => void} path={path} label={label} />;
}

function ScalarListField({ value, onChange, path, label }: { value: unknown; onChange: (value: Array<string | number | boolean>) => void; path: string; label: string }) {
  return <ScalarListEditor value={value} onChange={onChange} path={path} label={label} />;
}

function StringListField({ value, onChange, path, label, help }: { value: unknown; onChange: (value: string[]) => void; path: string; label: string; help?: ReactNode }) {
  const authoringDocument = useContext(AuthoringDocumentContext);
  if (path.includes(".prerequisites.") && path.endsWith(".requirements")) {
    return <RequirementListEditor value={value} document={authoringDocument} path={path} onChange={(next) => (onChange as unknown as (value: JsonObject[]) => void)(next)} />;
  }
  return <ScalarListEditor value={stringsOf(value)} onChange={(next) => onChange(next.filter((item): item is string => typeof item === "string"))} path={path} label={label} help={help} />;
}

function AdvancedJson({ value, onChange, path, label }: { value: unknown; onChange: (value: unknown) => void; path: string; label: string }) {
  return <AdvancedSection value={value} onChange={onChange} path={path} label={label} />;
}

function ListCard({ title, summary, machineKey, children, onAdd, onRemove, defaultExpanded = false }: { title: string; summary?: ReactNode; machineKey?: string; children: ReactNode; onAdd?: () => void; onRemove?: () => void; defaultExpanded?: boolean }) {
  return <NestedCard title={title} summary={summary ?? "展开编辑"} machineKey={machineKey} onAdd={onAdd} onRemove={onRemove} defaultExpanded={defaultExpanded}>{children}</NestedCard>;
}

function NodeSelectorEditor({ value, document, path, onChange }: { value: JsonObject; document: JsonObject; path: string; onChange: Change }) {
  const kind = typeof value.kind === "string" ? value.kind : "CURRENT_TARGET";
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <div className="typed-grid"><EnumField value={kind} onChange={(next) => { const base = defaultNodeSelector(next); onChange({ ...base, ...(next === kind ? value : {}) }); }} path={`${path}.kind`} label="Selector kind" choices={V2_ENUMS.selectorKind} />{kind === "EXPLICIT" && <ReferenceField value={value.node_key} onChange={(next) => update("node_key", next)} path={`${path}.node_key`} label="Node" domain="node" document={document} />}{kind === "RELATED" && <><ReferenceField value={value.relation_type_key} onChange={(next) => update("relation_type_key", next)} path={`${path}.relation_type_key`} label="Relation type" domain="relation_type" document={document} /><EnumField value={value.direction} onChange={(next) => update("direction", next)} path={`${path}.direction`} label="Direction" choices={V2_ENUMS.relationDirection} /><ReferenceField value={value.anchor_node_key} onChange={(next) => update("anchor_node_key", next)} path={`${path}.anchor_node_key`} label="Anchor node" domain="node" document={document} /><FactKeyField value={value.required_fact_key} onChange={(next) => update("required_fact_key", next)} path={`${path}.required_fact_key`} label="Required fact" document={document} nodeKey={typeof value.anchor_node_key === "string" ? value.anchor_node_key : undefined} /></>}</div>;
}

function FactKeyField({ value, onChange, path, label, document, nodeKey }: { value: unknown; onChange: (value: string) => void; path: string; label: string; document: JsonObject; nodeKey?: string }) {
  const all = referenceOptions(document, "fact");
  const options = nodeKey ? all.filter((item) => item.key.startsWith(`${nodeKey}.`)).map((item) => ({ ...item, key: item.key.slice(nodeKey.length + 1) })) : all.map((item) => ({ ...item, key: item.key.split(".").slice(-1)[0] }));
  return <OptionSelect value={value} onChange={onChange} path={path} label={label} options={options} placeholder="未选择" />;
}

function ValueExpressionEditor({ value, path, onChange }: { value: unknown; document?: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const current = clone(value);
  const source = typeof current.source === "string" ? current.source : "LITERAL";
  return <div className="typed-grid"><EnumField value={source} onChange={(next) => onChange(next === "LITERAL" ? { source: next, literal: current.literal ?? true } : { source: next, parameter_key: current.parameter_key ?? "" })} path={`${path}.source`} label="Value source" choices={V2_ENUMS.valueSource} />{source === "LITERAL" ? <ScalarField value={current.literal} onChange={(next) => onChange(setField(current, "literal", next))} path={`${path}.literal`} label="Literal" /> : <TextField value={current.parameter_key} onChange={(next) => onChange(setField(current, "parameter_key", next))} path={`${path}.parameter_key`} label="Parameter key" />}</div>;
}

function IntegerExpressionEditor({ value, path, onChange }: { value: unknown; path: string; onChange: (value: JsonObject) => void }) {
  const current = clone(value);
  const source = typeof current.source === "string" ? current.source : "LITERAL";
  return <div className="typed-grid"><EnumField value={source} onChange={(next) => onChange(next === "LITERAL" ? { source: next, literal: current.literal ?? 1, multiplier: current.multiplier ?? 1 } : { source: next, parameter_key: current.parameter_key ?? "", multiplier: current.multiplier ?? 1 })} path={`${path}.source`} label="Amount source" choices={V2_ENUMS.valueSource} />{source === "LITERAL" ? <NumberField value={current.literal} onChange={(next) => onChange(setField(current, "literal", next))} path={`${path}.literal`} label="Literal" /> : <TextField value={current.parameter_key} onChange={(next) => onChange(setField(current, "parameter_key", next))} path={`${path}.parameter_key`} label="Parameter key" />}<NumberField value={current.multiplier} onChange={(next) => onChange(setField(current, "multiplier", next))} path={`${path}.multiplier`} label="Multiplier" /></div>;
}

function ResourceScopeEditor({ value, document, path, onChange }: { value: unknown; document: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const current = clone(value);
  const kind = typeof current.kind === "string" ? current.kind : "EXPLICIT";
  return <div className="typed-grid"><EnumField value={kind} onChange={(next) => onChange(next === "EXPLICIT" ? { kind: next, node_key: current.node_key ?? "" } : { kind: next })} path={`${path}.kind`} label="Resource scope" choices={V2_ENUMS.resourceScope} />{kind === "EXPLICIT" && <ReferenceField value={current.node_key} onChange={(next) => onChange(setField(current, "node_key", next))} path={`${path}.node_key`} label="Scope node" domain="node" document={document} />}</div>;
}

function defaultNodeSelector(kind: string): JsonObject {
  if (kind === "EXPLICIT") return { kind, node_key: "" };
  if (kind === "RELATED") return { kind, relation_type_key: "", direction: "SOURCE", anchor_node_key: "", required_fact_key: "" };
  return { kind };
}

function defaultCondition(kind: string): JsonObject {
  if (kind === "ALL" || kind === "ANY") return { kind, conditions: [{ kind: "FACT_EQUALS", node: { kind: "CURRENT_TARGET" }, fact_key: "", value: true }] };
  if (kind === "NOT") return { kind, condition: { kind: "FACT_EQUALS", node: { kind: "CURRENT_TARGET" }, fact_key: "", value: true } };
  if (kind === "FACT_IN") return { kind, node: { kind: "CURRENT_TARGET" }, fact_key: "", values: [true] };
  if (kind === "FACT_COMPARE") return { kind, node: { kind: "CURRENT_TARGET" }, fact_key: "", operator: "EQ", value: true };
  if (kind === "RESOURCE_COMPARE") return { kind, resource_key: "", resource_scope: { kind: "ACTOR_CURRENT_REGION" }, operator: "GTE", value: 0 };
  if (kind === "PARAMETER_COMPARE") return { kind, parameter_key: "", operator: "EQ", value: "" };
  if (kind === "NODE_VISIBLE") return { kind, node: { kind: "CURRENT_TARGET" }, visibility: "KNOWN" };
  if (kind === "NODE_ACCESSIBLE") return { kind, node: { kind: "CURRENT_TARGET" }, access: "AVAILABLE" };
  if (kind === "RELATION_EXISTS") return { kind, node: { kind: "CURRENT_TARGET" }, relation_type_key: "", relation_direction: "SOURCE" };
  return { kind, node: { kind: "CURRENT_TARGET" }, fact_key: "", value: true };
}

function LegacyConditionEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void }) {
  const kind = typeof value.kind === "string" ? value.kind : "FACT_EQUALS";
  if (!(CONDITION_KINDS as readonly string[]).includes(kind)) {
    return <ListCard title={`未知条件 · ${kind}`} onRemove={onRemove}><AdvancedJson value={value} onChange={(next) => onChange(clone(next))} path={path} label="未知条件 JSON" /></ListCard>;
  }
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const children = arrayOf(value.conditions);
  return <ListCard title={`条件 · ${uiLabel(kind)}`} summary={cardSummary(value.fact_key, value.resource_key, value.parameter_key, value.operator)} onRemove={onRemove}><EnumField value={kind} onChange={(next) => onChange({ ...defaultCondition(next), key: value.key })} path={`${path}.kind`} label="Condition kind" choices={CONDITION_KINDS} />{(kind === "ALL" || kind === "ANY") && <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Child conditions")}</h4><button type="button" className="small" onClick={() => onChange(setField(value, "conditions", [...children, defaultCondition("FACT_EQUALS")]))}>＋ 添加条件</button></div>{children.map((child, index) => <ConditionEditor key={`${path}.conditions.${index}`} value={child} document={document} path={`${path}.conditions.${index}`} onChange={(next) => onChange(setField(value, "conditions", children.map((old, oldIndex) => oldIndex === index ? next : old)))} onRemove={() => onChange(setField(value, "conditions", children.filter((_, oldIndex) => oldIndex !== index)))} />)}</section>}{kind === "NOT" && <ConditionEditor value={clone(value.condition)} document={document} path={`${path}.condition`} onChange={(next) => update("condition", next)} />}{["FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE", "NODE_VISIBLE", "NODE_ACCESSIBLE", "RELATION_EXISTS"].includes(kind) && <NodeSelectorEditor value={clone(value.node)} document={document} path={`${path}.node`} onChange={(next) => update("node", next)} />}{["FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE"].includes(kind) && <FactKeyField value={value.fact_key} onChange={(next) => update("fact_key", next)} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node === "object" && value.node && (value.node as JsonObject).kind === "EXPLICIT" ? String((value.node as JsonObject).node_key ?? "") : undefined} />}{["FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_COMPARE", "RESOURCE_COMPARE", "PARAMETER_COMPARE"].includes(kind) && <EnumField value={value.operator} onChange={(next) => update("operator", next)} path={`${path}.operator`} label="Operator" choices={V2_ENUMS.comparison} />}{kind === "FACT_IN" && <ScalarListField value={value.values} onChange={(next) => update("values", next)} path={`${path}.values`} label="Accepted values" />}{["FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_COMPARE", "RESOURCE_COMPARE", "PARAMETER_COMPARE"].includes(kind) && <ScalarField value={value.value} onChange={(next) => update("value", next)} path={`${path}.value`} label="Value" />}{kind === "RESOURCE_COMPARE" && <><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="Resource" domain="resource" document={document} /><ResourceScopeEditor value={value.resource_scope} document={document} path={`${path}.resource_scope`} onChange={(next) => update("resource_scope", next)} /></>}{kind === "PARAMETER_COMPARE" && <TextField value={value.parameter_key} onChange={(next) => update("parameter_key", next)} path={`${path}.parameter_key`} label="Parameter key" />}{kind === "NODE_VISIBLE" && <EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={`${path}.visibility`} label="Visibility" choices={V2_ENUMS.visibility} />}{kind === "NODE_ACCESSIBLE" && <EnumField value={value.access} onChange={(next) => update("access", next)} path={`${path}.access`} label="Access" choices={V2_ENUMS.access} />}{kind === "RELATION_EXISTS" && <><TextField value={value.relation_type_key} onChange={(next) => update("relation_type_key", next)} path={`${path}.relation_type_key`} label="Relation type" /><EnumField value={value.relation_direction} onChange={(next) => update("relation_direction", next)} path={`${path}.relation_direction`} label="Relation direction" choices={V2_ENUMS.relationDirection} /></>}</ListCard>;
}

function ConditionEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void }) {
  if (value.kind !== "RELATION_EXISTS") return <LegacyConditionEditor value={value} document={document} path={path} onChange={onChange} onRemove={onRemove} />;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <ListCard title={`Condition · ${uiLabel("RELATION_EXISTS")}`} summary={cardSummary(value.relation_type_key, value.relation_direction)} onRemove={onRemove}>
    <NodeSelectorEditor value={clone(value.node)} document={document} path={`${path}.node`} onChange={(next) => update("node", next)} />
    <ReferenceField value={value.relation_type_key} onChange={(next) => update("relation_type_key", next)} path={`${path}.relation_type_key`} label="Relation type" domain="relation_type" document={document} />
    <EnumField value={value.relation_direction} onChange={(next) => update("relation_direction", next)} path={`${path}.relation_direction`} label="Relation direction" choices={V2_ENUMS.relationDirection} />
  </ListCard>;
}

function defaultEffect(kind: string): JsonObject {
  if (kind === "SET_FACT") return { kind, node: { kind: "CURRENT_TARGET" }, fact_key: "", value: { source: "LITERAL", literal: true } };
  if (["REVEAL_FACT", "HIDE_FACT"].includes(kind)) return { kind, node: { kind: "CURRENT_TARGET" }, fact_key: "" };
  if (["REVEAL_NODE", "HIDE_NODE"].includes(kind)) return { kind, node: { kind: "CURRENT_TARGET" } };
  if (kind === "SET_NODE_ACCESS") return { kind, node: { kind: "CURRENT_TARGET" }, access: "AVAILABLE" };
  if (["ADJUST_RESOURCE", "RESERVE_RESOURCE", "RELEASE_RESOURCE"].includes(kind)) return { kind, resource_key: "", resource_scope: { kind: "ACTOR_CURRENT_REGION" }, amount: { source: "LITERAL", literal: 1, multiplier: 1 } };
  if (kind === "EMIT_FAILURE") return { kind, failure_code: "FAILURE", message: "", retryable: false };
  if (kind === "EMIT_OUTCOME") return { kind, outcome_code: "SUCCESS", retryable: false };
  if (kind === "WRITE_MEMORY_EVENT") return { kind, memory_key: "", memory_content: "" };
  if (kind === "SET_ACTOR_COMMAND_REACHABILITY") return { kind, actor_key: "", command_reachability: "ONLINE" };
  if (kind === "SET_RELATION_VISIBILITY") return { kind, relation_key: "", visibility: "VISIBLE" };
  if (kind === "SET_REGION_RESOURCE_VISIBILITY") return { kind, region_key: "", visibility: "VISIBLE" };
  if (kind === "SET_RESOURCE_POOL_VISIBILITY") return { kind, pool_key: "", visibility: "VISIBLE" };
  if (kind === "SET_RESOURCE_POOL_AVAILABILITY") return { kind, pool_key: "", availability: "AVAILABLE" };
  return { kind };
}

function EffectEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void }) {
  const kind = typeof value.kind === "string" ? value.kind : "EMIT_OUTCOME";
  if (!(EFFECT_KINDS as readonly string[]).includes(kind)) {
    return <ListCard title={`未知效果 · ${kind}`} onRemove={onRemove}><AdvancedJson value={value} onChange={(next) => onChange(clone(next))} path={path} label="未知效果 JSON" /></ListCard>;
  }
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const factKinds = ["SET_FACT", "REVEAL_FACT", "HIDE_FACT"];
  const nodeKinds = ["REVEAL_NODE", "HIDE_NODE", "SET_NODE_ACCESS"];
  const resourceKinds = ["ADJUST_RESOURCE", "RESERVE_RESOURCE", "RELEASE_RESOURCE"];
  return <ListCard title={`效果 · ${uiLabel(kind)}`} summary={cardSummary(value.fact_key, value.resource_key, value.outcome_code, value.failure_code)} onRemove={onRemove}><EnumField value={kind} onChange={(next) => onChange({ ...defaultEffect(next), key: value.key })} path={`${path}.kind`} label="Effect kind" choices={EFFECT_KINDS} />{factKinds.includes(kind) && <><NodeSelectorEditor value={clone(value.node)} document={document} path={`${path}.node`} onChange={(next) => update("node", next)} /><FactKeyField value={value.fact_key} onChange={(next) => update("fact_key", next)} path={`${path}.fact_key`} label="Fact" document={document} /></>}{kind === "SET_FACT" && <ValueExpressionEditor value={value.value} document={document} path={`${path}.value`} onChange={(next) => update("value", next)} />}{nodeKinds.includes(kind) && <NodeSelectorEditor value={clone(value.node)} document={document} path={`${path}.node`} onChange={(next) => update("node", next)} />}{kind === "SET_NODE_ACCESS" && <EnumField value={value.access} onChange={(next) => update("access", next)} path={`${path}.access`} label="Access" choices={V2_ENUMS.access} />}{resourceKinds.includes(kind) && <><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="Resource" domain="resource" document={document} /><ResourceScopeEditor value={value.resource_scope} document={document} path={`${path}.resource_scope`} onChange={(next) => update("resource_scope", next)} /><IntegerExpressionEditor value={value.amount} path={`${path}.amount`} onChange={(next) => update("amount", next)} /></>}{kind === "EMIT_OUTCOME" && <TextField value={value.outcome_code} onChange={(next) => update("outcome_code", next)} path={`${path}.outcome_code`} label="Outcome code" />}{kind === "EMIT_FAILURE" && <><TextField value={value.failure_code} onChange={(next) => update("failure_code", next)} path={`${path}.failure_code`} label="Failure code" /><TextField value={value.message} onChange={(next) => update("message", next)} path={`${path}.message`} label="Message" multiline /></>}{["EMIT_OUTCOME", "EMIT_FAILURE"].includes(kind) && <BooleanField value={value.retryable} onChange={(next) => update("retryable", next)} path={`${path}.retryable`} label="Retryable" />}{kind === "WRITE_MEMORY_EVENT" && <><TextField value={value.memory_key} onChange={(next) => update("memory_key", next)} path={`${path}.memory_key`} label="Memory key" /><TextField value={value.memory_content} onChange={(next) => update("memory_content", next)} path={`${path}.memory_content`} label="Memory content" multiline /></>}{kind === "SET_ACTOR_COMMAND_REACHABILITY" && <><ReferenceField value={value.actor_key} onChange={(next) => update("actor_key", next)} path={`${path}.actor_key`} label="Actor" domain="actor" document={document} /><EnumField value={value.command_reachability} onChange={(next) => update("command_reachability", next)} path={`${path}.command_reachability`} label="Reachability" choices={["ONLINE", "DISCONNECTED"]} /></>}{kind === "SET_RELATION_VISIBILITY" && <><ReferenceField value={value.relation_key} onChange={(next) => update("relation_key", next)} path={`${path}.relation_key`} label="Relation" domain="relation" document={document} /><EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={`${path}.visibility`} label="Visibility" choices={V2_ENUMS.relationVisibility} /></>}{kind === "SET_REGION_RESOURCE_VISIBILITY" && <><ReferenceField value={value.region_key} onChange={(next) => update("region_key", next)} path={`${path}.region_key`} label="Region" domain="node" document={document} /><EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={`${path}.visibility`} label="Inventory visibility" choices={V2_ENUMS.resourceInventoryVisibility} /></>}{kind === "SET_RESOURCE_POOL_VISIBILITY" && <><ReferenceField value={value.pool_key} onChange={(next) => update("pool_key", next)} path={`${path}.pool_key`} label="Resource pool" domain="resource_pool" document={document} /><EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={`${path}.visibility`} label="Pool visibility" choices={V2_ENUMS.resourcePoolVisibility} /></>}{kind === "SET_RESOURCE_POOL_AVAILABILITY" && <><ReferenceField value={value.pool_key} onChange={(next) => update("pool_key", next)} path={`${path}.pool_key`} label="Resource pool" domain="resource_pool" document={document} /><EnumField value={value.availability} onChange={(next) => update("availability", next)} path={`${path}.availability`} label="Availability" choices={V2_ENUMS.resourceAvailability} /></>}{kind === "REVEAL_TARGET_REGION_FACILITY_FACTS" && <p className="typed-help">此效果没有额外字段；其运行时语义由 V2 引擎契约定义。</p>}</ListCard>;
}

function FactReferenceEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void }) {
  return <ListCard title="事实引用" summary={cardSummary(value.node_key, value.fact_key)} onRemove={onRemove}><ReferenceField value={value.node_key} onChange={(next) => onChange(setField(value, "node_key", next))} path={`${path}.node_key`} label="Node" domain="node" document={document} /><FactKeyField value={value.fact_key} onChange={(next) => onChange(setField(value, "fact_key", next))} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} /></ListCard>;
}

function KnowledgeGateEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void }) {
  if (path.endsWith("availability_requirement")) {
    return <ListCard title="资源可用性要求" summary={cardSummary(value.node_key, value.fact_key, value.value)} onRemove={onRemove}><ReferenceField value={value.node_key} onChange={(next) => onChange(setField(value, "node_key", next))} path={`${path}.node_key`} label="Node" domain="node" document={document} /><FactKeyField value={value.fact_key} onChange={(next) => onChange(setField(value, "fact_key", next))} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} /><ScalarField value={value.value} onChange={(next) => onChange(setField(value, "value", next))} path={`${path}.value`} label="Required value" /></ListCard>;
  }
  return <ListCard title="知识门槛" summary={cardSummary(value.node_key, value.fact_key)} onRemove={onRemove}><ReferenceField value={value.node_key} onChange={(next) => onChange(setField(value, "node_key", next))} path={`${path}.node_key`} label="Node" domain="node" document={document} /><FactKeyField value={value.fact_key} onChange={(next) => onChange(setField(value, "fact_key", next))} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} /><ScalarListField value={value.accepted_values} onChange={(next) => onChange(setField(value, "accepted_values", next))} path={`${path}.accepted_values`} label="Accepted values" /></ListCard>;
}

function ActionPlanningEditor({ value, document, path, onChange }: { value: JsonObject; document: JsonObject; path: string; onChange: Change }) {
  const current = clone(value);
  const terminals = arrayOf(current.terminal_effects);
  const targetTerminals = arrayOf(current.target_terminal_effects);
  const supporting = arrayOf(current.supporting_effects);
  const update = (key: string, next: unknown) => onChange(setField(current, key, next));
  const list = (items: JsonObject[], key: string, title: string, create: JsonObject, render: (item: JsonObject, index: number, set: (next: JsonObject) => void, remove: () => void) => ReactNode) => <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel(title)}</h4><button type="button" className="small" onClick={() => update(key, [...items, create])}>＋ 添加</button></div>{items.map((item, index) => render(item, index, (next) => update(key, items.map((old, oldIndex) => oldIndex === index ? next : old)), () => update(key, items.filter((_, oldIndex) => oldIndex !== index))))}</section>;
  return <article className="nested-editor"><header className="nested-object-header"><NestedObjectHeader typeLabel="行动规划" identity="投影" /></header>{list(terminals, "terminal_effects", "Terminal Fact effects", { node_key: "", fact_key: "" }, (item, index, set, remove) => <FactReferenceEditor key={`${path}.terminal_effects.${index}`} value={item} document={document} path={`${path}.terminal_effects.${index}`} onChange={set} onRemove={remove} />)}{list(targetTerminals, "target_terminal_effects", "Target-relative Fact effects", { fact_key: "", value: true }, (item, index, set, remove) => <ListCard key={`${path}.target_terminal_effects.${index}`} title="Target Fact effect" onRemove={remove}><FactKeyField value={item.fact_key} onChange={(next) => set(setField(item, "fact_key", next))} path={`${path}.target_terminal_effects.${index}.fact_key`} label="Fact" document={document} /><ScalarField value={item.value} onChange={(next) => set(setField(item, "value", next))} path={`${path}.target_terminal_effects.${index}.value`} label="Value" /></ListCard>)}{list(supporting, "supporting_effects", "Supporting Fact effects", { node_key: "", fact_key: "" }, (item, index, set, remove) => <FactReferenceEditor key={`${path}.supporting_effects.${index}`} value={item} document={document} path={`${path}.supporting_effects.${index}`} onChange={set} onRemove={remove} />)}<StringListField value={current.success_outcome_codes} onChange={(next) => update("success_outcome_codes", next)} path={`${path}.success_outcome_codes`} label="Success outcomes" /><StringListField value={current.wait_success_outcome_codes} onChange={(next) => update("wait_success_outcome_codes", next)} path={`${path}.wait_success_outcome_codes`} label="Wait success outcomes" /><StringListField value={current.hints} onChange={(next) => update("hints", next)} path={`${path}.hints`} label="Planner hints" />{current.knowledge_gate ? <KnowledgeGateEditor value={clone(current.knowledge_gate)} document={document} path={`${path}.knowledge_gate`} onChange={(next) => update("knowledge_gate", next)} onRemove={() => update("knowledge_gate", null)} /> : <button type="button" className="small" onClick={() => update("knowledge_gate", { node_key: "", fact_key: "", accepted_values: [true] })}>＋ {editorLabel("Add knowledge gate")}</button>}</article>;
}

function ParameterEditorBase({ value, path, onChange, onRemove }: { value: JsonObject; document?: JsonObject; path: string; onChange: Change; onRemove: () => void }) {
  return <ListCard title={`参数 · ${String(value.key ?? "")}`} summary={cardSummary(value.name, value.value_type)} onRemove={onRemove}><TextField value={value.key} onChange={(next) => onChange(setField(value, "key", next))} path={`${path}.key`} label="Key" /><TextField value={value.name} onChange={(next) => onChange(setField(value, "name", next))} path={`${path}.name`} label="Name" /><EnumField value={value.value_type} onChange={(next) => onChange(setField(value, "value_type", next))} path={`${path}.value_type`} label="Value type" choices={V2_ENUMS.parameterType} /><BooleanField value={value.required} onChange={(next) => onChange(setField(value, "required", next))} path={`${path}.required`} label="Required" />{value.value_type === "INTEGER" && <><NumberField value={value.minimum} onChange={(next) => onChange(setField(value, "minimum", next))} path={`${path}.minimum`} label="Minimum" /><NumberField value={value.maximum} onChange={(next) => onChange(setField(value, "maximum", next))} path={`${path}.maximum`} label="Maximum" /></>}{value.value_type === "ENUM" && <ScalarListField value={value.allowed_values} onChange={(next) => onChange(setField(value, "allowed_values", next))} path={`${path}.allowed_values`} label="Allowed values" />}{typeof value.semantic_reference_type === "string" && <EnumField value={value.semantic_reference_type} onChange={(next) => onChange(setField(value, "semantic_reference_type", next))} path={`${path}.semantic_reference_type`} label="Semantic reference" choices={V2_ENUMS.actionTargetReference} />}</ListCard>;
}

function ParameterEditor({ value, path, onChange, onRemove }: { value: JsonObject; document?: JsonObject; path: string; onChange: Change; onRemove: () => void }) {
  return <>
    <ParameterEditorBase value={value} path={path} onChange={onChange} onRemove={onRemove} />
    <ListCard title="Parameter semantics" summary={cardSummary(value.default, value.semantic_reference_type)}>
      <ScalarField value={value.default} onChange={(next) => onChange(setField(value, "default", next))} path={`${path}.default`} label="Default" />
      {typeof value.semantic_reference_type !== "string" && <EnumField value={value.semantic_reference_type} onChange={(next) => onChange(setField(value, "semantic_reference_type", next))} path={`${path}.semantic_reference_type`} label="Semantic reference" choices={V2_ENUMS.actionTargetReference} />}
    </ListCard>
  </>;
}

function ExpectedOutcomeEditor({ value, path, onChange, onRemove }: { value: JsonObject; path: string; onChange: Change; onRemove: () => void }) {
  return <ListCard title={`结果 · ${String(value.code ?? "")}`} summary={cardSummary(value.name, value.success === true ? "成功" : value.success === false ? "失败" : "")} onRemove={onRemove}><TextField value={value.code} onChange={(next) => onChange(setField(value, "code", next))} path={`${path}.code`} label="Code" /><TextField value={value.name} onChange={(next) => onChange(setField(value, "name", next))} path={`${path}.name`} label="Name" /><BooleanField value={value.success} onChange={(next) => onChange(setField(value, "success", next))} path={`${path}.success`} label="Success" /></ListCard>;
}

function LegacyActionEditor({ entity, document, onChange }: { entity: DraftObject; document: JsonObject; onChange: Change }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const parameters = arrayOf(value.parameters);
  const outcomes = arrayOf(value.expected_outcomes);
  const targetRoles = arrayOf(value.target_actor_roles);
  const bindings = arrayOf(value.operation_bindings);
  return <div className="typed-specialized-editor"><div className="typed-grid"><TextField value={value.key} onChange={(next) => update("key", next)} path={`${entity.kind}.${entity.key}.key`} label="Key" /><TextField value={value.name} onChange={(next) => update("name", next)} path={`${entity.kind}.${entity.key}.name`} label="Name" /><TextField value={value.description} onChange={(next) => update("description", next)} path={`${entity.kind}.${entity.key}.description`} label="Description" multiline /><ReferenceField value={value.required_interaction_key} onChange={(next) => update("required_interaction_key", next)} path={`${entity.kind}.${entity.key}.required_interaction_key`} label="Required interaction" domain="interaction" document={document} /><EnumField value={value.execution_mode} onChange={(next) => update("execution_mode", next)} path={`${entity.kind}.${entity.key}.execution_mode`} label="Execution mode" choices={V2_ENUMS.executionMode} /><EnumField value={value.behavior} onChange={(next) => update("behavior", next)} path={`${entity.kind}.${entity.key}.behavior`} label="Behavior" choices={V2_ENUMS.behavior} /><EnumField value={value.locality} onChange={(next) => update("locality", next)} path={`${entity.kind}.${entity.key}.locality`} label="Locality" choices={V2_ENUMS.locality} /><EnumField value={value.target_kind} onChange={(next) => update("target_kind", next)} path={`${entity.kind}.${entity.key}.target_kind`} label="Target kind" choices={V2_ENUMS.targetKind} /><EnumField value={value.target_semantic_reference_type} onChange={(next) => update("target_semantic_reference_type", next)} path={`${entity.kind}.${entity.key}.target_semantic_reference_type`} label="Target semantic reference" choices={V2_ENUMS.actionTargetReference} /><ReferenceField value={value.required_actor_role_key} onChange={(next) => update("required_actor_role_key", next)} path={`${entity.kind}.${entity.key}.required_actor_role_key`} label="Required actor role" domain="role" document={document} /><MultiReferenceField value={value.allowed_actor_capabilities} onChange={(next) => update("allowed_actor_capabilities", next)} path={`${entity.kind}.${entity.key}.allowed_actor_capabilities`} label="Allowed capabilities" domain="role" document={document} /><TextField value={value.source_relation_type_key} onChange={(next) => update("source_relation_type_key", next)} path={`${entity.kind}.${entity.key}.source_relation_type_key`} label="Source relation type" /></div><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Parameters")}</h4><button type="button" className="small" onClick={() => update("parameters", [...parameters, { key: "parameter", name: "Parameter", value_type: "STRING", required: true, allowed_values: [] }])}>＋ {editorLabel("Add parameter")}</button></div>{parameters.map((item, index) => <ParameterEditor key={`${entity.key}.parameters.${index}`} value={item} document={document} path={`${entity.kind}.${entity.key}.parameters.${index}`} onChange={(next) => update("parameters", parameters.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={() => update("parameters", parameters.filter((_, oldIndex) => oldIndex !== index))} />)}</section><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Expected outcomes")}</h4><button type="button" className="small" onClick={() => update("expected_outcomes", [...outcomes, { code: "SUCCESS", name: "Success", success: true }])}>＋ {editorLabel("Add outcome")}</button></div>{outcomes.map((item, index) => <ExpectedOutcomeEditor key={`${entity.key}.outcomes.${index}`} value={item} path={`${entity.kind}.${entity.key}.expected_outcomes.${index}`} onChange={(next) => update("expected_outcomes", outcomes.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={() => update("expected_outcomes", outcomes.filter((_, oldIndex) => oldIndex !== index))} />)}</section><ActionPlanningEditor value={clone(value.planning)} document={document} path={`${entity.kind}.${entity.key}.planning`} onChange={(next) => update("planning", next)} /><StringListField value={value.goal_required_slots} onChange={(next) => update("goal_required_slots", next)} path={`${entity.kind}.${entity.key}.goal_required_slots`} label="Goal required slots" /><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Target actor roles")}</h4><button type="button" className="small" onClick={() => update("target_actor_roles", [...targetRoles, { target_key: "", required_actor_role_key: "" }])}>＋ {editorLabel("Add target role")}</button></div>{targetRoles.map((item, index) => <ListCard key={`${entity.key}.target_actor_roles.${index}`} title="Target actor role" onRemove={() => update("target_actor_roles", targetRoles.filter((_, oldIndex) => oldIndex !== index))}><ReferenceField value={item.target_key} onChange={(next) => update("target_actor_roles", targetRoles.map((old, oldIndex) => oldIndex === index ? setField(item, "target_key", next) : old))} path={`${entity.kind}.${entity.key}.target_actor_roles.${index}.target_key`} label="Target" domain={value.target_kind === "ACTOR" ? "actor" : "node"} document={document} /><ReferenceField value={item.required_actor_role_key} onChange={(next) => update("target_actor_roles", targetRoles.map((old, oldIndex) => oldIndex === index ? setField(item, "required_actor_role_key", next) : old))} path={`${entity.kind}.${entity.key}.target_actor_roles.${index}.required_actor_role_key`} label="Required role" domain="role" document={document} /></ListCard>)}</section><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Operation bindings")}</h4><button type="button" className="small" onClick={() => update("operation_bindings", [...bindings, { role: "binding", value_type: "NODE", source: "EXPLICIT", description: "" }])}>＋ {editorLabel("Add binding")}</button></div>{bindings.map((item, index) => <ListCard key={`${entity.key}.operation_bindings.${index}`} title="Operation binding" onRemove={() => update("operation_bindings", bindings.filter((_, oldIndex) => oldIndex !== index))}><TextField value={item.role} onChange={(next) => update("operation_bindings", bindings.map((old, oldIndex) => oldIndex === index ? setField(item, "role", next) : old))} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.role`} label="Slot role" /><EnumField value={item.value_type} onChange={(next) => update("operation_bindings", bindings.map((old, oldIndex) => oldIndex === index ? setField(item, "value_type", next) : old))} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.value_type`} label="Value type" choices={V2_ENUMS.actionTargetReference} /><EnumField value={item.source} onChange={(next) => update("operation_bindings", bindings.map((old, oldIndex) => oldIndex === index ? setField(item, "source", next) : old))} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.source`} label="Source" choices={["EXPLICIT", "EXECUTION_START_ACTOR_REGION"]} /><TextField value={item.description} onChange={(next) => update("operation_bindings", bindings.map((old, oldIndex) => oldIndex === index ? setField(item, "description", next) : old))} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.description`} label="Description" multiline /></ListCard>)}</section></div>;
}

function ActionEditor({ entity, document, onChange }: { entity: DraftObject; document: JsonObject; onChange: Change }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <>
    <LegacyActionEditor entity={entity} document={document} onChange={onChange} />
    <section className="nested-list action-semantic-fields">
      <div className="typed-array-heading"><h4>{editorLabel("Target and relation semantics")}</h4></div>
      <MultiReferenceField value={value.target_node_type_keys} onChange={(next) => update("target_node_type_keys", next)} path={`${entity.kind}.${entity.key}.target_node_type_keys`} label="Target node types" domain="node_type" document={document} />
      <ReferenceField value={value.source_relation_type_key} onChange={(next) => update("source_relation_type_key", next)} path={`${entity.kind}.${entity.key}.source_relation_type_key`} label="Source relation type" domain="relation_type" document={document} />
    </section>
  </>;
}

function RuleEditor({ entity, document, onChange }: { entity: DraftObject; document: JsonObject; onChange: Change }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const effects = arrayOf(value.effects);
  const hasActionKey = typeof value.action_key === "string" && value.action_key.length > 0;
  const hasCondition = typeof value.condition === "object" && value.condition !== null && !Array.isArray(value.condition);
  const trigger = typeof value.trigger === "string" ? value.trigger : "ACTION";
  return <div className="typed-specialized-editor"><div className="typed-grid"><TextField value={value.key} onChange={(next) => update("key", next)} path={`${entity.kind}.${entity.key}.key`} label="Key" /><EnumField value={value.phase} onChange={(next) => update("phase", next)} path={`${entity.kind}.${entity.key}.phase`} label="Phase" choices={V2_ENUMS.phase} /><EnumField value={trigger} onChange={(next) => update("trigger", next)} path={`${entity.kind}.${entity.key}.trigger`} label="Trigger" choices={V2_ENUMS.trigger} />{trigger === "ACTION" && <ReferenceField value={value.action_key} onChange={(next) => update("action_key", next)} path={`${entity.kind}.${entity.key}.action_key`} label="Action" domain="action" document={document} />}{trigger === "STATE" && hasActionKey && <p className="field-error">STATE rules must not contain action_key; remove it before validation.</p>}<NumberField value={value.priority} onChange={(next) => update("priority", next)} path={`${entity.kind}.${entity.key}.priority`} label="Priority" /></div><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Condition AST")}</h4>{hasCondition ? <button type="button" className="small danger" onClick={() => update("condition", null)}>{editorLabel("Remove condition")}</button> : <button type="button" className="small" onClick={() => update("condition", defaultCondition("FACT_EQUALS"))}>＋ {editorLabel("Add condition")}</button>}</div>{hasCondition && <ConditionEditor value={clone(value.condition)} document={document} path={`${entity.kind}.${entity.key}.condition`} onChange={(next) => update("condition", next)} />}</section><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Effects")}</h4><button type="button" className="small" onClick={() => update("effects", [...effects, defaultEffect("EMIT_OUTCOME")])}>＋ {editorLabel("Add effect")}</button></div>{effects.map((item, index) => <EffectEditor key={`${entity.key}.effects.${index}`} value={item} document={document} path={`${entity.kind}.${entity.key}.effects.${index}`} onChange={(next) => update("effects", effects.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={() => update("effects", effects.filter((_, oldIndex) => oldIndex !== index))} />)}</section><p className="typed-help">当前所有 ConditionV2 和 EffectV2 变体都使用结构化控件；未知的未来变体仍由服务端校验，并应通过高级回退结构编辑。</p></div>;
}

function RequirementEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove: () => void }) {
  const kind = typeof value.kind === "string" ? value.kind : "FACT";
  if (!(V2_ENUMS.requirementKind as readonly string[]).includes(kind)) {
    return <ListCard title={`未知要求 · ${kind}`} onRemove={onRemove}><AdvancedJson value={value} onChange={(next) => onChange(clone(next))} path={path} label="未知要求 JSON" /></ListCard>;
  }
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <ListCard title={`Requirement · ${String(value.key ?? "")}`} onRemove={onRemove}><TextField value={value.key} onChange={(next) => update("key", next)} path={`${path}.key`} label="Key" /><EnumField value={kind} onChange={(next) => onChange({ key: value.key, description: value.description, kind: next, ...(next === "FACT" ? { node_key: "", fact_key: "", accepted_values: [true] } : next === "RESOURCE_AT_LEAST" ? { region_key: "", resource_key: "", minimum: 0 } : { derived_key: "", accepted_values: [true] })})} path={`${path}.kind`} label="Requirement kind" choices={V2_ENUMS.requirementKind} />{kind === "FACT" && <><ReferenceField value={value.node_key} onChange={(next) => update("node_key", next)} path={`${path}.node_key`} label="Node" domain="node" document={document} /><FactKeyField value={value.fact_key} onChange={(next) => update("fact_key", next)} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} /><ScalarListField value={value.accepted_values} onChange={(next) => update("accepted_values", next)} path={`${path}.accepted_values`} label="Accepted values" /></>}{kind === "RESOURCE_AT_LEAST" && <><ReferenceField value={value.region_key} onChange={(next) => update("region_key", next)} path={`${path}.region_key`} label="Region" domain="node" document={document} /><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="Resource" domain="resource" document={document} /><NumberField value={value.minimum} onChange={(next) => update("minimum", next)} path={`${path}.minimum`} label="Minimum" /></>}{kind === "DERIVED_STATE" && <><ReferenceField value={value.derived_key} onChange={(next) => update("derived_key", next)} path={`${path}.derived_key`} label="Derived state" domain="derived_state" document={document} /><ScalarListField value={value.accepted_values} onChange={(next) => update("accepted_values", next)} path={`${path}.accepted_values`} label="Accepted values" /></>}<TextField value={value.description} onChange={(next) => update("description", next)} path={`${path}.description`} label="Description" multiline />{value.knowledge_gate ? <KnowledgeGateEditor value={clone(value.knowledge_gate)} document={document} path={`${path}.knowledge_gate`} onChange={(next) => update("knowledge_gate", next)} onRemove={() => update("knowledge_gate", null)} /> : <button type="button" className="small" onClick={() => update("knowledge_gate", { node_key: "", fact_key: "", accepted_values: [true] })}>＋ {editorLabel("Add knowledge gate")}</button>}</ListCard>;
}

function RequirementListEditor({ value, document, path, onChange }: { value: unknown; document: JsonObject; path: string; onChange: (value: JsonObject[]) => void }) {
  const requirements = arrayOf(value);
  return <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Requirements")}</h4><button type="button" className="small" onClick={() => onChange([...requirements, { key: "requirement", kind: "FACT", node_key: "", fact_key: "", accepted_values: [true], description: "Requirement" }])}>＋ {editorLabel("Add requirement")}</button></div>{requirements.map((item, index) => <RequirementEditor key={`${path}.${index}`} value={item} document={document} path={`${path}.${index}`} onChange={(next) => onChange(requirements.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={() => onChange(requirements.filter((_, oldIndex) => oldIndex !== index))} />)}</section>;
}

function ObjectiveEditor({ entity, document, onChange }: { entity: DraftObject; document: JsonObject; onChange: Change }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const requirements = arrayOf(value.completion_requirements);
  const prerequisites = arrayOf(value.prerequisites);
  return <div className="typed-specialized-editor"><div className="typed-grid"><TextField value={value.key} onChange={(next) => update("key", next)} path={`${entity.kind}.${entity.key}.key`} label="Key" /><TextField value={value.name} onChange={(next) => update("name", next)} path={`${entity.kind}.${entity.key}.name`} label="Name" /><TextField value={value.description} onChange={(next) => update("description", next)} path={`${entity.kind}.${entity.key}.description`} label="Description" multiline /><TextField value={value.planning_guidance} onChange={(next) => update("planning_guidance", next)} path={`${entity.kind}.${entity.key}.planning_guidance`} label="Planning guidance" multiline /></div><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Completion requirements")}</h4><button type="button" className="small" onClick={() => update("completion_requirements", [...requirements, { key: "requirement", kind: "FACT", node_key: "", fact_key: "", accepted_values: [true], description: "Requirement" }])}>＋ {editorLabel("Add requirement")}</button></div>{requirements.map((item, index) => <RequirementEditor key={`${entity.key}.completion_requirements.${index}`} value={item} document={document} path={`${entity.kind}.${entity.key}.completion_requirements.${index}`} onChange={(next) => update("completion_requirements", requirements.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={() => update("completion_requirements", requirements.filter((_, oldIndex) => oldIndex !== index))} />)}</section><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Prerequisites")}</h4><button type="button" className="small" onClick={() => update("prerequisites", [...prerequisites, { key: "prerequisite", description: "Prerequisite", requirements: [] }])}>＋ {editorLabel("Add prerequisite")}</button></div>{prerequisites.map((item, index) => <ListCard key={`${entity.key}.prerequisites.${index}`} title={`Prerequisite · ${String(item.key ?? "")}`} onRemove={() => update("prerequisites", prerequisites.filter((_, oldIndex) => oldIndex !== index))}><TextField value={item.key} onChange={(next) => update("prerequisites", prerequisites.map((old, oldIndex) => oldIndex === index ? setField(item, "key", next) : old))} path={`${entity.kind}.${entity.key}.prerequisites.${index}.key`} label="Key" /><TextField value={item.description} onChange={(next) => update("prerequisites", prerequisites.map((old, oldIndex) => oldIndex === index ? setField(item, "description", next) : old))} path={`${entity.kind}.${entity.key}.prerequisites.${index}.description`} label="Description" multiline /><StringListField value={item.requirements} onChange={(next) => update("prerequisites", prerequisites.map((old, oldIndex) => oldIndex === index ? setField(item, "requirements", next) : old))} path={`${entity.kind}.${entity.key}.prerequisites.${index}.requirements`} label="Requirements (Advanced identity list)" /></ListCard>)}</section><MultiReferenceField value={value.subsumes} onChange={(next) => update("subsumes", next)} path={`${entity.kind}.${entity.key}.subsumes`} label="Subsumed objectives" domain="objective" document={document} /><StringListField value={value.goal_aliases} onChange={(next) => update("goal_aliases", next)} path={`${entity.kind}.${entity.key}.goal_aliases`} label="Goal aliases" /><StringListField value={value.goal_examples} onChange={(next) => update("goal_examples", next)} path={`${entity.kind}.${entity.key}.goal_examples`} label="Goal examples" /></div>;
}

function DependencyEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove: () => void }) {
  const kind = typeof value.kind === "string" ? value.kind : "FACT";
  if (!(V2_ENUMS.derivedDependency as readonly string[]).includes(kind)) {
    return <ListCard title={`未知依赖 · ${kind}`} onRemove={onRemove}><AdvancedJson value={value} onChange={(next) => onChange(clone(next))} path={path} label="未知依赖 JSON" /></ListCard>;
  }
  const update = (key: string, next: unknown) => {
    const nextValue = setField(value, key, next);
    delete nextValue.__owner_key;
    onChange(nextValue);
  };
  return <ListCard title={`Dependency · ${kind}`} onRemove={onRemove}><EnumField value={kind} onChange={(next) => onChange({ kind: next, ...(next === "FACT" ? { node_key: "", fact_key: "", accepted_values: [true] } : next === "RESOURCE_AT_LEAST" ? { region_key: "", resource_key: "", minimum: 0 } : { derived_key: "", accepted_values: [true] })})} path={`${path}.kind`} label="Dependency kind" choices={V2_ENUMS.derivedDependency} />{kind === "FACT" && <><ReferenceField value={value.node_key} onChange={(next) => update("node_key", next)} path={`${path}.node_key`} label="Node" domain="node" document={document} /><FactKeyField value={value.fact_key} onChange={(next) => update("fact_key", next)} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} /><ScalarListField value={value.accepted_values} onChange={(next) => update("accepted_values", next)} path={`${path}.accepted_values`} label="Accepted values" /></>}{kind === "RESOURCE_AT_LEAST" && <><ReferenceField value={value.region_key} onChange={(next) => update("region_key", next)} path={`${path}.region_key`} label="Region" domain="node" document={document} /><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="Resource" domain="resource" document={document} /><NumberField value={value.minimum} onChange={(next) => update("minimum", next)} path={`${path}.minimum`} label="Minimum" /></>}{kind === "DERIVED_STATE" && <><ReferenceField value={value.derived_key} onChange={(next) => update("derived_key", next)} path={`${path}.derived_key`} label="Derived state" domain="derived_state" document={document} /><ScalarListField value={value.accepted_values} onChange={(next) => update("accepted_values", next)} path={`${path}.accepted_values`} label="Accepted values" />{value.derived_key === value.__owner_key && <p className="field-error">Self-reference is invalid; the server validator will reject it.</p>}</>}{value.knowledge_gate ? <KnowledgeGateEditor value={clone(value.knowledge_gate)} document={document} path={`${path}.knowledge_gate`} onChange={(next) => update("knowledge_gate", next)} onRemove={() => update("knowledge_gate", null)} /> : <button type="button" className="small" onClick={() => update("knowledge_gate", { node_key: "", fact_key: "", accepted_values: [true] })}>＋ {editorLabel("Add knowledge gate")}</button>}</ListCard>;
}

function LegacyDerivedStateEditor({ entity, document, onChange }: { entity: DraftObject; document: JsonObject; onChange: Change }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const dependencies = arrayOf(value.dependencies);
  return <div className="typed-specialized-editor"><div className="typed-grid"><TextField value={value.key} onChange={(next) => update("key", next)} path={`${entity.kind}.${entity.key}.key`} label="Key" /><TextField value={value.name} onChange={(next) => update("name", next)} path={`${entity.kind}.${entity.key}.name`} label="Name" /><TextField value={value.description} onChange={(next) => update("description", next)} path={`${entity.kind}.${entity.key}.description`} label="Description" multiline /><EnumField value={value.value_type} onChange={(next) => update("value_type", next)} path={`${entity.kind}.${entity.key}.value_type`} label="Value type" choices={V2_ENUMS.factType} /><ScalarField value={value.available_value} onChange={(next) => update("available_value", next)} path={`${entity.kind}.${entity.key}.available_value`} label="Available value" /><ScalarField value={value.unavailable_value} onChange={(next) => update("unavailable_value", next)} path={`${entity.kind}.${entity.key}.unavailable_value`} label="Unavailable value" /><BooleanField value={value.goal_addressable} onChange={(next) => update("goal_addressable", next)} path={`${entity.kind}.${entity.key}.goal_addressable`} label="Goal addressable" /></div><ScalarListField value={value.allowed_values} onChange={(next) => update("allowed_values", next)} path={`${entity.kind}.${entity.key}.allowed_values`} label="Allowed values" /><StringListField value={value.goal_aliases} onChange={(next) => update("goal_aliases", next)} path={`${entity.kind}.${entity.key}.goal_aliases`} label="Goal aliases" /><StringListField value={value.goal_examples} onChange={(next) => update("goal_examples", next)} path={`${entity.kind}.${entity.key}.goal_examples`} label="Goal examples" /><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Dependencies")}</h4><button type="button" className="small" onClick={() => update("dependencies", [...dependencies, { kind: "FACT", node_key: "", fact_key: "", accepted_values: [true] }])}>＋ {editorLabel("Add dependency")}</button></div>{dependencies.map((item, index) => <DependencyEditor key={`${entity.key}.dependencies.${index}`} value={item} document={document} path={`${entity.kind}.${entity.key}.dependencies.${index}`} onChange={(next) => update("dependencies", dependencies.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={() => update("dependencies", dependencies.filter((_, oldIndex) => oldIndex !== index))} />)}</section></div>;
}

function DerivedStateEditor({ entity, document, onChange }: { entity: DraftObject; document: JsonObject; onChange: Change }) {
  const value = entity.value;
  return <>
    <LegacyDerivedStateEditor entity={entity} document={document} onChange={onChange} />
    <ValueLabelList value={value.value_labels} valueType={value.value_type} allowedValues={value.allowed_values} path={`${entity.kind}.${entity.key}.value_labels`} onChange={(next) => onChange(setField(value, "value_labels", next))} />
  </>;
}

function InitializationEditor({ value, document, onChange }: { value: JsonObject; document: JsonObject; onChange: Change }) {
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const initialStates = arrayOf(value.resource_initial_states);
  const pools = arrayOf(value.resource_pools);
  const regionKnowledge = arrayOf(value.region_resource_knowledge);
  return <div className="typed-specialized-editor"><div className="typed-grid"><ReferenceField value={value.start_node_key} onChange={(next) => update("start_node_key", next)} path="initialization.start_node_key" label="Start node" domain="node" document={document} /><ReferenceField value={value.primary_actor_key} onChange={(next) => update("primary_actor_key", next)} path="initialization.primary_actor_key" label="Primary actor" domain="actor" document={document} /></div><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Resource initial states")}</h4><button type="button" className="small" onClick={() => update("resource_initial_states", [...initialStates, { resource_key: "", scope_node_key: null, value: 0, reserved_value: 0 }])}>＋ {editorLabel("Add initial state")}</button></div>{initialStates.map((item, index) => <ListCard key={`initialization.resource_initial_states.${index}`} title="Resource initial state" onRemove={() => update("resource_initial_states", initialStates.filter((_, oldIndex) => oldIndex !== index))}><ReferenceField value={item.resource_key} onChange={(next) => update("resource_initial_states", initialStates.map((old, oldIndex) => oldIndex === index ? setField(item, "resource_key", next) : old))} path={`initialization.resource_initial_states.${index}.resource_key`} label="Resource" domain="resource" document={document} /><ReferenceField value={item.scope_node_key} onChange={(next) => update("resource_initial_states", initialStates.map((old, oldIndex) => oldIndex === index ? setField(item, "scope_node_key", next) : old))} path={`initialization.resource_initial_states.${index}.scope_node_key`} label="Scope node" domain="node" document={document} /><NumberField value={item.value} onChange={(next) => update("resource_initial_states", initialStates.map((old, oldIndex) => oldIndex === index ? setField(item, "value", next) : old))} path={`initialization.resource_initial_states.${index}.value`} label="Value" /><NumberField value={item.reserved_value} onChange={(next) => update("resource_initial_states", initialStates.map((old, oldIndex) => oldIndex === index ? setField(item, "reserved_value", next) : old))} path={`initialization.resource_initial_states.${index}.reserved_value`} label="Reserved" /></ListCard>)}</section><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Resource pools (initialization, not definitions)")}</h4><button type="button" className="small" onClick={() => update("resource_pools", [...pools, { pool_key: "new_pool", resource_key: "", region_key: null, facility_key: null, quantity: 0, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false }])}>＋ {editorLabel("Add pool")}</button></div>{pools.map((item, index) => <ListCard key={`initialization.resource_pools.${index}`} title={`Pool · ${String(item.pool_key ?? "")}`} onRemove={() => update("resource_pools", pools.filter((_, oldIndex) => oldIndex !== index))}><TextField value={item.pool_key} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "pool_key", next) : old))} path={`initialization.resource_pools.${index}.pool_key`} label="Pool key" /><ReferenceField value={item.resource_key} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "resource_key", next) : old))} path={`initialization.resource_pools.${index}.resource_key`} label="Resource" domain="resource" document={document} /><ReferenceField value={item.region_key} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "region_key", next) : old))} path={`initialization.resource_pools.${index}.region_key`} label="Region" domain="node" document={document} /><ReferenceField value={item.facility_key} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "facility_key", next) : old))} path={`initialization.resource_pools.${index}.facility_key`} label="Facility" domain="node" document={document} /><NumberField value={item.quantity} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "quantity", next) : old))} path={`initialization.resource_pools.${index}.quantity`} label="Quantity" /><NumberField value={item.reserved_value} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "reserved_value", next) : old))} path={`initialization.resource_pools.${index}.reserved_value`} label="Reserved" /><EnumField value={item.visibility} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "visibility", next) : old))} path={`initialization.resource_pools.${index}.visibility`} label="Visibility" choices={V2_ENUMS.resourcePoolVisibility} /><EnumField value={item.availability} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "availability", next) : old))} path={`initialization.resource_pools.${index}.availability`} label="Availability" choices={V2_ENUMS.resourceAvailability} /><BooleanField value={item.survey_discoverable} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "survey_discoverable", next) : old))} path={`initialization.resource_pools.${index}.survey_discoverable`} label="Survey discoverable" />{item.availability_requirement ? <KnowledgeGateEditor value={clone(item.availability_requirement)} document={document} path={`initialization.resource_pools.${index}.availability_requirement`} onChange={(next) => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "availability_requirement", next) : old))} onRemove={() => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "availability_requirement", null) : old))} /> : <button type="button" className="small" onClick={() => update("resource_pools", pools.map((old, oldIndex) => oldIndex === index ? setField(item, "availability_requirement", { node_key: "", fact_key: "", value: true }) : old))}>＋ {editorLabel("Add availability requirement")}</button>}</ListCard>)}</section><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Region resource knowledge")}</h4><button type="button" className="small" onClick={() => update("region_resource_knowledge", [...regionKnowledge, { region_key: "", resource_inventory_visibility: "VISIBLE", resource_survey_completed: false }])}>＋ {editorLabel("Add region state")}</button></div>{regionKnowledge.map((item, index) => <ListCard key={`initialization.region_resource_knowledge.${index}`} title="Region resource knowledge" onRemove={() => update("region_resource_knowledge", regionKnowledge.filter((_, oldIndex) => oldIndex !== index))}><ReferenceField value={item.region_key} onChange={(next) => update("region_resource_knowledge", regionKnowledge.map((old, oldIndex) => oldIndex === index ? setField(item, "region_key", next) : old))} path={`initialization.region_resource_knowledge.${index}.region_key`} label="Region" domain="node" document={document} /><EnumField value={item.resource_inventory_visibility} onChange={(next) => update("region_resource_knowledge", regionKnowledge.map((old, oldIndex) => oldIndex === index ? setField(item, "resource_inventory_visibility", next) : old))} path={`initialization.region_resource_knowledge.${index}.resource_inventory_visibility`} label="Inventory visibility" choices={V2_ENUMS.resourceInventoryVisibility} /><BooleanField value={item.resource_survey_completed} onChange={(next) => update("region_resource_knowledge", regionKnowledge.map((old, oldIndex) => oldIndex === index ? setField(item, "resource_survey_completed", next) : old))} path={`initialization.region_resource_knowledge.${index}.resource_survey_completed`} label="Survey completed" /></ListCard>)}</section></div>;
}

function PlanningEditor({ value, onChange }: { value: JsonObject; onChange: Change }) {
  return <div className="typed-specialized-editor"><StringListField value={value.instructions} onChange={(next) => onChange(setField(value, "instructions", next))} path="planning.instructions" label="Planning instructions" />{arrayOf(value.recovery_hints).map((item, index) => <ListCard key={`planning.recovery_hints.${index}`} title="Recovery hint" onRemove={() => onChange(setField(value, "recovery_hints", arrayOf(value.recovery_hints).filter((_, oldIndex) => oldIndex !== index)))}><TextField value={item.failure_code} onChange={(next) => onChange(setField(value, "recovery_hints", arrayOf(value.recovery_hints).map((old, oldIndex) => oldIndex === index ? setField(item, "failure_code", next) : old)))} path={`planning.recovery_hints.${index}.failure_code`} label="Failure code" /><TextField value={item.hint} onChange={(next) => onChange(setField(value, "recovery_hints", arrayOf(value.recovery_hints).map((old, oldIndex) => oldIndex === index ? setField(item, "hint", next) : old)))} path={`planning.recovery_hints.${index}.hint`} label="Hint" multiline /></ListCard>)}<button type="button" className="small" onClick={() => onChange(setField(value, "recovery_hints", [...arrayOf(value.recovery_hints), { failure_code: "FAILURE", hint: "" }]))}>＋ {editorLabel("Add recovery hint")}</button></div>;
}

function GoalResolutionEditor({ value, onChange }: { value: JsonObject; onChange: Change }) {
  return <div className="typed-specialized-editor"><BooleanField value={value.allow_llm_fallback} onChange={(next) => onChange(setField(value, "allow_llm_fallback", next))} path="goal_resolution.allow_llm_fallback" label="Allow LLM fallback" /><TextField value={value.clarification_prompt} onChange={(next) => onChange(setField(value, "clarification_prompt", next))} path="goal_resolution.clarification_prompt" label="Clarification prompt" multiline /><StringListField value={value.quick_inputs} onChange={(next) => onChange(setField(value, "quick_inputs", next))} path="goal_resolution.quick_inputs" label="Quick inputs" help="仅用于填充玩家的目标输入；玩家仍可编辑，提交后仍由 Goal Resolver 解析。" /><BooleanField value={value.world_goal_state_catalog} onChange={(next) => onChange(setField(value, "world_goal_state_catalog", next))} path="goal_resolution.world_goal_state_catalog" label="World goal state catalog" /></div>;
}

function PublicKnowledgeEditor({ value, document, onChange }: { value: JsonObject; document: JsonObject; onChange: Change }) {
  const hints = arrayOf(value.resource_source_hints);
  const update = (next: JsonObject[]) => onChange(setField(value, "resource_source_hints", next));
  return <div className="typed-specialized-editor"><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Resource source hints")}</h4><button type="button" className="small" onClick={() => update([...hints, { resource_key: "", primary_region_key: null, candidate_region_keys: [] }])}>＋ {editorLabel("Add source hint")}</button></div>{hints.map((item, index) => <ListCard key={`public_knowledge.resource_source_hints.${index}`} title="Resource source hint" onRemove={() => update(hints.filter((_, oldIndex) => oldIndex !== index))}><ReferenceField value={item.resource_key} onChange={(next) => update(hints.map((old, oldIndex) => oldIndex === index ? setField(item, "resource_key", next) : old))} path={`public_knowledge.resource_source_hints.${index}.resource_key`} label="Resource" domain="resource" document={document} /><ReferenceField value={item.primary_region_key} onChange={(next) => update(hints.map((old, oldIndex) => oldIndex === index ? setField(item, "primary_region_key", next) : old))} path={`public_knowledge.resource_source_hints.${index}.primary_region_key`} label="Primary region" domain="node" document={document} /><MultiReferenceField value={item.candidate_region_keys} onChange={(next) => update(hints.map((old, oldIndex) => oldIndex === index ? setField(item, "candidate_region_keys", next) : old))} path={`public_knowledge.resource_source_hints.${index}.candidate_region_keys`} label="Candidate regions" domain="node" document={document} /></ListCard>)}</section></div>;
}

function PublicReferenceEditor({ entity, document, onChange }: { entity: DraftObject; document: JsonObject; onChange: Change }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const type = typeof value.ref_type === "string" ? value.ref_type : "NODE";
  const domain: ReferenceDomain = type === "RESOURCE" ? "resource" : type === "DERIVED_STATE" ? "derived_state" : type === "ACTION" ? "action" : type === "ACTOR" ? "actor" : "node";
  return <div className="typed-specialized-editor"><TextField value={value.term} onChange={(next) => update("term", next)} path={`${entity.kind}.${entity.key}.term`} label="Public term" /><EnumField value={type} onChange={(next) => update("ref_type", next)} path={`${entity.kind}.${entity.key}.ref_type`} label="Reference type" choices={V2_ENUMS.publicReferenceType} /><ReferenceField value={value.ref_key} onChange={(next) => update("ref_key", next)} path={`${entity.kind}.${entity.key}.ref_key`} label="Reference key" domain={domain} document={document} /></div>;
}

function collectionRootPath(section: "initialization" | "planning" | "public-knowledge"): string {
  return section === "public-knowledge" ? "public_knowledge" : section;
}

function CollectionDetailEditor({ section, selection, index, value, document, onChange, onRemove }: { section: "initialization" | "planning" | "public-knowledge"; selection: RootCollectionSelection; index: number; value: JsonObject; document: JsonObject; onChange: Change; onRemove: () => void }) {
  const path = `${collectionRootPath(section)}.${selection.collection}.${index}`;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <article className="collection-detail-editor"><header className="collection-detail-heading"><div><p className="panel-kicker">{editorLabel("Collection item")}</p><h4>{rootCollectionLabel(selection.collection)}</h4><code>{path}</code></div><button type="button" className="small danger" onClick={onRemove}>删除此项</button></header>{selection.collection === "resource_source_hints" && <div className="typed-grid"><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="资源" domain="resource" document={document} /><ReferenceField value={value.primary_region_key} onChange={(next) => update("primary_region_key", next)} path={`${path}.primary_region_key`} label="主要区域" domain="node" document={document} /><div className="form-field-full"><MultiReferenceField value={value.candidate_region_keys} onChange={(next) => update("candidate_region_keys", next)} path={`${path}.candidate_region_keys`} label="候选区域" domain="node" document={document} /></div></div>}{selection.collection === "recovery_hints" && <div className="typed-grid"><TextField value={value.failure_code} onChange={(next) => update("failure_code", next)} path={`${path}.failure_code`} label="失败代码" /><div className="form-field-full"><TextField value={value.hint} onChange={(next) => update("hint", next)} path={`${path}.hint`} label="恢复提示" multiline /></div></div>}{selection.collection === "resource_initial_states" && <div className="typed-grid"><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="资源" domain="resource" document={document} /><ReferenceField value={value.scope_node_key} onChange={(next) => update("scope_node_key", next)} path={`${path}.scope_node_key`} label="作用域节点" domain="node" document={document} /><NumberField value={value.value} onChange={(next) => update("value", next)} path={`${path}.value`} label="数量" /><NumberField value={value.reserved_value} onChange={(next) => update("reserved_value", next)} path={`${path}.reserved_value`} label="已预留" /></div>}{selection.collection === "resource_pools" && <div className="typed-grid"><TextField value={value.pool_key} onChange={(next) => update("pool_key", next)} path={`${path}.pool_key`} label="资源池键" /><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="资源" domain="resource" document={document} /><ReferenceField value={value.region_key} onChange={(next) => update("region_key", next)} path={`${path}.region_key`} label="区域" domain="node" document={document} /><ReferenceField value={value.facility_key} onChange={(next) => update("facility_key", next)} path={`${path}.facility_key`} label="设施" domain="node" document={document} /><NumberField value={value.quantity} onChange={(next) => update("quantity", next)} path={`${path}.quantity`} label="数量" /><NumberField value={value.reserved_value} onChange={(next) => update("reserved_value", next)} path={`${path}.reserved_value`} label="已预留" /><EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={`${path}.visibility`} label="可见性" choices={V2_ENUMS.resourcePoolVisibility} /><EnumField value={value.availability} onChange={(next) => update("availability", next)} path={`${path}.availability`} label="可用性" choices={V2_ENUMS.resourceAvailability} /><BooleanField value={value.survey_discoverable} onChange={(next) => update("survey_discoverable", next)} path={`${path}.survey_discoverable`} label="可由调查发现" />{value.availability_requirement ? <KnowledgeGateEditor value={clone(value.availability_requirement)} document={document} path={`${path}.availability_requirement`} onChange={(next) => update("availability_requirement", next)} onRemove={() => update("availability_requirement", null)} /> : <button type="button" className="small" onClick={() => update("availability_requirement", { node_key: "", fact_key: "", value: true })}>＋ 添加可用性要求</button>}</div>}{selection.collection === "region_resource_knowledge" && <div className="typed-grid"><ReferenceField value={value.region_key} onChange={(next) => update("region_key", next)} path={`${path}.region_key`} label="区域" domain="node" document={document} /><EnumField value={value.resource_inventory_visibility} onChange={(next) => update("resource_inventory_visibility", next)} path={`${path}.resource_inventory_visibility`} label="资源库存可见性" choices={V2_ENUMS.resourceInventoryVisibility} /><BooleanField value={value.resource_survey_completed} onChange={(next) => update("resource_survey_completed", next)} path={`${path}.resource_survey_completed`} label="已完成资源调查" /></div>}</article>;
}

function selectedCollection(value: JsonObject, selection: RootOwnerSelection | null): { index: number; value: JsonObject } | null {
  return selection?.owner === "collection" ? rootCollectionItem(value, selection) : null;
}

function CollectionEmptyState({ label }: { label: string }) {
  return <div className="collection-empty-state"><strong>{label}</strong><p>请从左侧列表选择或新建一项。</p></div>;
}

export function MasterDetailInitializationEditor({ value, document, selection, onChange, onCollectionChange, onCollectionRemove }: { value: JsonObject; document: JsonObject; selection: RootOwnerSelection | null; onChange: Change; onCollectionChange: (value: JsonObject) => void; onCollectionRemove: () => void }) {
  const selected = selectedCollection(value, selection);
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  if (selection?.owner === "singleton") {
    return <div className="typed-specialized-editor"><section className="root-singleton-card"><div className="root-singleton-heading"><div><p className="panel-kicker">基础配置</p><h4>初始化入口</h4></div></div><div className="typed-grid"><ReferenceField value={value.start_node_key} onChange={(next) => update("start_node_key", next)} path="initialization.start_node_key" label="起始节点" domain="node" document={document} /><ReferenceField value={value.primary_actor_key} onChange={(next) => update("primary_actor_key", next)} path="initialization.primary_actor_key" label="主要参与者" domain="actor" document={document} /></div></section></div>;
  }
  return <div className="typed-specialized-editor">{selected && selection?.owner === "collection" ? <CollectionDetailEditor section="initialization" selection={selection} index={selected.index} value={selected.value} document={document} onChange={onCollectionChange} onRemove={onCollectionRemove} /> : <CollectionEmptyState label="请选择初始化配置或集合项" />}</div>;
}

export function MasterDetailPlanningEditor({ value, document, selection, onChange, onCollectionChange, onCollectionRemove }: { value: JsonObject; document: JsonObject; selection: RootOwnerSelection | null; onChange: Change; onCollectionChange: (value: JsonObject) => void; onCollectionRemove: () => void }) {
  const selected = selectedCollection(value, selection);
  if (selection?.owner === "singleton") {
    return <div className="typed-specialized-editor"><section className="root-singleton-card"><div className="root-singleton-heading"><div><p className="panel-kicker">基础配置</p><h4>规划指引</h4></div></div><StringListField value={value.instructions} onChange={(next) => onChange(setField(value, "instructions", next))} path="planning.instructions" label="规划指引" /></section></div>;
  }
  return <div className="typed-specialized-editor">{selected && selection?.owner === "collection" ? <CollectionDetailEditor section="planning" selection={selection} index={selected.index} value={selected.value} document={document} onChange={onCollectionChange} onRemove={onCollectionRemove} /> : <CollectionEmptyState label="请选择规划配置或恢复提示" />}</div>;
}

export function MasterDetailPublicKnowledgeEditor({ value, document, selection, onCollectionChange, onCollectionRemove }: { value: JsonObject; document: JsonObject; selection: RootOwnerSelection | null; onCollectionChange: (value: JsonObject) => void; onCollectionRemove: () => void }) {
  const selected = selectedCollection(value, selection);
  return <div className="typed-specialized-editor">{selected && selection?.owner === "collection" ? <CollectionDetailEditor section="public-knowledge" selection={selection} index={selected.index} value={selected.value} document={document} onChange={onCollectionChange} onRemove={onCollectionRemove} /> : <div className="collection-empty-state"><strong>请选择或新建资源发现知识</strong><p>资源来源提示用于配置可公开使用的资源位置线索。</p></div>}</div>;
}

function ActionAuthorityPolicyEditor({ value, path, onChange }: { value: unknown; path: string; onChange: (value: unknown) => void }) {
  return <AdvancedJson value={value} onChange={onChange} path={path} label="Authority policy" />;
}

function AuthoringDocumentProvider({ document, children }: { document: JsonObject; children: ReactNode }) {
  return <AuthoringDocumentContext.Provider value={document}>{children}</AuthoringDocumentContext.Provider>;
}

export function ScenarioOverviewEditor({ value, document, onChange }: { value: JsonObject; document: JsonObject; onChange: Change }) {
  const locality = clone(value.locality);
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const updateLocality = (key: string, next: unknown) => onChange(setField(value, "locality", setField(locality, key, next)));
  return <div className="typed-specialized-editor">
    <div className="typed-grid">
      <TextField value={value.key} onChange={(next) => update("key", next)} path="metadata.key" label="Scenario key" />
      <TextField value={value.name} onChange={(next) => update("name", next)} path="metadata.name" label="Scenario name" />
      <TextField value={value.description} onChange={(next) => update("description", next)} path="metadata.description" label="Description" multiline />
    </div>
    <section className="nested-list locality-contract-editor">
      <div className="typed-array-heading"><h4>{editorLabel("Locality contract")}</h4></div>
      <div className="typed-grid">
        <BooleanField value={locality.enabled} onChange={(next) => updateLocality("enabled", next)} path="metadata.locality.enabled" label="Enabled" />
        <BooleanField value={locality.scoped_resources} onChange={(next) => updateLocality("scoped_resources", next)} path="metadata.locality.scoped_resources" label="Scoped resources" />
        <ReferenceField value={locality.region_node_type_key} onChange={(next) => updateLocality("region_node_type_key", next)} path="metadata.locality.region_node_type_key" label="Region node type" domain="node_type" document={document} />
        <ReferenceField value={locality.facility_node_type_key} onChange={(next) => updateLocality("facility_node_type_key", next)} path="metadata.locality.facility_node_type_key" label="Facility node type" domain="node_type" document={document} />
        <ReferenceField value={locality.transport_node_type_key} onChange={(next) => updateLocality("transport_node_type_key", next)} path="metadata.locality.transport_node_type_key" label="Transport node type" domain="node_type" document={document} />
        <ReferenceField value={locality.located_in_relation_type_key} onChange={(next) => updateLocality("located_in_relation_type_key", next)} path="metadata.locality.located_in_relation_type_key" label="Located-in relation type" domain="relation_type" document={document} />
        <ReferenceField value={locality.transport_endpoint_relation_type_key} onChange={(next) => updateLocality("transport_endpoint_relation_type_key", next)} path="metadata.locality.transport_endpoint_relation_type_key" label="Endpoint relation type" domain="relation_type" document={document} />
        <FactKeyField value={locality.passability_fact_key} onChange={(next) => updateLocality("passability_fact_key", next)} path="metadata.locality.passability_fact_key" label="Passability fact" document={document} />
      </div>
      <p className="typed-help">局部性契约是区域、设施和交通节点族语义的唯一依据。场景发布后请保持稳定键不变。</p>
    </section>
  </div>;
}

export {
  ActionEditor,
  ActionAuthorityPolicyEditor,
  AuthoringDocumentProvider,
  DerivedStateEditor,
  GoalResolutionEditor,
  InitializationEditor,
  ObjectiveEditor,
  PlanningEditor,
  PublicKnowledgeEditor,
  PublicReferenceEditor,
  RuleEditor,
};
