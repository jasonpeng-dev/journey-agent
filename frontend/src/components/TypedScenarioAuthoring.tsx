import { useState, type ReactNode } from "react";
import { AuthoringActionButton, FieldActionRow } from "./editor/AuthoringActionButton";
import { ReferenceUsageSection } from "./editor/ReferenceUsageSection";
import type { ReferenceEdge } from "../types";

import { V2_ENUMS, type ReferenceDomain } from "../editor-registry";
import { rootCollectionItem, rootCollectionLabel, type RootCollectionSelection, type RootOwnerSelection } from "../editor-collections";

export type NestedDeleteHandler = (request: {
  parentKind: string;
  parentKey: string;
  collection: string;
  nestedKey: string;
  subject: string;
}) => void;
import { type DraftObject, type IdentityCreationIntent, type JsonObject } from "../editor";
import {
  AdvancedSection,
  BooleanControl,
  EnumSelect,
  IdentityValue,
  MultiValuePicker,
  NestedCard,
  NestedObjectHeader,
  NumberInput,
  OptionSelect,
  ReferencePicker,
  ReorderControls,
  ScalarListEditor,
  TextArea,
  TextInput,
} from "./editor/FormPrimitives";
import { referenceOptions } from "./editor/ReferencePicker";
import { IdentityCreationDialog, type IdentityCreationField, type IdentityCreationValues, type IdentityOption } from "./editor/IdentityCreationDialog";
import { ValueLabelList } from "./editor/ValueLabelEditor";
import { editorLabel, platformEnumLabel, type PlatformEnumDomain } from "../ui";
import { moveItem, type MoveDirection } from "../editor-order";
import { parseTypedScalarInput, typedScalarDisplay, typedScalarToken } from "./editor/typed-values";

type Change = (value: JsonObject, intent?: IdentityCreationIntent) => void;

function ownerDisplayLabel(value: string): string {
  const normalized = value.trim().toLocaleLowerCase().replaceAll("_", " ").replaceAll("-", " ");
  const labels: Record<string, string> = {
    "node type": "节点类型", "nodetype": "节点类型", node: "世界实体", "world entities": "世界实体", region: "区域",
    fact: "事实", "relation type": "关系类型", relation: "关系实例", role: "角色", actor: "参与者",
    interaction: "交互", action: "行动", resource: "资源定义", resources: "资源定义", "resource pool": "资源池",
    "derived state": "派生状态", initialization: "初始化", "planning recovery": "规划恢复", planning: "规划配置",
    metadata: "场景设置", "public reference": "公开术语", "action parameter": "行动参数", "action outcome": "行动结果", "action target contract": "目标信息",
  };
  return labels[normalized] ?? value;
}

function OwnerLink({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return <AuthoringActionButton className={className} intent="navigate" to={to}>{children}</AuthoringActionButton>;
}

const EFFECT_KINDS = V2_ENUMS.effectKind;
const CONDITION_KINDS = V2_ENUMS.conditionKind;

function enumDomainForChoices(choices: readonly string[]): PlatformEnumDomain | undefined {
  const domains: Array<[readonly string[], PlatformEnumDomain]> = [
    [V2_ENUMS.access, "node_access"], [V2_ENUMS.visibility, "node_knowledge"],
    [V2_ENUMS.relationVisibility, "relation_visibility"], [V2_ENUMS.resourceInventoryVisibility, "resource_inventory_visibility"],
    [V2_ENUMS.resourcePoolVisibility, "resource_pool_visibility"], [V2_ENUMS.resourceAvailability, "resource_pool_availability"],
    [V2_ENUMS.capabilities, "capability"], [V2_ENUMS.executionMode, "execution_mode"], [V2_ENUMS.behavior, "action_behavior"],
    [V2_ENUMS.locality, "locality"], [V2_ENUMS.targetKind, "target_kind"], [V2_ENUMS.parameterType, "value_type"],
    [V2_ENUMS.factType, "value_type"], [V2_ENUMS.actionTargetReference, "action_target_reference"], [V2_ENUMS.phase, "rule_phase"],
    [V2_ENUMS.trigger, "rule_trigger"], [V2_ENUMS.conditionKind, "condition_kind"], [V2_ENUMS.effectKind, "effect_kind"],
    [V2_ENUMS.comparison, "comparison"], [V2_ENUMS.valueSource, "value_source"], [V2_ENUMS.selectorKind, "selector_kind"],
    [V2_ENUMS.relationDirection, "relation_direction"], [V2_ENUMS.resourceScope, "resource_scope"],
    [V2_ENUMS.derivedDependency, "derived_dependency"], [V2_ENUMS.publicReferenceType, "public_reference_type"],
  ];
  return domains.find(([domainChoices]) => domainChoices === choices)?.[1];
}

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

function TextField({ value, onChange, path, label, multiline = false, readOnly = false, required = false, error }: { value: unknown; onChange: (value: string) => void; path: string; label: string; multiline?: boolean; readOnly?: boolean; required?: boolean; error?: ReactNode }) {
  if (label === "Source relation type") return null;
  const normalizedLabel = label.toLocaleLowerCase();
  const identityReadOnly = readOnly || ["key", "code", "稳定键", "稳定身份", "结果代码", "绑定角色"].some((token) => normalizedLabel.includes(token));
  if (identityReadOnly) return <IdentityValue value={value} path={path} label={label} />;
  return multiline ? <TextArea value={value} onChange={onChange} path={path} label={label} required={required} error={error} /> : <TextInput value={value} onChange={onChange} path={path} label={label} required={required} error={error} />;
}

function NumberField({ value, onChange, path, label, integer = true, required = false }: { value: unknown; onChange: (value: number | null) => void; path: string; label: string; integer?: boolean; required?: boolean }) {
  return <NumberInput value={value} onChange={onChange} path={path} label={label} integer={integer} required={required} />;
}

function BooleanField({ value, onChange, path, label, required = false, allowEmpty = false, onEmpty }: { value: unknown; onChange: (value: boolean) => void; path: string; label: string; required?: boolean; allowEmpty?: boolean; onEmpty?: () => void }) {
  return <BooleanControl value={value} onChange={onChange} path={path} label={label} required={required} allowEmpty={allowEmpty} onEmpty={onEmpty} />;
}

function EnumField({ value, onChange, path, label, choices, enumDomain, disabled = false, required = false, error }: { value: unknown; onChange: (value: string) => void; path: string; label: string; choices: readonly string[]; enumDomain?: PlatformEnumDomain; disabled?: boolean; required?: boolean; error?: ReactNode }) {
  return <EnumSelect value={value} onChange={onChange} path={path} label={label} choices={choices} enumDomain={enumDomain ?? enumDomainForChoices(choices)} disabled={disabled} required={required} error={error} />;
}

function referenceOwnerSection(domain: ReferenceDomain): string {
  if (domain === "node_type") return "node-types";
  if (domain === "relation_type") return "relation-types";
  if (domain === "fact" || domain === "region" || domain === "node") return "world-entities";
  if (domain === "derived_state") return "derived-states";
  if (domain === "resource_pool") return "initialization";
  return domain.replaceAll("_", "-") + "s";
}

function referenceTargetHref(domain: ReferenceDomain, key: string, scenarioId?: string): string {
  const base = scenarioId ? `/scenarios/${scenarioId}/edit` : "..";
  if (domain === "fact") {
    const [nodeKey, factKey] = key.split(".");
    return nodeKey && factKey ? `${base}/world-entities/${encodeURIComponent(nodeKey)}?focus_path=${encodeURIComponent(`facts.${factKey}`)}` : `${base}/world-entities`;
  }
  if (domain === "resource_pool") {
    const query = new URLSearchParams({ domain: "resources", group: "resource-pools", item: `pool:${key}` });
    return `${base}/initialization?${query}`;
  }
  const section = domain === "region" ? "world-entities" : referenceOwnerSection(domain);
  return `${base}/${section}/${encodeURIComponent(key)}${domain === "relation" ? "?kind=relation" : ""}`;
}

function ReferenceField({ value, onChange, path, label, domain, document, readOnly = false, help, required = false }: { value: unknown; onChange: (value: string) => void; path: string; label: string; domain: ReferenceDomain; document: JsonObject; readOnly?: boolean; help?: ReactNode; required?: boolean }) {
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const ownerSection = referenceOwnerSection(domain);
  const selected = typeof value === "string" ? value : "";
  let targetHref: string | undefined;
  if (scenarioId && selected && referenceOptions(document, domain).some((option) => option.key === selected)) {
    if (domain === "fact") {
      const [nodeKey, factKey] = selected.split(".");
      if (nodeKey && factKey) targetHref = `/scenarios/${scenarioId}/edit/world-entities/${encodeURIComponent(nodeKey)}?focus_path=${encodeURIComponent(`facts.${factKey}`)}`;
    } else if (domain === "resource_pool") {
      const query = new URLSearchParams({ domain: "resources", group: "resource-pools", item: `pool:${selected}` });
      targetHref = `/scenarios/${scenarioId}/edit/initialization?${query}`;
    } else {
      targetHref = `/scenarios/${scenarioId}/edit/${domain === "region" ? "world-entities" : ownerSection}/${encodeURIComponent(selected)}${domain === "relation" ? "?kind=relation" : ""}`;
    }
  }
  const ownerHref = scenarioId ? `/scenarios/${scenarioId}/edit/${ownerSection}` : `../${ownerSection}`;
  const headingAddon = <OwnerLink className="field-owner-link" to={targetHref ?? ownerHref}>{targetHref ? "前往所选对象" : "前往" + (domain === "fact" ? "事实" : domain === "node_type" ? "节点类型" : domain === "relation_type" ? "关系类型" : domain === "resource_pool" ? "资源池" : domain.replaceAll("_", " "))}</OwnerLink>;
  if (readOnly) return <IdentityValue value={value} path={path} label={label} />;
  return <ReferencePicker value={value} onChange={onChange} path={path} label={label} domain={domain} document={document} help={help} required={required} headingAddon={headingAddon} />;
}
function MultiReferenceField({ value, onChange, path, label, domain, document, required = false }: { value: unknown; onChange: (value: string[]) => void; path: string; label: string; domain: ReferenceDomain; document: JsonObject; required?: boolean }) {
  const isCapabilitySet = path.endsWith("allowed_actor_capabilities");
  const options = isCapabilitySet
    ? V2_ENUMS.capabilities.map((item) => ({ key: item, name: platformEnumLabel("capability", item) }))
    : referenceOptions(document, domain);
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const ownerHref = scenarioId ? `/scenarios/${scenarioId}/edit/${referenceOwnerSection(domain)}` : `../${referenceOwnerSection(domain)}`;
  const selected = stringsOf(value);
  const ownerLabel = domain === "interaction" ? "交互" : domain === "action" ? "行动" : domain === "node_type" ? "节点类型" : domain === "relation_type" ? "关系类型" : domain === "role" ? "角色" : domain === "resource" ? "资源定义" : domain === "fact" ? "事实" : domain === "node" || domain === "region" ? "世界实体" : ownerDisplayLabel(domain);
  const headingAddon = !isCapabilitySet && <span className="field-owner-links">
    {selected.filter((key) => options.some((option) => option.key === key)).map((key) => <OwnerLink className="field-owner-link" key={key} to={referenceTargetHref(domain, key, scenarioId)}>前往{options.find((option) => option.key === key)?.name ?? key}</OwnerLink>)}
    <OwnerLink className="field-owner-link" to={ownerHref}>前往{ownerLabel}</OwnerLink>
  </span>;
  return <MultiValuePicker value={value} onChange={onChange} path={path} label={label} options={options} required={required} headingAddon={headingAddon} />;
}

function actionTargetOptions(document: JsonObject, action: JsonObject | undefined): IdentityOption[] {
  const targetKind = action?.target_kind === "ACTOR" ? "ACTOR" : "NODE";
  const domain: ReferenceDomain = targetKind === "ACTOR" ? "actor" : "node";
  const options = referenceOptions(document, domain);
  if (targetKind === "ACTOR") {
    const actionKey = typeof action?.key === "string" ? action.key : "";
    const actors = arrayOf(
      document.actors && typeof document.actors === "object" && !Array.isArray(document.actors)
        ? (document.actors as JsonObject).actor_profiles
        : [],
    );
    const eligible = new Set(
      actors
        .filter((actor) => actionKey && stringsOf(actor.allowed_action_keys).includes(actionKey))
        .map((actor) => String(actor.key ?? "")),
    );
    return options.filter((option) => eligible.has(option.key));
  }
  const world = document.world && typeof document.world === "object" && !Array.isArray(document.world) ? document.world as JsonObject : {};
  const nodes = arrayOf(world.nodes);
  const allowedTypes = stringsOf(action?.target_node_type_keys);
  const interaction = typeof action?.required_interaction_key === "string" ? action.required_interaction_key : undefined;
  const eligible = new Set(nodes.filter((node) =>
    typeof node.key === "string"
    && (!allowedTypes.length || allowedTypes.includes(String(node.node_type_key ?? "")))
    && (!interaction || stringsOf(node.interaction_keys).includes(interaction))
  ).map((node) => String(node.key)));
  return options.filter((option) => eligible.has(option.key));
}

function ScalarField({ value, onChange, path, label, required = false, valueType, allowedValues = [], valueLabels = [] }: { value: unknown; onChange: (value: string | number | boolean | null) => void; path: string; label: string; required?: boolean; valueType?: unknown; allowedValues?: unknown; valueLabels?: unknown }) {
  if (valueType === "BOOLEAN") return <BooleanControl value={typeof value === "boolean" ? value : null} onChange={onChange as (value: boolean) => void} onEmpty={() => onChange(null)} allowEmpty path={path} label={label} required={required} />;
  if (valueType === "INTEGER") return <NumberInput value={value} onChange={onChange as (value: number | null) => void} path={path} label={label} required={required} />;
  if (valueType === "ENUM") {
    const values = Array.isArray(allowedValues) ? allowedValues.filter((candidate): candidate is string | number | boolean => typeof candidate === "string" || typeof candidate === "number" || typeof candidate === "boolean") : [];
    const options = values.map((candidate) => ({ key: typedScalarToken(candidate), name: typedScalarDisplay(candidate, { value_type: valueType, allowed_values: values, value_labels: valueLabels }) }));
    const selected = values.find((candidate) => Object.is(candidate, value));
    return <OptionSelect value={selected === undefined ? "" : typedScalarToken(selected)} onChange={(token) => {
      const parsed = parseTypedScalarInput(token, "ENUM", values);
      if (parsed !== null) onChange(parsed);
    }} path={path} label={label} options={options} required placeholder="请选择…" showMachineValue={false} />;
  }
  if (typeof value === "boolean") return <BooleanControl value={value} onChange={onChange as (value: boolean) => void} onEmpty={() => onChange(null)} allowEmpty={required} path={path} label={label} required={required} />;
  if (typeof value === "number") return <NumberField value={value} onChange={onChange as (value: number | null) => void} path={path} label={label} required={required} />;
  return <TextField value={value} onChange={onChange as (value: string) => void} path={path} label={label} required={required} />;
}

function ScalarListField({ value, onChange, path, label, required = false }: { value: unknown; onChange: (value: Array<string | number | boolean>) => void; path: string; label: string; required?: boolean }) {
  return <ScalarListEditor value={value} onChange={onChange} path={path} label={label} required={required} minItems={required ? 1 : 0} />;
}

function FactValueListField({ value, onChange, path, label, required, definition }: { value: unknown; onChange: (value: unknown[]) => void; path: string; label: string; required?: boolean; definition?: FactValueDefinition }) {
  const items = Array.isArray(value) ? value : [];
  const listError = required && items.length === 0 ? "请至少添加一个值。" : undefined;
  return <section className={`typed-array scalar-list-editor${listError ? " form-field-error" : ""}`} aria-label={label} data-field-path={path}>
    <div className="typed-array-heading"><h4>{label}{required && <> <span className="required-marker">*</span></>}</h4><button type="button" className="small" onClick={() => onChange([...items, null])}>＋ 添加值</button></div>
    {items.length === 0 && <p className="muted">暂无内容。</p>}
    {items.map((item, index) => <div className="typed-array-row" key={`${path}.${index}`}>
      <ScalarField value={item} onChange={(next) => onChange(items.map((old, oldIndex) => oldIndex === index ? next : old))} path={`${path}.${index}`} label={`${label} ${index + 1}`} required valueType={definition?.value_type} allowedValues={definition?.allowed_values} valueLabels={definition?.value_labels} />
      <span className="typed-array-actions"><ReorderControls index={index} count={items.length} onMove={(direction) => onChange(moveItem(items, index, direction))} /><button type="button" className="small danger" onClick={() => onChange(items.filter((_, oldIndex) => oldIndex !== index))}>移除</button></span>
    </div>)}
    {listError && <small className="field-error" role="alert">{listError}</small>}
  </section>;
}

function StringListField({ value, onChange, path, label, help, required = false }: { value: unknown; onChange: (value: string[]) => void; path: string; label: string; help?: ReactNode; required?: boolean }) {
  return <ScalarListEditor value={stringsOf(value)} onChange={(next) => onChange(next.filter((item): item is string => typeof item === "string"))} path={path} label={label} help={help} required={required} minItems={required ? 1 : 0} />;
}

function AdvancedJson({ value, onChange, path, label }: { value: unknown; onChange: (value: unknown) => void; path: string; label: string }) {
  return <AdvancedSection value={value} onChange={onChange} path={path} label={label} />;
}

function ListCard({ title, summary, machineKey, children, onAdd, onRemove, onMove, moveIndex, moveCount, defaultExpanded = false, focusPath, expanded, onExpandedChange }: { title: string; summary?: ReactNode; machineKey?: string; children: ReactNode; onAdd?: () => void; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number; defaultExpanded?: boolean; focusPath?: string; expanded?: boolean; onExpandedChange?: (expanded: boolean) => void }) {
  return <NestedCard title={title} summary={summary ?? "展开编辑"} machineKey={machineKey} onAdd={onAdd} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount} defaultExpanded={defaultExpanded} focusPath={focusPath} expanded={expanded} onExpandedChange={onExpandedChange}>{children}</NestedCard>;
}

function NodeSelectorEditor({ value, document, path, onChange, required = false }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; required?: boolean }) {
  const kind = typeof value.kind === "string" ? value.kind : "CURRENT_TARGET";
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <div className="typed-grid"><EnumField value={kind} onChange={(next) => { const base = defaultNodeSelector(next); onChange({ ...base, ...(next === kind ? value : {}) }); }} path={`${path}.kind`} label="Selector kind" choices={V2_ENUMS.selectorKind} required={required} error={required && typeof value.kind !== "string" ? "请选择节点选择器。" : undefined} />{kind === "EXPLICIT" && <ReferenceField value={value.node_key} onChange={(next) => update("node_key", next)} path={`${path}.node_key`} label="Node" domain="node" document={document} required />}{kind === "RELATED" && <><ReferenceField value={value.relation_type_key} onChange={(next) => update("relation_type_key", next)} path={`${path}.relation_type_key`} label="Relation type" domain="relation_type" document={document} required /><EnumField value={value.direction} onChange={(next) => update("direction", next)} path={`${path}.direction`} label="Direction" choices={V2_ENUMS.relationDirection} required /><ReferenceField value={value.anchor_node_key} onChange={(next) => update("anchor_node_key", next)} path={`${path}.anchor_node_key`} label="Anchor node" domain="node" document={document} required /><FactKeyField value={value.required_fact_key} onChange={(next) => update("required_fact_key", next)} path={`${path}.required_fact_key`} label="Required fact" document={document} nodeKey={typeof value.anchor_node_key === "string" ? value.anchor_node_key : undefined} /></>}</div>;
}

function FactKeyField({ value, onChange, path, label, document, nodeKey, required = false }: { value: unknown; onChange: (value: string) => void; path: string; label: string; document: JsonObject; nodeKey?: string; required?: boolean }) {
  const all = referenceOptions(document, "fact");
  const options = nodeKey ? all.filter((item) => item.key.startsWith(`${nodeKey}.`)).map((item) => ({ ...item, key: item.key.slice(nodeKey.length + 1) })) : all.map((item) => ({ ...item, key: item.key.split(".").slice(-1)[0] }));
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const selectedFact = typeof value === "string" && value ? nodeKey ? `${nodeKey}.${value}` : value.includes(".") ? value : "" : "";
  const [targetNode, targetFact] = selectedFact.split(".");
  const targetNodeExists = all.some((item) => item.key === selectedFact);
  const targetHref = scenarioId && targetNode && targetFact && targetNodeExists ? `/scenarios/${scenarioId}/edit/world-entities/${encodeURIComponent(targetNode)}?focus_path=${encodeURIComponent(`facts.${targetFact}`)}` : undefined;
  const ownerHref = scenarioId ? `/scenarios/${scenarioId}/edit/world-entities` : "../world-entities";
  return <OptionSelect value={value} onChange={onChange} path={path} label={label} options={options} placeholder="请选择事实" required={required} error={required && !(typeof value === "string" && value.trim()) ? "此字段为必填项。" : undefined} headingAddon={<OwnerLink className="field-owner-link" to={targetHref ?? ownerHref}>{targetHref ? "前往所选事实" : "前往事实定义"}</OwnerLink>} />;
}

type FactValueDefinition = { value_type: string; allowed_values?: unknown; value_labels?: unknown };

function factValueDefinition(document: JsonObject, factKey: unknown, nodeSelector: JsonObject): FactValueDefinition | undefined {
  if (typeof factKey !== "string" || !factKey) return undefined;
  const world = document.world && typeof document.world === "object" && !Array.isArray(document.world)
    ? document.world as JsonObject
    : {};
  const nodes = arrayOf(world.nodes);
  const definitionFor = (node: JsonObject | undefined): FactValueDefinition | undefined => {
    const fact = arrayOf(node?.facts).find((candidate) => candidate.key === factKey);
    return fact && typeof fact.value_type === "string"
      ? { value_type: fact.value_type, allowed_values: fact.allowed_values, value_labels: fact.value_labels }
      : undefined;
  };

  if (nodeSelector.kind === "EXPLICIT" && typeof nodeSelector.node_key === "string") {
    return definitionFor(nodes.find((node) => node.key === nodeSelector.node_key));
  }

  // Dynamic selectors may use a typed control when every Fact with this key
  // has the same authored value contract. Otherwise stay conservative.
  const definitions = nodes.map(definitionFor).filter((definition): definition is FactValueDefinition => Boolean(definition));
  if (definitions.length === 0) return undefined;
  const signature = (definition: FactValueDefinition) => JSON.stringify([
    definition.value_type,
    definition.allowed_values ?? null,
    definition.value_labels ?? null,
  ]);
  return definitions.every((definition) => signature(definition) === signature(definitions[0])) ? definitions[0] : undefined;
}

function ValueExpressionEditor({ value, document, factKey, nodeSelector, path, onChange }: { value: unknown; document: JsonObject; factKey: unknown; nodeSelector: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const current = clone(value);
  const source = typeof current.source === "string" ? current.source : "LITERAL";
  const definition = factValueDefinition(document, factKey, nodeSelector);
  return <div className="typed-grid"><EnumField value={source} onChange={(next) => onChange(next === "LITERAL" ? { source: next, ...(current.literal === undefined ? {} : { literal: current.literal }) } : { source: next, parameter_key: current.parameter_key ?? "" })} path={`${path}.source`} label="Value source" choices={V2_ENUMS.valueSource} required error={typeof current.source === "string" ? undefined : "请选择值来源。"} />{source === "LITERAL" ? <ScalarField value={current.literal} onChange={(next) => onChange(setField(current, "literal", next))} path={`${path}.literal`} label="Literal" required valueType={definition?.value_type} allowedValues={definition?.allowed_values} valueLabels={definition?.value_labels} /> : <TextField value={current.parameter_key} onChange={(next) => onChange(setField(current, "parameter_key", next))} path={`${path}.parameter_key`} label="Parameter key" required />}</div>;
}

function IntegerExpressionEditor({ value, path, onChange }: { value: unknown; path: string; onChange: (value: JsonObject) => void }) {
  const current = clone(value);
  const source = typeof current.source === "string" ? current.source : "LITERAL";
  return <div className="typed-grid"><EnumField value={source} onChange={(next) => onChange(next === "LITERAL" ? { source: next, literal: current.literal ?? 1, multiplier: current.multiplier ?? 1 } : { source: next, parameter_key: current.parameter_key ?? "", multiplier: current.multiplier ?? 1 })} path={`${path}.source`} label="Amount source" choices={V2_ENUMS.valueSource} required error={typeof current.source === "string" ? undefined : "请选择数量来源。"} />{source === "LITERAL" ? <NumberField value={current.literal} onChange={(next) => onChange(setField(current, "literal", next))} path={`${path}.literal`} label="Literal" required /> : <TextField value={current.parameter_key} onChange={(next) => onChange(setField(current, "parameter_key", next))} path={`${path}.parameter_key`} label="Parameter key" required />}<NumberField value={current.multiplier} onChange={(next) => onChange(setField(current, "multiplier", next))} path={`${path}.multiplier`} label="Multiplier" /></div>;
}

function ResourceScopeEditor({ value, document, path, onChange }: { value: unknown; document: JsonObject; path: string; onChange: (value: JsonObject) => void }) {
  const current = clone(value);
  const kind = typeof current.kind === "string" ? current.kind : "EXPLICIT";
  const configured = Object.keys(current).length > 0;
  return <div className="typed-grid"><EnumField value={kind} onChange={(next) => onChange(next === "EXPLICIT" ? { kind: next, node_key: current.node_key ?? "" } : { kind: next })} path={`${path}.kind`} label="Resource scope" choices={V2_ENUMS.resourceScope} required={configured} error={configured && typeof current.kind !== "string" ? "请选择资源作用域。" : undefined} />{kind === "EXPLICIT" && <ReferenceField value={current.node_key} onChange={(next) => onChange(setField(current, "node_key", next))} path={`${path}.node_key`} label="Scope node" domain="node" document={document} required={configured} />}</div>;
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

function LegacyConditionEditor({ value, document, path, onChange, onRemove, onMove, moveIndex, moveCount }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number }) {
  const kind = typeof value.kind === "string" ? value.kind : "FACT_EQUALS";
  if (!(CONDITION_KINDS as readonly string[]).includes(kind)) {
    return <ListCard title={`未知条件 · ${kind}`} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}><AdvancedJson value={value} onChange={(next) => onChange(clone(next))} path={path} label="未知条件 JSON" /></ListCard>;
  }
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const children = arrayOf(value.conditions);
  const nodeSelectorKinds = ["FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE", "NODE_VISIBLE", "NODE_ACCESSIBLE", "RELATION_EXISTS"];
  const factKinds = ["FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE"];
  const comparisonKinds = ["FACT_COMPARE", "RESOURCE_COMPARE", "PARAMETER_COMPARE"];
  const valueKinds = ["FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_COMPARE", "RESOURCE_COMPARE", "PARAMETER_COMPARE"];
  const factDefinition = factKinds.includes(kind) ? factValueDefinition(document, value.fact_key, clone(value.node)) : undefined;
  return <ListCard title={`条件 · ${platformEnumLabel("condition_kind", kind)}`} summary={cardSummary(value.fact_key, value.resource_key, value.parameter_key, value.operator)} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}>
    <EnumField value={kind} onChange={(next) => onChange({ ...defaultCondition(next), key: value.key })} path={`${path}.kind`} label="Condition kind" choices={CONDITION_KINDS} required error={typeof value.kind === "string" ? undefined : "请选择条件类型。"} />
    {(kind === "ALL" || kind === "ANY") && <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Child conditions")} <span className="required-marker">*</span></h4>{children.length === 0 && <p className="field-error" role="alert">至少需要一个子条件。</p>}<button type="button" className="small" onClick={() => onChange(setField(value, "conditions", [...children, defaultCondition("FACT_EQUALS")]))}>＋ 添加条件</button></div>{children.map((child, index) => <ConditionEditor key={`${path}.conditions.${index}`} value={child} document={document} path={`${path}.conditions.${index}`} onChange={(next) => onChange(setField(value, "conditions", children.map((old, oldIndex) => oldIndex === index ? next : old)))} onRemove={() => onChange(setField(value, "conditions", children.filter((_, oldIndex) => oldIndex !== index)))} onMove={(direction) => onChange(setField(value, "conditions", moveItem(children, index, direction)))} moveIndex={index} moveCount={children.length} />)}</section>}
    {kind === "NOT" && <section className="nested-list"><h4>{editorLabel("Child condition")} <span className="required-marker">*</span></h4>{!value.condition && <p className="field-error" role="alert">需要一个子条件。</p>}<ConditionEditor value={clone(value.condition)} document={document} path={`${path}.condition`} onChange={(next) => update("condition", next)} /></section>}
    {nodeSelectorKinds.includes(kind) && <NodeSelectorEditor value={clone(value.node)} document={document} path={`${path}.node`} onChange={(next) => update("node", next)} required />}
    {factKinds.includes(kind) && <FactKeyField value={value.fact_key} onChange={(next) => update("fact_key", next)} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node === "object" && value.node && (value.node as JsonObject).kind === "EXPLICIT" ? String((value.node as JsonObject).node_key ?? "") : undefined} required />}
    {comparisonKinds.includes(kind) && <EnumField value={value.operator} onChange={(next) => update("operator", next)} path={`${path}.operator`} label="Operator" choices={V2_ENUMS.comparison} required />}
    {kind === "FACT_IN" && <FactValueListField value={value.values} onChange={(next) => update("values", next)} path={`${path}.values`} label="Accepted values" required definition={factDefinition} />}
    {valueKinds.includes(kind) && <ScalarField value={value.value} onChange={(next) => update("value", next)} path={`${path}.value`} label="值" required {...(factKinds.includes(kind) ? { valueType: factDefinition?.value_type, allowedValues: factDefinition?.allowed_values, valueLabels: factDefinition?.value_labels } : {})} />}
    {kind === "RESOURCE_COMPARE" && <><ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={`${path}.resource_key`} label="Resource" domain="resource" document={document} required /><ResourceScopeEditor value={value.resource_scope} document={document} path={`${path}.resource_scope`} onChange={(next) => update("resource_scope", next)} /></>}
    {kind === "PARAMETER_COMPARE" && <TextField value={value.parameter_key} onChange={(next) => update("parameter_key", next)} path={`${path}.parameter_key`} label="Parameter key" required />}
    {kind === "NODE_VISIBLE" && <EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={`${path}.visibility`} label="Visibility" choices={V2_ENUMS.visibility} required />}
    {kind === "NODE_ACCESSIBLE" && <EnumField value={value.access} onChange={(next) => update("access", next)} path={`${path}.access`} label="Access" choices={V2_ENUMS.access} required />}
    {kind === "RELATION_EXISTS" && <><ReferenceField value={value.relation_type_key} onChange={(next) => update("relation_type_key", next)} path={`${path}.relation_type_key`} label="Relation type" domain="relation_type" document={document} required /><EnumField value={value.relation_direction} onChange={(next) => update("relation_direction", next)} path={`${path}.relation_direction`} label="Relation direction" choices={V2_ENUMS.relationDirection} required /></>}
  </ListCard>;
}

function ConditionEditor({ value, document, path, onChange, onRemove, onMove, moveIndex, moveCount }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number }) {
  if (value.kind !== "RELATION_EXISTS") return <LegacyConditionEditor value={value} document={document} path={path} onChange={onChange} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount} />;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <ListCard title={`条件 · ${platformEnumLabel("condition_kind", "RELATION_EXISTS")}`} summary={cardSummary(value.relation_type_key, value.relation_direction)} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}>
    <NodeSelectorEditor value={clone(value.node)} document={document} path={`${path}.node`} onChange={(next) => update("node", next)} required />
    <ReferenceField value={value.relation_type_key} onChange={(next) => update("relation_type_key", next)} path={`${path}.relation_type_key`} label="Relation type" domain="relation_type" document={document} required />
    <EnumField value={value.relation_direction} onChange={(next) => update("relation_direction", next)} path={`${path}.relation_direction`} label="Relation direction" choices={V2_ENUMS.relationDirection} required />
  </ListCard>;
}

function defaultEffect(kind: string): JsonObject {
  if (kind === "SET_FACT") return { kind, node: { kind: "CURRENT_TARGET" }, fact_key: "", value: { source: "LITERAL", literal: true } };
  if (["REVEAL_FACT", "HIDE_FACT"].includes(kind)) return { kind, node: { kind: "CURRENT_TARGET" }, fact_key: "" };
  if (["REVEAL_NODE", "HIDE_NODE"].includes(kind)) return { kind, node: { kind: "CURRENT_TARGET" } };
  if (kind === "SET_NODE_ACCESS") return { kind, node: { kind: "CURRENT_TARGET" }, access: "AVAILABLE" };
  if (["ADJUST_RESOURCE", "RESERVE_RESOURCE", "RELEASE_RESOURCE"].includes(kind)) return { kind, resource_key: "", resource_scope: { kind: "ACTOR_CURRENT_REGION" }, amount: { source: "LITERAL", literal: 1, multiplier: 1 } };
  if (kind === "EMIT_FAILURE") return { kind, failure_code: "", message: "", retryable: false };
  if (kind === "EMIT_OUTCOME") return { kind, outcome_code: "", retryable: false };
  if (kind === "WRITE_MEMORY_EVENT") return { kind, memory_key: "", memory_content: "" };
  if (kind === "SET_ACTOR_COMMAND_REACHABILITY") return { kind, actor_key: "", command_reachability: "ONLINE" };
  if (kind === "SET_RELATION_VISIBILITY") return { kind, relation_key: "", visibility: "VISIBLE" };
  if (kind === "SET_REGION_RESOURCE_VISIBILITY") return { kind, region_key: "", visibility: "VISIBLE" };
  if (kind === "SET_RESOURCE_POOL_VISIBILITY") return { kind, pool_key: "", visibility: "VISIBLE" };
  if (kind === "SET_RESOURCE_POOL_AVAILABILITY") return { kind, pool_key: "", availability: "AVAILABLE" };
  return { kind };
}

function EffectEditor({ value, document, path, onChange, outcomeOptions, outcomeOwnerHref, onRemove, onMove, moveIndex, moveCount }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; outcomeOptions: IdentityOption[]; outcomeOwnerHref?: string; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number }) {
  const kind = typeof value.kind === "string" ? value.kind : "EMIT_OUTCOME";
  if (!(EFFECT_KINDS as readonly string[]).includes(kind)) {
    return <ListCard title={`未知效果 · ${kind}`} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}><AdvancedJson value={value} onChange={(next) => onChange(clone(next))} path={path} label="未知效果 JSON" /></ListCard>;
  }
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const factKinds = ["SET_FACT", "REVEAL_FACT", "HIDE_FACT"];
  const nodeKinds = ["REVEAL_NODE", "HIDE_NODE", "SET_NODE_ACCESS"];
  const resourceKinds = ["ADJUST_RESOURCE", "RESERVE_RESOURCE", "RELEASE_RESOURCE"];
  const ruleKey = path.split(".")[1];
  const ownerRule = arrayOf(document.rules).find((item) => item.key === ruleKey);
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const inferredOutcomeOwnerHref = scenarioId && typeof ownerRule?.action_key === "string"
    ? `/scenarios/${scenarioId}/edit/actions/${encodeURIComponent(ownerRule.action_key)}`
    : scenarioId ? `/scenarios/${scenarioId}/edit/actions` : "../actions";
  return <ListCard title={`效果 · ${platformEnumLabel("effect_kind", kind)}`} summary={cardSummary(value.fact_key, value.resource_key, value.outcome_code, value.failure_code)} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}>
    <EnumField value={kind} onChange={(next) => onChange({ ...defaultEffect(next), key: value.key })} path={path + ".kind"} label="Effect kind" choices={EFFECT_KINDS} required />
    {factKinds.includes(kind) && <>
      <NodeSelectorEditor value={clone(value.node)} document={document} path={path + ".node"} onChange={(next) => update("node", next)} required />
      <FactKeyField value={value.fact_key} onChange={(next) => update("fact_key", next)} path={path + ".fact_key"} label="Fact" document={document} required />
    </>}
    {kind === "SET_FACT" && <ValueExpressionEditor value={value.value} document={document} factKey={value.fact_key} nodeSelector={clone(value.node)} path={path + ".value"} onChange={(next) => update("value", next)} />}
    {nodeKinds.includes(kind) && <NodeSelectorEditor value={clone(value.node)} document={document} path={path + ".node"} onChange={(next) => update("node", next)} required />}
    {kind === "SET_NODE_ACCESS" && <EnumField value={value.access} onChange={(next) => update("access", next)} path={path + ".access"} label="Access" choices={V2_ENUMS.access} required />}
    {resourceKinds.includes(kind) && <>
      <ReferenceField value={value.resource_key} onChange={(next) => update("resource_key", next)} path={path + ".resource_key"} label="Resource" domain="resource" document={document} required />
      <ResourceScopeEditor value={value.resource_scope} document={document} path={path + ".resource_scope"} onChange={(next) => update("resource_scope", next)} />
      <IntegerExpressionEditor value={value.amount} path={path + ".amount"} onChange={(next) => update("amount", next)} />
    </>}
    {kind === "EMIT_OUTCOME" && <OptionSelect value={value.outcome_code} onChange={(next) => update("outcome_code", next)} options={outcomeOptions} path={path + ".outcome_code"} label="Expected outcome" placeholder="请选择已有结果" required headingAddon={<OwnerLink className="field-owner-link" to={outcomeOwnerHref ?? inferredOutcomeOwnerHref}>前往行动结果</OwnerLink>} />}
    {kind === "EMIT_FAILURE" && <>
      <TextField value={value.failure_code} onChange={(next) => update("failure_code", next)} path={path + ".failure_code"} label="Failure code" required />
      <TextField value={value.message} onChange={(next) => update("message", next)} path={path + ".message"} label="Message" multiline required />
    </>}
    {["EMIT_OUTCOME", "EMIT_FAILURE"].includes(kind) && <BooleanField value={value.retryable} onChange={(next) => update("retryable", next)} path={path + ".retryable"} label="Retryable" />}
    {kind === "WRITE_MEMORY_EVENT" && <>
      <TextField value={value.memory_key} onChange={(next) => update("memory_key", next)} path={path + ".memory_key"} label="Memory key" required />
      <TextField value={value.memory_content} onChange={(next) => update("memory_content", next)} path={path + ".memory_content"} label="Memory content" multiline required />
    </>}
    {kind === "SET_ACTOR_COMMAND_REACHABILITY" && <>
      <ReferenceField value={value.actor_key} onChange={(next) => update("actor_key", next)} path={path + ".actor_key"} label="Actor" domain="actor" document={document} />
      <EnumField value={value.command_reachability} onChange={(next) => update("command_reachability", next)} path={path + ".command_reachability"} label="Reachability" choices={["ONLINE", "DISCONNECTED"]} enumDomain="command_reachability" required />
    </>}
    {kind === "SET_RELATION_VISIBILITY" && <>
      <ReferenceField value={value.relation_key} onChange={(next) => update("relation_key", next)} path={path + ".relation_key"} label="Relation" domain="relation" document={document} required />
      <EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={path + ".visibility"} label="Visibility" choices={V2_ENUMS.relationVisibility} required />
    </>}
    {kind === "SET_REGION_RESOURCE_VISIBILITY" && <>
      <ReferenceField value={value.region_key} onChange={(next) => update("region_key", next)} path={path + ".region_key"} label="Region" domain="region" document={document} required />
      <EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={path + ".visibility"} label="Inventory visibility" choices={V2_ENUMS.resourceInventoryVisibility} required />
    </>}
    {kind === "SET_RESOURCE_POOL_VISIBILITY" && <>
      <ReferenceField value={value.pool_key} onChange={(next) => update("pool_key", next)} path={path + ".pool_key"} label="Resource pool" domain="resource_pool" document={document} required />
      <EnumField value={value.visibility} onChange={(next) => update("visibility", next)} path={path + ".visibility"} label="Pool visibility" choices={V2_ENUMS.resourcePoolVisibility} required />
    </>}
    {kind === "SET_RESOURCE_POOL_AVAILABILITY" && <>
      <ReferenceField value={value.pool_key} onChange={(next) => update("pool_key", next)} path={path + ".pool_key"} label="Resource pool" domain="resource_pool" document={document} required />
      <EnumField value={value.availability} onChange={(next) => update("availability", next)} path={path + ".availability"} label="Availability" choices={V2_ENUMS.resourceAvailability} required />
    </>}
    {kind === "REVEAL_TARGET_REGION_FACILITY_FACTS" && <p className="typed-help">此效果没有额外字段；其运行时语义由 V2 引擎契约定义。</p>}
  </ListCard>;
}
function FactReferenceEditor({ value, document, path, onChange, onRemove, onMove, moveIndex, moveCount }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number }) {
  return <ListCard title="事实引用" summary={cardSummary(value.node_key, value.fact_key)} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}><ReferenceField value={value.node_key} onChange={(next) => onChange(setField(value, "node_key", next))} path={`${path}.node_key`} label="Node" domain="node" document={document} required /><FactKeyField value={value.fact_key} onChange={(next) => onChange(setField(value, "fact_key", next))} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} required /></ListCard>;
}

function KnowledgeGateEditor({ value, document, path, onChange, onRemove }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; onRemove?: () => void }) {
  if (path.endsWith("availability_requirement")) {
    return <ListCard title="资源可用性要求" summary={cardSummary(value.node_key, value.fact_key, value.value)} onRemove={onRemove}><ReferenceField value={value.node_key} onChange={(next) => onChange(setField(value, "node_key", next))} path={`${path}.node_key`} label="Node" domain="node" document={document} required /><FactKeyField value={value.fact_key} onChange={(next) => onChange(setField(value, "fact_key", next))} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} required /><ScalarField value={value.value} onChange={(next) => onChange(setField(value, "value", next))} path={`${path}.value`} label="Required value" required /></ListCard>;
  }
  return <ListCard title="知识门槛" summary={cardSummary(value.node_key, value.fact_key)} onRemove={onRemove}><ReferenceField value={value.node_key} onChange={(next) => onChange(setField(value, "node_key", next))} path={`${path}.node_key`} label="Node" domain="node" document={document} required /><FactKeyField value={value.fact_key} onChange={(next) => onChange(setField(value, "fact_key", next))} path={`${path}.fact_key`} label="Fact" document={document} nodeKey={typeof value.node_key === "string" ? value.node_key : undefined} required /><ScalarListField value={value.accepted_values} onChange={(next) => onChange(setField(value, "accepted_values", next))} path={`${path}.accepted_values`} label="Accepted values" required /></ListCard>;
}

function ActionPlanningEditor({ value, document, path, onChange, outcomeOptions }: { value: JsonObject; document: JsonObject; path: string; onChange: Change; outcomeOptions: IdentityOption[] }) {
  const current = clone(value);
  const actionKey = path.split(".")[1] ?? "";
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const outcomesOwnerHref = scenarioId && actionKey
    ? `/scenarios/${scenarioId}/edit/actions/${encodeURIComponent(actionKey)}?focus_path=expected_outcomes`
    : "../actions";
  const outcomeNavigation = (value: unknown) => {
    const selected = stringsOf(value);
    return <span className="field-owner-links">
      {selected.filter((code) => outcomeOptions.some((option) => option.key === code)).map((code) => {
        const targetHref = scenarioId && actionKey
          ? `/scenarios/${scenarioId}/edit/actions/${encodeURIComponent(actionKey)}?focus_path=${encodeURIComponent(`expected_outcomes.${code}`)}`
          : "../actions";
        const label = outcomeOptions.find((option) => option.key === code)?.name ?? code;
        return <OwnerLink className="field-owner-link" key={code} to={targetHref}>前往{label}</OwnerLink>;
      })}
      <OwnerLink className="field-owner-link" to={outcomesOwnerHref}>前往行动结果</OwnerLink>
    </span>;
  };
  const lists = [
    { key: "terminal_effects", title: "Terminal Fact effects", items: arrayOf(current.terminal_effects), create: { node_key: "", fact_key: "" } },
    { key: "target_terminal_effects", title: "Target-relative Fact effects", items: arrayOf(current.target_terminal_effects), create: { fact_key: "", value: true } },
    { key: "supporting_effects", title: "Supporting Fact effects", items: arrayOf(current.supporting_effects), create: { node_key: "", fact_key: "" } },
  ] as const;
  const update = (key: string, next: unknown) => onChange(setField(current, key, next));
  const renderItem = (list: typeof lists[number], item: JsonObject, index: number) => {
    const items = list.items;
    const set = (next: JsonObject) => update(list.key, items.map((old, oldIndex) => oldIndex === index ? next : old));
    const remove = () => update(list.key, items.filter((_, oldIndex) => oldIndex !== index));
    const move = (direction: MoveDirection) => update(list.key, moveItem(items, index, direction));
    if (list.key === "terminal_effects" || list.key === "supporting_effects") {
      return <FactReferenceEditor key={`${path}.${list.key}.${index}`} value={item} document={document} path={`${path}.${list.key}.${index}`} onChange={set} onRemove={remove} onMove={move} moveIndex={index} moveCount={items.length} />;
    }
    return <ListCard key={`${path}.${list.key}.${index}`} title="Target Fact effect" onRemove={remove} onMove={move} moveIndex={index} moveCount={items.length}><FactKeyField value={item.fact_key} onChange={(next) => set(setField(item, "fact_key", next))} path={`${path}.${list.key}.${index}.fact_key`} label="Fact" document={document} required /><ScalarField value={item.value} onChange={(next) => set(setField(item, "value", next))} path={`${path}.${list.key}.${index}.value`} label="值" required /></ListCard>;
  };
  return <article className="nested-editor"><header className="nested-object-header"><NestedObjectHeader typeLabel="Action planning" identity={editorLabel("Projection")} /></header>{lists.map((list) => <section className="nested-list" key={list.key}><div className="typed-array-heading"><h4>{editorLabel(list.title)}</h4><button type="button" className="small" onClick={() => update(list.key, [...list.items, list.create])}>{editorLabel("Add")}</button></div>{list.items.map((item, index) => renderItem(list, item, index))}</section>)}<MultiValuePicker value={current.success_outcome_codes} onChange={(next) => update("success_outcome_codes", next)} path={`${path}.success_outcome_codes`} label="Success outcomes" options={outcomeOptions} headingAddon={outcomeNavigation(current.success_outcome_codes)} /><MultiValuePicker value={current.wait_success_outcome_codes} onChange={(next) => update("wait_success_outcome_codes", next)} path={`${path}.wait_success_outcome_codes`} label="Wait success outcomes" options={outcomeOptions} headingAddon={outcomeNavigation(current.wait_success_outcome_codes)} /><StringListField value={current.hints} onChange={(next) => update("hints", next)} path={`${path}.hints`} label="Planner hints" />{current.knowledge_gate ? <KnowledgeGateEditor value={clone(current.knowledge_gate)} document={document} path={`${path}.knowledge_gate`} onChange={(next) => update("knowledge_gate", next)} onRemove={() => update("knowledge_gate", null)} /> : <button type="button" className="small" onClick={() => update("knowledge_gate", { node_key: "", fact_key: "", accepted_values: [true] })}>{editorLabel("Add knowledge gate")}</button>}</article>;
}

function ParameterEditorBase({ value, path, onChange, onRemove, onMove, moveIndex, moveCount, defaultExpanded, focusPath }: { value: JsonObject; document?: JsonObject; path: string; onChange: Change; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number; defaultExpanded?: boolean; focusPath?: string }) {
  return <ListCard title={`参数 · ${String(value.key ?? "")}`} summary={cardSummary(value.name, value.value_type)} defaultExpanded={defaultExpanded} focusPath={focusPath} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}><TextField value={value.key} onChange={(next) => onChange(setField(value, "key", next))} path={`${path}.key`} label="稳定键" readOnly /><TextField value={value.name} onChange={(next) => onChange(setField(value, "name", next))} path={`${path}.name`} label="显示名称" required /><EnumField value={value.value_type} onChange={(next) => onChange(setField(value, "value_type", next))} path={`${path}.value_type`} label="值类型" choices={V2_ENUMS.parameterType} required /><BooleanField value={value.required} onChange={(next) => onChange(setField(value, "required", next))} path={`${path}.required`} label="Required" />{value.value_type === "INTEGER" && <><NumberField value={value.minimum} onChange={(next) => onChange(setField(value, "minimum", next))} path={`${path}.minimum`} label="Minimum" /><NumberField value={value.maximum} onChange={(next) => onChange(setField(value, "maximum", next))} path={`${path}.maximum`} label="最大值" /></>}{value.value_type === "ENUM" && <ScalarListField value={value.allowed_values} onChange={(next) => onChange(setField(value, "allowed_values", next))} path={`${path}.allowed_values`} label="允许的值" required={value.value_type === "ENUM"} />}{typeof value.semantic_reference_type === "string" && <EnumField value={value.semantic_reference_type} onChange={(next) => onChange(setField(value, "semantic_reference_type", next))} path={`${path}.semantic_reference_type`} label="Semantic reference" choices={V2_ENUMS.actionTargetReference} />}</ListCard>;
}

function ParameterEditor({ value, path, actionKey, document, references, scenarioId, onChange, onRemove, onMove, moveIndex, moveCount, defaultExpanded }: { value: JsonObject; path: string; actionKey: string; document: JsonObject; references: ReferenceEdge[]; scenarioId?: string; onChange: Change; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number; defaultExpanded?: boolean }) {
  const parameterKey = typeof value.key === "string" ? value.key : "";
  return <>
    <ParameterEditorBase value={value} document={document} path={path} onChange={onChange} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount} defaultExpanded={defaultExpanded} focusPath={parameterKey ? `action.${actionKey}.parameters.${parameterKey}` : undefined} />
    <ListCard title="Parameter semantics" summary={cardSummary(value.default, value.semantic_reference_type)}>
      <ScalarField value={value.default} onChange={(next) => onChange(setField(value, "default", next))} path={path + ".default"} label="Default" valueType={value.value_type} allowedValues={value.allowed_values} valueLabels={value.value_labels} />
      {typeof value.semantic_reference_type !== "string" && <EnumField value={value.semantic_reference_type} onChange={(next) => onChange(setField(value, "semantic_reference_type", next))} path={path + ".semantic_reference_type"} label="Semantic reference" choices={V2_ENUMS.actionTargetReference} />}
    </ListCard>
    {scenarioId && parameterKey && <ReferenceUsageSection references={references} target={{ object_kind: "action_parameter", object_key: actionKey + ":" + parameterKey, field_path: "parameters." + parameterKey }} document={document} scenarioId={scenarioId} />}
  </>;
}

function ExpectedOutcomeEditor({ value, path, onChange, onRemove, onMove, moveIndex, moveCount, defaultExpanded, focusPath }: { value: JsonObject; path: string; onChange: Change; onRemove?: () => void; onMove?: (direction: MoveDirection) => void; moveIndex?: number; moveCount?: number; defaultExpanded?: boolean; focusPath?: string }) {
  return <ListCard title={`结果 · ${String(value.code ?? "")}`} summary={cardSummary(value.name, value.success === true ? "成功" : value.success === false ? "失败" : "")} defaultExpanded={defaultExpanded} focusPath={focusPath} onRemove={onRemove} onMove={onMove} moveIndex={moveIndex} moveCount={moveCount}><TextField value={value.code} onChange={(next) => onChange(setField(value, "code", next))} path={`${path}.code`} label="结果代码" readOnly /><TextField value={value.name} onChange={(next) => onChange(setField(value, "name", next))} path={`${path}.name`} label="显示名称" required /><BooleanField value={value.success} onChange={(next) => onChange(setField(value, "success", next))} path={`${path}.success`} label="Success" required allowEmpty onEmpty={() => { const next = { ...value }; delete next.success; onChange(next); }} /></ListCard>;
}

function LegacyActionEditor({ entity, document, onChange, onDeleteNested, focusPath, references = [], scenarioId }: { entity: DraftObject; document: JsonObject; onChange: Change; onDeleteNested?: NestedDeleteHandler; focusPath?: string | null; references?: ReferenceEdge[]; scenarioId?: string }) {
  const value = entity.value;
  const [creationKind, setCreationKind] = useState<"parameter" | "outcome" | "binding" | "target_role" | "target_contract" | null>(null);
  const nestedFocus = focusPath?.replace(new RegExp(`^${entity.kind}\\.${entity.key}\\.`), "") ?? "";
  const focusMatch = nestedFocus.match(/^(parameters|expected_outcomes|operation_bindings|target_contracts)\.([^.]+)(?:\.|$)/);
  const [createdNestedIdentity, setCreatedNestedIdentity] = useState<string | null>(null);
  const selectedNestedIdentity = focusMatch ? `${focusMatch[1]}:${focusMatch[2]}` : createdNestedIdentity;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const parameters = arrayOf(value.parameters);
  const outcomes = arrayOf(value.expected_outcomes);
  const outcomeOptions: IdentityOption[] = outcomes.flatMap((item) => typeof item.code === "string" ? [{ key: item.code, name: `${item.code}${typeof item.name === "string" ? ` · ${item.name}` : ""}` }] : []);
  const targetRoles = arrayOf(value.target_actor_roles);
  const targetContracts = arrayOf(value.target_contracts);
  const bindings = arrayOf(value.operation_bindings);
  const behavior = typeof value.behavior === "string" ? value.behavior : "RULE";
  const targetKind = typeof value.target_kind === "string" ? value.target_kind : "NODE";
  const locality = typeof value.locality === "string" ? value.locality : "NONE";
  const requiresSourceParameter = behavior === "SUPPLY_POWER";
  const sourceParameterReady = parameters.some((item) => item.key === "source_key" && item.value_type === "STRING" && item.required === true);
  const targetKindError = behavior === "RELAY_MESSAGE" && targetKind !== "ACTOR"
    ? "传递信息行动需要以参与者为目标。"
    : ["SUPPLY_POWER", "DEPLOY_HEAVY_ENGINEERING_SUPPORT"].includes(behavior) && targetKind !== "NODE"
      ? "此行动需要以节点为目标。"
      : undefined;
  const localityError = targetKind === "ACTOR" && locality !== "ACTOR_REGION"
    ? "参与者目标行动需要使用参与者所在区域。"
    : behavior === "SURVEY_RESOURCES" && locality !== "REGION"
      ? "资源调查行动需要使用区域范围。"
      : ["SUPPLY_POWER", "DEPLOY_HEAVY_ENGINEERING_SUPPORT"].includes(behavior) && targetKind === "NODE" && locality === "NONE"
        ? "此行动需要指定节点范围。"
        : undefined;
  const semanticReferenceError = targetKind === "NODE" && typeof value.target_semantic_reference_type === "string" && !["NODE", "REGION", "FACILITY"].includes(value.target_semantic_reference_type)
    ? "节点目标只能使用节点、区域或设施语义。"
    : targetKind === "ACTOR" && typeof value.target_semantic_reference_type === "string" && value.target_semantic_reference_type !== "ACTOR"
      ? "参与者目标必须使用参与者语义。"
      : undefined;
  const removeNested = (collection: string, nestedKey: string, subject: string) => onDeleteNested?.({ parentKind: "action", parentKey: entity.key, collection, nestedKey, subject });
  const targetDomain = "node" as const;
  const targetOptions = referenceOptions(document, targetDomain);
  const targetContractOptions = actionTargetOptions(document, value);
  const roleOptions = referenceOptions(document, "role");
  const identityFields: IdentityCreationField[] = creationKind === "parameter"
    ? [{ key: "key", label: "参数键", required: true, pattern: "[a-z][a-z0-9_]{0,79}" }]
    : creationKind === "outcome"
      ? [{ key: "code", label: "结果代码", required: true, pattern: "[A-Za-z][A-Za-z0-9_]{0,99}" }]
      : creationKind === "binding"
        ? [{ key: "role", label: "绑定角色", required: true, pattern: "[a-z][a-z0-9_]{0,79}" }]
        : creationKind === "target_role"
          ? [
            { key: "target_key", label: "目标节点", type: "select", required: true, options: targetOptions, emptyMessage: "暂无可用目标节点，请先创建世界实体。", ownerHref: "../world-entities", ownerLabel: "前往世界实体" },
            { key: "required_actor_role_key", label: "参与者角色", type: "select", required: true, options: roleOptions, emptyMessage: "暂无可用角色，请先创建角色。", ownerHref: "../roles", ownerLabel: "前往角色" },
          ]
          : creationKind === "target_contract"
            ? [{ key: "target_key", label: targetKind === "ACTOR" ? "目标参与者" : "目标节点", type: "select", required: true, options: targetContractOptions, emptyMessage: "暂无可用目标，请先创建目标对象。", ownerHref: targetKind === "ACTOR" ? "../actors" : "../world-entities", ownerLabel: targetKind === "ACTOR" ? "前往参与者" : "前往世界实体" }]
            : [];
  const createNestedIdentity = (values: IdentityCreationValues): string | null => {
    if (creationKind === "parameter") {
      const key = values.key?.trim() ?? "";
      if (!/^[a-z][a-z0-9_]{0,79}$/.test(key)) return "参数键必须是小写稳定键。";
      if (parameters.some((item) => item.key === key)) return "此行动已经存在相同参数键。";
      setCreatedNestedIdentity(`parameters:${key}`);
      onChange(setField(value, "parameters", [...parameters, { key, name: "", required: true, allowed_values: [] }]), "identity-create");
    } else if (creationKind === "outcome") {
      const code = values.code?.trim() ?? "";
      if (!/^[A-Za-z][A-Za-z0-9_]{0,99}$/.test(code)) return "结果代码必须以字母开头，只能包含字母、数字或下划线。";
      if (outcomes.some((item) => item.code === code)) return "此行动已经存在相同结果代码。";
      setCreatedNestedIdentity(`expected_outcomes:${code}`);
      onChange(setField(value, "expected_outcomes", [...outcomes, { code, name: "" }]), "identity-create");
    } else if (creationKind === "binding") {
      const role = values.role?.trim() ?? "";
      if (!/^[a-z][a-z0-9_]{0,79}$/.test(role)) return "绑定角色必须是小写稳定键。";
      if (bindings.some((item) => item.role === role)) return "此行动已经存在相同绑定角色。";
      setCreatedNestedIdentity(`operation_bindings:${role}`);
      onChange(setField(value, "operation_bindings", [...bindings, { role, source: "EXPLICIT" }]), "identity-create");
    } else if (creationKind === "target_role") {
      const targetKey = values.target_key?.trim() ?? "";
      const roleKey = values.required_actor_role_key?.trim() ?? "";
      if (!targetOptions.some((option) => option.key === targetKey) || !roleOptions.some((option) => option.key === roleKey)) return "请选择已有的目标和角色。";
      if (targetRoles.some((item) => item.target_key === targetKey && item.required_actor_role_key === roleKey)) return "此行动已经存在相同的目标与角色组合。";
      setCreatedNestedIdentity(`target_actor_roles:${JSON.stringify([targetKey, roleKey])}`);
      onChange(setField(value, "target_actor_roles", [...targetRoles, { target_key: targetKey, required_actor_role_key: roleKey }]), "identity-create");
    } else if (creationKind === "target_contract") {
      const targetKey = values.target_key?.trim() ?? "";
      if (!targetContractOptions.some((option) => option.key === targetKey)) return "请选择已有的可用目标。";
      if (targetContracts.some((item) => item.target_key === targetKey)) return "此行动已经存在该目标的信息配置。";
      setCreatedNestedIdentity(`target_contracts:${targetKey}`);
      onChange(setField(value, "target_contracts", [...targetContracts, { target_key: targetKey, initial_visibility: "KNOWN" }]), "identity-create");
    } else return "请选择要创建的嵌套身份。";
    setCreationKind(null);
    return null;
  };
  return <>
    <div className="typed-specialized-editor"><div className="typed-grid">
      <IdentityValue value={value.key} path={`${entity.kind}.${entity.key}.key`} label="稳定键" />
      <TextField value={value.name} onChange={(next) => update("name", next)} path={`${entity.kind}.${entity.key}.name`} label="显示名称" required />
      <TextField value={value.description} onChange={(next) => update("description", next)} path={`${entity.kind}.${entity.key}.description`} label="说明" multiline />
      <ReferenceField value={value.required_interaction_key} onChange={(next) => update("required_interaction_key", next)} path={`${entity.kind}.${entity.key}.required_interaction_key`} label="Required interaction" domain="interaction" document={document} required />
      <EnumField value={value.execution_mode} onChange={(next) => update("execution_mode", next)} path={`${entity.kind}.${entity.key}.execution_mode`} label="Execution mode" choices={V2_ENUMS.executionMode} required />
      <EnumField value={value.behavior} onChange={(next) => update("behavior", next)} path={`${entity.kind}.${entity.key}.behavior`} label="Behavior" choices={V2_ENUMS.behavior} />
      <EnumField value={value.locality} onChange={(next) => update("locality", next)} path={`${entity.kind}.${entity.key}.locality`} label="Locality" choices={V2_ENUMS.locality} error={localityError} />
      <EnumField value={value.target_kind} onChange={(next) => update("target_kind", next)} path={`${entity.kind}.${entity.key}.target_kind`} label="Target kind" choices={V2_ENUMS.targetKind} error={targetKindError} />
      <EnumField value={value.target_semantic_reference_type} onChange={(next) => update("target_semantic_reference_type", next)} path={`${entity.kind}.${entity.key}.target_semantic_reference_type`} label="Target semantic reference" choices={V2_ENUMS.actionTargetReference} error={semanticReferenceError} />
      <ReferenceField value={value.required_actor_role_key} onChange={(next) => update("required_actor_role_key", next)} path={`${entity.kind}.${entity.key}.required_actor_role_key`} label="Required actor role" domain="role" document={document} />
      <MultiReferenceField value={value.allowed_actor_capabilities} onChange={(next) => update("allowed_actor_capabilities", next)} path={`${entity.kind}.${entity.key}.allowed_actor_capabilities`} label="Allowed capabilities" domain="role" document={document} required />
    </div>
    <section className="nested-list" data-focus-path={`action.${entity.key}.parameters`}><div className="typed-array-heading"><h4>{editorLabel("Parameters")}{requiresSourceParameter && <> <span className="required-marker">*</span></>}</h4>{requiresSourceParameter && !sourceParameterReady && <p className="field-error" role="alert">供电行动至少需要一个名为 source_key 的必填字符串参数。</p>}<button type="button" className="small" onClick={() => setCreationKind("parameter")}>＋ {editorLabel("Add parameter")}</button></div>
      {parameters.map((item, index) => <ParameterEditor key={String(item.key)} value={item} actionKey={entity.key} references={references} scenarioId={scenarioId} document={document} path={`${entity.kind}.${entity.key}.parameters.${index}`} onChange={(next) => update("parameters", parameters.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={onDeleteNested ? () => removeNested("parameters", String(item.key ?? ""), `参数 ${String(item.name ?? item.key ?? "")}`) : undefined} onMove={(direction) => update("parameters", moveItem(parameters, index, direction))} moveIndex={index} moveCount={parameters.length} defaultExpanded={selectedNestedIdentity === `parameters:${String(item.key ?? "")}`} />)}
    </section>
    <section className="nested-list" data-focus-path={`action.${entity.key}.expected_outcomes`}><div className="typed-array-heading"><h4>{editorLabel("Expected outcomes")} <span className="required-marker">*</span></h4>{outcomes.length === 0 && <p className="field-error" role="alert">至少需要一个预期结果。</p>}<button type="button" className="small" onClick={() => setCreationKind("outcome")}>＋ {editorLabel("Add outcome")}</button></div>
      {outcomes.map((item, index) => <ExpectedOutcomeEditor key={String(item.code)} value={item} path={`${entity.kind}.${entity.key}.expected_outcomes.${index}`} onChange={(next) => update("expected_outcomes", outcomes.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={onDeleteNested ? () => removeNested("expected_outcomes", String(item.code ?? ""), `结果 ${String(item.name ?? item.code ?? "")}`) : undefined} onMove={(direction) => update("expected_outcomes", moveItem(outcomes, index, direction))} moveIndex={index} moveCount={outcomes.length} defaultExpanded={selectedNestedIdentity === `expected_outcomes:${String(item.code ?? "")}`} focusPath={`action.${entity.key}.expected_outcomes.${String(item.code ?? "")}`} />)}
    </section>
    <ActionPlanningEditor value={clone(value.planning)} document={document} path={`${entity.kind}.${entity.key}.planning`} outcomeOptions={outcomeOptions} onChange={(next) => update("planning", next)} />
    <StringListField value={value.goal_required_slots} onChange={(next) => update("goal_required_slots", next)} path={`${entity.kind}.${entity.key}.goal_required_slots`} label="Goal required slots" />
    <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Target actor roles")}</h4><button type="button" className="small" onClick={() => setCreationKind("target_role")}>＋ {editorLabel("Add target role")}</button></div>
      {targetRoles.map((item, index) => {
        const targetKey = String(item.target_key ?? "");
        const roleKey = String(item.required_actor_role_key ?? "");
        const targetOption = targetOptions.find((option) => option.key === targetKey);
        const roleOption = roleOptions.find((option) => option.key === roleKey);
        const targetHref = scenarioId && targetKey ? `/scenarios/${scenarioId}/edit/world-entities/${encodeURIComponent(targetKey)}` : "../world-entities";
        const roleHref = scenarioId && roleKey ? `/scenarios/${scenarioId}/edit/roles/${encodeURIComponent(roleKey)}` : "../roles";
        const identity = JSON.stringify([targetKey, roleKey]);
        return <ListCard key={identity} title={`目标参与者角色 \u00b7 ${targetOption?.name ?? targetKey} / ${roleOption?.name ?? roleKey}`} summary={`${targetKey} / ${roleKey}`} defaultExpanded={selectedNestedIdentity === `target_actor_roles:${identity}`} focusPath={`action.${entity.key}.target_actor_roles.${targetKey}:${roleKey}`} onRemove={onDeleteNested ? () => removeNested("target_actor_roles", `${targetKey}:${roleKey}`, `目标参与者角色 ${roleOption?.name ?? roleKey}`) : undefined} onMove={(direction) => update("target_actor_roles", moveItem(targetRoles, index, direction))} moveIndex={index} moveCount={targetRoles.length}><IdentityValue value={`${targetKey} \u00b7 ${roleKey}`} path={`${entity.kind}.${entity.key}.target_actor_roles.${index}`} label="目标与角色" /><FieldActionRow><OwnerLink className="field-owner-link" to={targetHref}>{`前往${targetOption?.name ?? "目标对象"}`}</OwnerLink><OwnerLink className="field-owner-link" to={roleHref}>{`前往${roleOption?.name ?? "角色"}`}</OwnerLink></FieldActionRow></ListCard>;
      })}
    </section>
    <section className="nested-list" data-focus-path={`action.${entity.key}.target_contracts`}><div className="typed-array-heading"><h4>{editorLabel("Target contracts")}</h4><button type="button" className="small" onClick={() => setCreationKind("target_contract")}>{editorLabel("Add target contract")}</button></div>
      {targetContracts.map((item, index) => {
        const targetKey = typeof item.target_key === "string" ? item.target_key : "";
        const domain = targetKind === "ACTOR" ? "actor" : "node";
        const targetOption = targetContractOptions.find((option) => option.key === targetKey);
        const targetHref = scenarioId && targetKey ? `/scenarios/${scenarioId}/edit/${domain === "actor" ? "actors" : "world-entities"}/${encodeURIComponent(targetKey)}` : domain === "actor" ? "../actors" : "../world-entities";
        return <ListCard key={targetKey || index} title={`目标信息 \u00b7 ${targetOption?.name ?? targetKey}`} summary={targetKey} defaultExpanded={selectedNestedIdentity === `target_contracts:${targetKey}`} focusPath={`action.${entity.key}.target_contracts.${targetKey}`} onRemove={onDeleteNested ? () => removeNested("target_contracts", targetKey, `目标信息 ${targetOption?.name ?? targetKey}`) : undefined} onMove={(direction) => update("target_contracts", moveItem(targetContracts, index, direction))} moveIndex={index} moveCount={targetContracts.length}>
          <IdentityValue value={targetKey} path={`${entity.kind}.${entity.key}.target_contracts.${index}.target_key`} label="目标" />
          <EnumField value={item.initial_visibility} onChange={(next) => update("target_contracts", targetContracts.map((old, oldIndex) => oldIndex === index ? setField(item, "initial_visibility", next) : old))} path={`${entity.kind}.${entity.key}.target_contracts.${index}.initial_visibility`} label="初始可见性" choices={V2_ENUMS.visibility} required />
          <BooleanField value={item.reveal_on_inspect} onChange={(next) => update("target_contracts", targetContracts.map((old, oldIndex) => oldIndex === index ? setField(item, "reveal_on_inspect", next) : old))} path={`${entity.kind}.${entity.key}.target_contracts.${index}.reveal_on_inspect`} label="检查后揭示" />
          <p className="typed-help">控制玩家/智能体何时可以获知该目标对应的具体行动要求。</p>
          <FieldActionRow><OwnerLink className="field-owner-link" to={targetHref}>前往目标对象</OwnerLink></FieldActionRow>
        </ListCard>;
      })}
    </section>
    <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Operation bindings")}</h4><button type="button" className="small" onClick={() => setCreationKind("binding")}>＋ {editorLabel("Add binding")}</button></div>
      {bindings.map((item, index) => <ListCard key={String(item.role)} title={`操作绑定 \u00b7 ${String(item.role ?? "")}`} defaultExpanded={selectedNestedIdentity === `operation_bindings:${String(item.role ?? "")}`} focusPath={`action.${entity.key}.operation_bindings.${String(item.role ?? "")}`} onRemove={onDeleteNested ? () => removeNested("operation_bindings", String(item.role ?? ""), `操作绑定 ${String(item.role ?? "")}`) : undefined} onMove={(direction) => update("operation_bindings", moveItem(bindings, index, direction))} moveIndex={index} moveCount={bindings.length}><IdentityValue value={item.role} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.role`} label="绑定角色" /><EnumField value={item.value_type} onChange={(next) => update("operation_bindings", bindings.map((old, oldIndex) => oldIndex === index ? setField(item, "value_type", next) : old))} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.value_type`} label="值类型" choices={V2_ENUMS.actionTargetReference} required /><EnumField value={item.source} onChange={(next) => update("operation_bindings", bindings.map((old, oldIndex) => oldIndex === index ? setField(item, "source", next) : old))} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.source`} label="来源" choices={["EXPLICIT", "EXECUTION_START_ACTOR_REGION"]} enumDomain="operation_binding_source" /><TextField value={item.description} onChange={(next) => update("operation_bindings", bindings.map((old, oldIndex) => oldIndex === index ? setField(item, "description", next) : old))} path={`${entity.kind}.${entity.key}.operation_bindings.${index}.description`} label="说明" multiline /></ListCard>)}
    </section></div>
    {creationKind && <IdentityCreationDialog key={creationKind} title={creationKind === "parameter" ? "创建参数" : creationKind === "outcome" ? "创建结果" : creationKind === "binding" ? "创建操作绑定" : creationKind === "target_role" ? "创建目标参与者角色" : "创建目标信息"} fields={identityFields} onCancel={() => setCreationKind(null)} onCreate={createNestedIdentity} />}
    </>
}
function ActionEditor({ entity, document, onChange, onDeleteNested, focusPath, references = [], scenarioId }: { entity: DraftObject; document: JsonObject; onChange: Change; onDeleteNested?: NestedDeleteHandler; focusPath?: string | null; references?: ReferenceEdge[]; scenarioId?: string }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  return <>
    <LegacyActionEditor entity={entity} document={document} onChange={onChange} onDeleteNested={onDeleteNested} focusPath={focusPath} references={references} scenarioId={scenarioId} />
    <section className="nested-list action-semantic-fields">
      <div className="typed-array-heading"><h4>{editorLabel("Target and relation semantics")}</h4></div>
      <MultiReferenceField value={value.target_node_type_keys} onChange={(next) => update("target_node_type_keys", next)} path={`${entity.kind}.${entity.key}.target_node_type_keys`} label="Target node types" domain="node_type" document={document} />
      <ReferenceField value={value.source_relation_type_key} onChange={(next) => update("source_relation_type_key", next)} path={`${entity.kind}.${entity.key}.source_relation_type_key`} label="Source relation type" domain="relation_type" document={document} required={value.behavior === "SUPPLY_POWER"} />
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
  const matchedAction = arrayOf(document.actions).find((item) => item.key === value.action_key);
  const applicableTargetOptions = actionTargetOptions(document, matchedAction);
  const applicableTargetKeys = stringsOf(value.applicable_target_keys);
  const applicableTargetDomain: ReferenceDomain = matchedAction?.target_kind === "ACTOR" ? "actor" : "node";
  const applicabilityOwnerSection = referenceOwnerSection(applicableTargetDomain);
  const applicabilityScenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const applicabilityHeadingAddon = matchedAction && <span className="field-owner-links">
    {applicableTargetKeys.filter((key) => applicableTargetOptions.some((option) => option.key === key)).map((key) => <OwnerLink className="field-owner-link" key={key} to={referenceTargetHref(applicableTargetDomain, key, applicabilityScenarioId)}>{`前往${applicableTargetOptions.find((option) => option.key === key)?.name ?? key}`}</OwnerLink>)}
    <OwnerLink className="field-owner-link" to={applicabilityScenarioId ? `/scenarios/${applicabilityScenarioId}/edit/${applicabilityOwnerSection}` : `../${applicabilityOwnerSection}`}>{`前往${ownerDisplayLabel(applicabilityOwnerSection)}`}</OwnerLink>
  </span>;
  const applicabilityError = trigger === "STATE" && applicableTargetKeys.length > 0
    ? "状态规则不能设置适用目标，请清空后再验证。"
    : undefined;
  const outcomeOptions: IdentityOption[] = arrayOf(matchedAction?.expected_outcomes).flatMap((item) => typeof item.code === "string" ? [{ key: item.code, name: `${item.code}${typeof item.name === "string" ? ` · ${item.name}` : ""}` }] : []);
  const applicabilityField = trigger === "ACTION" && matchedAction
    ? <MultiValuePicker value={value.applicable_target_keys} onChange={(next) => update("applicable_target_keys", next)} path={`${entity.kind}.${entity.key}.applicable_target_keys`} label="适用目标" options={applicableTargetOptions} headingAddon={applicabilityHeadingAddon} error={applicabilityError} help="限制这条规则只对指定目标生效；留空表示所有符合行动约束的目标。" />
    : applicabilityError ? <p className="field-error" role="alert">{applicabilityError}</p> : null;
  return <div className="typed-specialized-editor"><div className="typed-grid"><TextField value={value.key} onChange={(next) => update("key", next)} path={`${entity.kind}.${entity.key}.key`} label="稳定键" />{applicabilityField}<EnumField value={value.phase} onChange={(next) => update("phase", next)} path={`${entity.kind}.${entity.key}.phase`} label="Phase" choices={V2_ENUMS.phase} required /><EnumField value={trigger} onChange={(next) => update("trigger", next)} path={`${entity.kind}.${entity.key}.trigger`} label="Trigger" choices={V2_ENUMS.trigger} />{trigger === "ACTION" && <ReferenceField value={value.action_key} onChange={(next) => update("action_key", next)} path={`${entity.kind}.${entity.key}.action_key`} label="Action" domain="action" document={document} required={trigger === "ACTION"} />}{trigger === "STATE" && hasActionKey && <p className="field-error">状态规则不能填写行动；请在校验前清空行动。</p>}<NumberField value={value.priority} onChange={(next) => update("priority", next)} path={`${entity.kind}.${entity.key}.priority`} label="Priority" required /></div><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Condition AST")}</h4>{hasCondition ? <button type="button" className="small danger" onClick={() => update("condition", null)}>{editorLabel("Remove condition")}</button> : <button type="button" className="small" onClick={() => update("condition", defaultCondition("FACT_EQUALS"))}>＋ {editorLabel("Add condition")}</button>}</div>{hasCondition && <ConditionEditor value={clone(value.condition)} document={document} path={`${entity.kind}.${entity.key}.condition`} onChange={(next) => update("condition", next)} />}</section><section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Effects")} <span className="required-marker">*</span></h4>{effects.length === 0 && <p className="field-error" role="alert">至少需要一个效果。</p>}<button type="button" className="small" onClick={() => update("effects", [...effects, defaultEffect("EMIT_OUTCOME")])}>＋ {editorLabel("Add effect")}</button></div>{effects.map((item, index) => <EffectEditor key={`${entity.key}.effects.${index}`} value={item} document={document} outcomeOptions={outcomeOptions} path={`${entity.kind}.${entity.key}.effects.${index}`} onChange={(next) => update("effects", effects.map((old, oldIndex) => oldIndex === index ? next : old))} onRemove={() => update("effects", effects.filter((_, oldIndex) => oldIndex !== index))} onMove={(direction) => update("effects", moveItem(effects, index, direction))} moveIndex={index} moveCount={effects.length} />)}</section><p className="typed-help">当前所有 ConditionV2 和 EffectV2 变体都使用结构化控件；未知的未来变体仍由服务端校验，并应通过高级回退结构编辑。</p></div>;
}

function dependencyIdentity(value: JsonObject): string {
  return JSON.stringify([value.kind ?? null, value.node_key ?? null, value.fact_key ?? null, value.accepted_values ?? [], value.region_key ?? null, value.resource_key ?? null, value.minimum ?? null, value.derived_key ?? null, value.knowledge_gate ?? null]);
}

function dependencyFocusIdentity(value: JsonObject): string {
  if (value.kind === "FACT") return `FACT:${String(value.node_key ?? "")}:${String(value.fact_key ?? "")}`;
  if (value.kind === "RESOURCE_AT_LEAST") return `RESOURCE_AT_LEAST:${String(value.region_key ?? "")}:${String(value.resource_key ?? "")}`;
  if (value.kind === "DERIVED_STATE") return `DERIVED_STATE:${String(value.derived_key ?? "")}`;
  return "";
}

function parseTypedScalar(raw: string, valueType: unknown, allowedValues: unknown[] = []): string | number | boolean | null {
  return parseTypedScalarInput(raw, valueType, allowedValues);
}

function factFromQualifiedKey(document: JsonObject, qualifiedKey: string): { nodeKey: string; factKey: string; fact: JsonObject } | null {
  const [nodeKey, factKey] = qualifiedKey.split(".");
  if (!nodeKey || !factKey) return null;
  const world = document.world && typeof document.world === "object" && !Array.isArray(document.world) ? document.world as JsonObject : {};
  const node = arrayOf(world.nodes).find((item) => item.key === nodeKey);
  const fact = arrayOf(node?.facts).find((item) => item.key === factKey);
  return fact ? { nodeKey, factKey, fact } : null;
}

function dependencySemanticRows(value: JsonObject, document: JsonObject): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  const gate = value.knowledge_gate && typeof value.knowledge_gate === "object" && !Array.isArray(value.knowledge_gate) ? value.knowledge_gate as JsonObject : null;
  const gateFact = gate ? factFromQualifiedKey(document, `${String(gate.node_key ?? "")}.${String(gate.fact_key ?? "")}`) : null;
  if (value.kind === "FACT") {
    const fact = factFromQualifiedKey(document, `${String(value.node_key ?? "")}.${String(value.fact_key ?? "")}`);
    const factName = referenceOptions(document, "fact").find((option) => option.key === `${String(value.node_key ?? "")}.${String(value.fact_key ?? "")}`)?.name ?? "无法解析目标事实";
    const accepted = Array.isArray(value.accepted_values) ? value.accepted_values[0] : undefined;
    rows.push(["目標事實", factName]);
    rows.push(["要求值", typedScalarDisplay(accepted, fact?.fact ?? {})]);
  } else if (value.kind === "RESOURCE_AT_LEAST") {
    const resourceName = referenceOptions(document, "resource").find((option) => option.key === value.resource_key)?.name ?? "无法解析资源";
    const regionName = referenceOptions(document, "region").find((option) => option.key === value.region_key)?.name ?? "无法解析区域";
    rows.push(["資源", resourceName]);
    rows.push(["範圍", regionName]);
    rows.push(["最低数量", String(value.minimum ?? "未设置")]);
  } else if (value.kind === "DERIVED_STATE") {
    const definition = arrayOf(document.derived_states).find((candidate) => candidate.key === value.derived_key);
    const derivedName = referenceOptions(document, "derived_state").find((option) => option.key === value.derived_key)?.name ?? "无法解析目标派生状态";
    rows.push(["目标派生状态", derivedName]);
    rows.push(["要求值", typedScalarDisplay(Array.isArray(value.accepted_values) ? value.accepted_values[0] : undefined, definition ?? {})]);
  }
  if (gate) {
    const gateName = referenceOptions(document, "fact").find((option) => option.key === `${String(gate.node_key ?? "")}.${String(gate.fact_key ?? "")}`)?.name ?? "无法解析知识条件事实";
    const accepted = Array.isArray(gate.accepted_values) ? gate.accepted_values[0] : undefined;
    rows.push(["知识条件", gateFact ? `${gateName} = ${typedScalarDisplay(accepted, gateFact.fact)}` : gateName]);
  } else {
    rows.push(["知识条件", "无"]);
  }
  return rows;
}

function DependencyEditor({ value, document, onRemove, onMove, index, count, defaultExpanded, focusPath }: { value: JsonObject; document: JsonObject; onRemove: () => void; onMove: (direction: MoveDirection) => void; index: number; count: number; defaultExpanded?: boolean; focusPath?: string }) {
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const kind = value.kind;
  const dependencyLabel = platformEnumLabel("derived_dependency", typeof kind === "string" ? kind : "");
  const gate = value.knowledge_gate && typeof value.knowledge_gate === "object" && !Array.isArray(value.knowledge_gate) ? value.knowledge_gate as JsonObject : null;
  const references: Array<{ domain: ReferenceDomain; key: string }> = [
    ...(kind === "FACT" && typeof value.node_key === "string" && typeof value.fact_key === "string" ? [{ domain: "fact" as const, key: `${value.node_key}.${value.fact_key}` }] : []),
    ...(kind === "RESOURCE_AT_LEAST" && typeof value.region_key === "string" ? [{ domain: "region" as const, key: value.region_key }] : []),
    ...(kind === "RESOURCE_AT_LEAST" && typeof value.resource_key === "string" ? [{ domain: "resource" as const, key: value.resource_key }] : []),
    ...(kind === "DERIVED_STATE" && typeof value.derived_key === "string" ? [{ domain: "derived_state" as const, key: value.derived_key }] : []),
    ...(gate && typeof gate.node_key === "string" && typeof gate.fact_key === "string" ? [{ domain: "fact" as const, key: `${gate.node_key}.${gate.fact_key}` }] : []),
  ];
  const targetLinks = references.flatMap(({ domain, key }, index) => {
    const option = referenceOptions(document, domain).find((item) => item.key === key);
    return option ? [{ id: `${index}:${domain}:${key}`, key, label: option.name, href: referenceTargetHref(domain, key, scenarioId) }] : [];
  });
  const ownerDomains = [...new Set(references.map((reference) => reference.domain))];
  return <ListCard title={dependencyLabel === "—" ? "依赖" : dependencyLabel} defaultExpanded={defaultExpanded} focusPath={focusPath} onRemove={onRemove} onMove={onMove} moveIndex={index} moveCount={count}>
    <section className="dependency-semantic-summary" aria-label={dependencyLabel}>
      {dependencySemanticRows(value, document).map(([label, text]) => <div className="dependency-semantic-row" key={label}><span>{label}</span><output className="readonly-field-display">{text}</output></div>)}
    </section>
    <FieldActionRow>
      {targetLinks.map((item) => <OwnerLink className="field-owner-link" key={item.id} to={item.href}>{`前往${item.label}`}</OwnerLink>)}
      {ownerDomains.filter((domain) => !references.some((reference) => reference.domain === domain && targetLinks.some((item) => item.key === reference.key))).map((domain) => <OwnerLink className="field-owner-link" key={domain} to={scenarioId ? `/scenarios/${scenarioId}/edit/${referenceOwnerSection(domain)}` : `../${referenceOwnerSection(domain)}`}>{`前往${ownerDisplayLabel(domain)}`}</OwnerLink>)}
    </FieldActionRow>
    <p className="typed-help">此依赖的目标与要求值在创建时确定。如需修改依赖身份，请删除后重新创建。</p>
  </ListCard>;
}

function LegacyDerivedStateEditor({ entity, document, onChange, focusPath }: { entity: DraftObject; document: JsonObject; onChange: Change; focusPath?: string | null }) {
  const value = entity.value;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const dependencies = arrayOf(value.dependencies);
  const [creationKind, setCreationKind] = useState<"FACT" | "RESOURCE_AT_LEAST" | "DERIVED_STATE" | null>(null);
  const [createdDependencyIdentity, setCreatedDependencyIdentity] = useState<string | null>(null);
  const nestedFocus = focusPath?.replace(new RegExp(`^${entity.kind}\\.${entity.key}\\.`), "") ?? "";
  const focusedDependencyIdentity = nestedFocus.startsWith("dependencies.") ? nestedFocus.slice("dependencies.".length).split(".")[0] : null;
  const selectedDependencyIdentity = createdDependencyIdentity;
  const factOptions = referenceOptions(document, "fact");
  const regionOptions = referenceOptions(document, "region");
  const resourceOptions = referenceOptions(document, "resource");
  const derivedOptions = referenceOptions(document, "derived_state").filter((option) => option.key !== entity.key);
  const factDomain = (values: IdentityCreationValues) => factFromQualifiedKey(document, values.fact_ref ?? "")?.fact ?? null;
  const gateDomain = (values: IdentityCreationValues) => factFromQualifiedKey(document, values.gate_fact_ref ?? "")?.fact ?? null;
  const derivedDomain = (values: IdentityCreationValues) => arrayOf(document.derived_states).find((candidate) => candidate.key === values.derived_key) ?? null;
  const gateFields: IdentityCreationField[] = [
    { key: "gate_fact_ref", label: "知识条件事实", type: "select", options: factOptions, placeholder: "不添加知识条件", emptyMessage: "暂无可选事实。", ownerHref: "../world-entities", ownerLabel: "前往事实定义", help: "仅当依赖还要求玩家已经知道某个事实时配置。" },
    { key: "gate_value", label: "已知时要求值", type: "typed-scalar", visible: (values) => Boolean(values.gate_fact_ref), required: (values) => Boolean(values.gate_fact_ref), scalarDomain: gateDomain, resetOnChange: ["gate_fact_ref"], help: "当玩家已知的事实符合此值时，知识条件成立。" },
  ];
  const creationFields: IdentityCreationField[] = creationKind === "FACT"
    ? [
      { key: "fact_ref", label: "目标事实", type: "select", required: true, options: factOptions, emptyMessage: "暂无可选事实，请先创建事实。", ownerHref: "../world-entities", ownerLabel: "前往事实定义", help: "选择哪个事实需要满足条件。" },
      { key: "accepted_value", label: "要求值", type: "typed-scalar", required: true, scalarDomain: factDomain, resetOnChange: ["fact_ref"], help: "当该事实满足此值时，依赖成立。" },
      ...gateFields,
    ]
    : creationKind === "RESOURCE_AT_LEAST"
      ? [
        { key: "resource_key", label: "资源", type: "select", required: true, options: resourceOptions, emptyMessage: "暂无可选资源，请先创建资源。", ownerHref: "../resources", ownerLabel: "前往资源定义", help: "选择要检查数量的资源。" },
        { key: "region_key", label: "资源范围（区域）", type: "select", required: true, options: regionOptions, emptyMessage: "暂无可选区域，请先配置区域节点类型。", ownerHref: "../world-entities", ownerLabel: "前往世界实体", help: "此字段对应依赖的区域范围。" },
        { key: "minimum", label: "最低数量", type: "typed-scalar", required: true, scalarDomain: () => ({ value_type: "INTEGER" }), help: "当指定区域内的已知资源数量达到此下限时，依赖成立。" },
        ...gateFields,
      ]
      : creationKind === "DERIVED_STATE"
        ? [
          { key: "derived_key", label: "目标派生状态", type: "select", required: true, options: derivedOptions, emptyMessage: "暂无其他派生状态，请先创建派生状态。", ownerHref: "../derived-states", ownerLabel: "前往派生状态", help: "选择需要满足条件的派生状态。" },
          { key: "accepted_value", label: "要求值", type: "typed-scalar", required: true, scalarDomain: derivedDomain, resetOnChange: ["derived_key"], help: "当目标派生状态满足此值时，依赖成立。" },
          ...gateFields,
        ]
        : [];

  const createKnowledgeGate = (values: IdentityCreationValues): JsonObject | string | null => {
    const qualified = values.gate_fact_ref ?? "";
    if (!qualified) return null;
    const selected = factFromQualifiedKey(document, qualified);
    if (!selected) return "请选择已有的知识条件事实。";
    const accepted = parseTypedScalar(values.gate_value ?? "", selected.fact.value_type, Array.isArray(selected.fact.allowed_values) ? selected.fact.allowed_values : []);
    if (accepted === null) return "请选择符合知识条件事实类型的要求值。";
    return { node_key: selected.nodeKey, fact_key: selected.factKey, accepted_values: [accepted] };
  };
  const createDependency = (values: IdentityCreationValues): string | null => {
    if (!creationKind) return "请先选择依赖类型。";
    let item: JsonObject;
    if (creationKind === "FACT") {
      const selected = factFromQualifiedKey(document, values.fact_ref ?? "");
      if (!selected) return "请选择已有的目标事实。";
      const accepted = parseTypedScalar(values.accepted_value ?? "", selected.fact.value_type, Array.isArray(selected.fact.allowed_values) ? selected.fact.allowed_values : []);
      if (accepted === null) return "请选择符合目标事实类型的要求值。";
      item = { kind: "FACT", node_key: selected.nodeKey, fact_key: selected.factKey, accepted_values: [accepted] };
    } else if (creationKind === "RESOURCE_AT_LEAST") {
      const regionKey = values.region_key?.trim() ?? "";
      const resourceKey = values.resource_key?.trim() ?? "";
      const minimum = parseTypedScalar(values.minimum ?? "", "INTEGER");
      if (!regionOptions.some((option) => option.key === regionKey) || !resourceOptions.some((option) => option.key === resourceKey)) return "请选择已有的区域与资源。";
      if (typeof minimum !== "number" || minimum < 0) return "最低数量必须是大于或等于 0 的整数。";
      item = { kind: "RESOURCE_AT_LEAST", region_key: regionKey, resource_key: resourceKey, minimum };
    } else {
      const derivedKey = values.derived_key?.trim() ?? "";
      const selected = arrayOf(document.derived_states).find((candidate) => candidate.key === derivedKey);
      if (!selected || derivedKey === entity.key || !derivedOptions.some((option) => option.key === derivedKey)) return "请选择其他已有的派生状态。";
      const accepted = parseTypedScalar(values.accepted_value ?? "", selected.value_type, Array.isArray(selected.allowed_values) ? selected.allowed_values : []);
      if (accepted === null) return "请选择符合目标派生状态类型的要求值。";
      item = { kind: "DERIVED_STATE", derived_key: derivedKey, accepted_values: [accepted] };
    }
    const gate = createKnowledgeGate(values);
    if (typeof gate === "string") return gate;
    if (gate) item.knowledge_gate = gate;
    const identity = dependencyIdentity(item);
    if (dependencies.some((existing) => dependencyIdentity(existing) === identity)) return "此依赖身份已存在，请调整条件或删除重复依赖。";
    setCreatedDependencyIdentity(identity);
    onChange(setField(value, "dependencies", [...dependencies, item]), "identity-create");
    setCreationKind(null);
    return null;
  };
  return <div className="typed-specialized-editor"><div className="typed-grid"><TextField value={value.key} onChange={(next) => update("key", next)} path={`${entity.kind}.${entity.key}.key`} label="稳定键" /><TextField value={value.name} onChange={(next) => update("name", next)} path={`${entity.kind}.${entity.key}.name`} label="显示名称" required /><TextField value={value.description} onChange={(next) => update("description", next)} path={`${entity.kind}.${entity.key}.description`} label="说明" multiline /><EnumField value={value.value_type} onChange={(next) => update("value_type", next)} path={`${entity.kind}.${entity.key}.value_type`} label="值类型" choices={V2_ENUMS.factType} required /><ScalarField value={value.available_value} onChange={(next) => update("available_value", next)} path={`${entity.kind}.${entity.key}.available_value`} label="可用值" required valueType={value.value_type} allowedValues={value.allowed_values} valueLabels={value.value_labels} /><ScalarField value={value.unavailable_value} onChange={(next) => update("unavailable_value", next)} path={`${entity.kind}.${entity.key}.unavailable_value`} label="不可用值" required valueType={value.value_type} allowedValues={value.allowed_values} valueLabels={value.value_labels} /><BooleanField value={value.goal_addressable} onChange={(next) => update("goal_addressable", next)} path={`${entity.kind}.${entity.key}.goal_addressable`} label="可用于目标" /></div><ScalarListField value={value.allowed_values} onChange={(next) => update("allowed_values", next)} path={`${entity.kind}.${entity.key}.allowed_values`} label="允许的值" required={value.value_type === "ENUM"} /><StringListField value={value.goal_aliases} onChange={(next) => update("goal_aliases", next)} path={`${entity.kind}.${entity.key}.goal_aliases`} label="目标别名" /><StringListField value={value.goal_examples} onChange={(next) => update("goal_examples", next)} path={`${entity.kind}.${entity.key}.goal_examples`} label="目标示例" /><section className="nested-list" data-focus-path={`derived_state.${entity.key}.dependencies`}><div className="typed-array-heading"><h4>{editorLabel("Dependencies")} <span className="required-marker">*</span></h4>{dependencies.length === 0 && <p className="field-error" role="alert">至少需要一个组成条件。</p>}<div className="button-row">{(["FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE"] as const).map((kind) => <button type="button" className="small" key={kind} onClick={() => setCreationKind(kind)}>＋ 添加{platformEnumLabel("derived_dependency", kind)}</button>)}</div></div>{dependencies.map((item, index) => <DependencyEditor key={dependencyIdentity(item)} value={item} document={document} onRemove={() => update("dependencies", dependencies.filter((_, oldIndex) => oldIndex !== index))} onMove={(direction) => update("dependencies", moveItem(dependencies, index, direction))} index={index} count={dependencies.length} defaultExpanded={selectedDependencyIdentity === dependencyIdentity(item) || focusedDependencyIdentity === dependencyFocusIdentity(item)} focusPath={`derived_state.${entity.key}.dependencies.${dependencyFocusIdentity(item)}`} />)}</section>{creationKind && <IdentityCreationDialog key={creationKind} title={`添加${platformEnumLabel("derived_dependency", creationKind)}`} fields={creationFields} onCancel={() => setCreationKind(null)} onCreate={createDependency} />}</div>;
}

function DerivedStateEditor({ entity, document, onChange, focusPath }: { entity: DraftObject; document: JsonObject; onChange: Change; focusPath?: string | null }) {
  const value = entity.value;
  return <>
    <LegacyDerivedStateEditor entity={entity} document={document} onChange={onChange} focusPath={focusPath} />
    <ValueLabelList value={value.value_labels} valueType={value.value_type} allowedValues={value.allowed_values} path={`${entity.kind}.${entity.key}.value_labels`} onChange={(next, intent) => onChange(setField(value, "value_labels", next), intent)} />
  </>;
}

function InitializationEditor({ value }: { value: JsonObject; document: JsonObject; onChange: Change }) {
  const legacyRowCount = arrayOf(value.resource_initial_states).length;
  return <div className="typed-specialized-editor">
    <p>{'\u521d\u59cb\u5316\u6761\u76ee\u5728\u5404\u81ea\u7684\u89c4\u8303\u5f52\u5c5e\u9875\u9762\u521b\u5efa\u548c\u7f16\u8f91\u3002\u517c\u5bb9\u65e7\u7248\u6761\u76ee\uff1a'}{legacyRowCount}{'\u3002'}</p>
    <FieldActionRow><OwnerLink to="../initialization">前往初始化</OwnerLink></FieldActionRow>
  </div>;
}

function PlanningEditor({ value, onChange }: { value: JsonObject; onChange: Change }) {
  const hints = arrayOf(value.recovery_hints);
  return <div className="typed-specialized-editor">
    <StringListField value={value.instructions} onChange={(next) => onChange(setField(value, "instructions", next))} path="planning.instructions" label="Planning instructions" />
    <section className="nested-list"><h4>恢复提示</h4>{hints.map((item) => <div className="readonly-field" key={String(item.failure_code ?? "")}><code>{String(item.failure_code ?? "")}</code><span>{String(item.hint ?? "")}</span></div>)}
      <FieldActionRow><OwnerLink to="../planning-recovery">前往规划恢复</OwnerLink></FieldActionRow>
    </section>
  </div>;
}
export function ResourceSourceHintEditor({ value, document, path, onChange }: { value: unknown; document: JsonObject; path: string; onChange: (value: JsonObject | null) => void }) {
  const hint = clone(value);
  const configured = value !== null && typeof value === "object" && !Array.isArray(value);
  const options = referenceOptions(document, "region");
  const scenarioId = window.location.pathname.match(/^\/scenarios\/([^/]+)\/edit(?:\/|$)/)?.[1];
  const regionOwnerHref = scenarioId ? `/scenarios/${scenarioId}/edit/world-entities` : "../world-entities";
  const candidateLinks = stringsOf(hint.candidate_region_keys).flatMap((key) => {
    const option = options.find((candidate) => candidate.key === key);
    return option ? [{ key, name: option.name, href: referenceTargetHref("region", key, scenarioId) }] : [];
  });
  const candidateHeadingAddon = <span className="field-owner-links">{candidateLinks.map((item) => <OwnerLink className="field-owner-link" key={item.key} to={item.href}>{`前往${item.name}`}</OwnerLink>)}<OwnerLink className="field-owner-link" to={regionOwnerHref}>前往区域</OwnerLink></span>;
  const update = (key: string, next: unknown) => onChange(setField(hint, key, next));
  if (!configured) return <section className="nested-list resource-source-hint-editor"><h4>{editorLabel("Source hint")}</h4><p className="typed-help">来源提示由资源定义。请在详细信息中添加主要或候选区域。</p><button type="button" className="small" onClick={() => onChange({ primary_region_key: null, candidate_region_keys: [] })}>{'\uff0b \u914d\u7f6e\u6765\u6e90\u63d0\u793a'}</button></section>;
  const hasAnyRegion = Boolean(hint.primary_region_key) || stringsOf(hint.candidate_region_keys).length > 0;
  return <section className="nested-list resource-source-hint-editor"><div className="typed-array-heading"><h4>{editorLabel("Source hint")} <span className="required-marker">*</span></h4><button type="button" className="small danger" onClick={() => onChange(null)}>{editorLabel("Clear source hint")}</button></div><p className="typed-help">来源区域必须引用现有的区域节点。</p><div className="typed-grid"><ReferenceField value={hint.primary_region_key} onChange={(next) => update("primary_region_key", next || null)} path={`${path}.primary_region_key`} label="主要区域" domain="region" document={document} /><div className="form-field-full"><MultiValuePicker value={hint.candidate_region_keys} onChange={(next) => update("candidate_region_keys", next)} path={`${path}.candidate_region_keys`} label="候选区域" options={options} headingAddon={candidateHeadingAddon} /></div></div>{!hasAnyRegion && <p className="field-error" role="alert">请选择主要区域或添加至少一个候选区域。</p>}</section>;
}

function GoalResolutionEditor({ value, onChange }: { value: JsonObject; onChange: Change }) {
  return <div className="typed-specialized-editor"><BooleanField value={value.allow_llm_fallback} onChange={(next) => onChange(setField(value, "allow_llm_fallback", next))} path="goal_resolution.allow_llm_fallback" label="Allow LLM fallback" /><TextField value={value.clarification_prompt} onChange={(next) => onChange(setField(value, "clarification_prompt", next))} path="goal_resolution.clarification_prompt" label="澄清提示" multiline required /><StringListField value={value.quick_inputs} onChange={(next) => onChange(setField(value, "quick_inputs", next))} path="goal_resolution.quick_inputs" label="快速输入" help="仅用于填充玩家的目标输入；玩家仍可编辑，提交后仍由目标解析器处理。" /><BooleanField value={value.world_goal_state_catalog} onChange={(next) => onChange(setField(value, "world_goal_state_catalog", next))} path="goal_resolution.world_goal_state_catalog" label="世界目标状态目录" /></div>;
}

function PublicKnowledgeEditor({ value }: { value: JsonObject; document: JsonObject; onChange: Change }) {
  const legacyRows = arrayOf(value.resource_source_hints);
  return <div className="typed-specialized-editor">
    <p>资源来源提示归资源定义管理，旧行只读展示。</p>
    {legacyRows.map((item) => <div className="readonly-field" key={String(item.resource_key ?? "")}><code>{String(item.resource_key ?? "")}</code></div>)}
    <FieldActionRow><OwnerLink to="../resources">前往资源定义</OwnerLink></FieldActionRow>
  </div>;
}
function PublicReferenceEditor({ entity, document, scenarioId }: { entity: DraftObject; document: JsonObject; scenarioId?: string }) {
  const value = entity.value;
  const domain: ReferenceDomain = value.ref_type === "RESOURCE" ? "resource" : value.ref_type === "DERIVED_STATE" ? "derived_state" : value.ref_type === "ACTION" ? "action" : value.ref_type === "ACTOR" ? "actor" : value.ref_type === "REGION" ? "region" : "node";
  const targetKey = typeof value.ref_key === "string" ? value.ref_key : "";
  const targetExists = referenceOptions(document, domain).some((option) => option.key === targetKey);
  const targetHref = targetExists ? referenceTargetHref(domain, targetKey, scenarioId) : undefined;
  const ownerHref = scenarioId ? `/scenarios/${scenarioId}/edit/${referenceOwnerSection(domain)}` : `../${referenceOwnerSection(domain)}`;
  return <div className="typed-specialized-editor"><IdentityValue value={value.term} path={`${entity.kind}.${entity.key}.term`} label="Public term" /><IdentityValue value={value.ref_type} path={`${entity.kind}.${entity.key}.ref_type`} label="Reference type" /><IdentityValue value={value.ref_key} path={`${entity.kind}.${entity.key}.ref_key`} label="Reference key" /><FieldActionRow><OwnerLink className="field-owner-link" to={targetHref ?? ownerHref}>{targetHref ? "前往所选对象" : "前往目标对象"}</OwnerLink></FieldActionRow></div>;
}
function collectionRootPath(section: "initialization" | "planning" | "public-knowledge"): string {
  return section === "public-knowledge" ? "public_knowledge" : section;
}

function CollectionDetailEditor({ section, selection, index, value, onChange, onRemove, onMove, moveCount }: { section: "initialization" | "planning" | "public-knowledge"; selection: RootCollectionSelection; index: number; value: JsonObject; document?: JsonObject; onChange: Change; onRemove: () => void; onMove?: (direction: MoveDirection) => void; moveCount?: number }) {
  const path = `${collectionRootPath(section)}.${selection.collection}.${index}`;
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  if (selection.collection === "resource_initial_states") return <article className="collection-detail-editor"><h4>旧版资源初始状态</h4><IdentityValue value={value.resource_key} path={`${path}.resource_key`} label="资源身份" /><IdentityValue value={value.scope_node_key ?? "global"} path={`${path}.scope_node_key`} label="范围身份" /><p>此兼容集合仅支持只读查看，当前数据请使用初始化正式所有者。</p><FieldActionRow><OwnerLink to="../initialization">前往初始化</OwnerLink></FieldActionRow></article>;
  return <article className="collection-detail-editor"><header className="collection-detail-heading"><div><p className="panel-kicker">{editorLabel("Collection item")}</p><h4>{rootCollectionLabel(selection.collection)}</h4></div><span className="collection-detail-actions"><ReorderControls index={index} count={moveCount ?? 1} onMove={onMove} /><button type="button" className="small danger" onClick={onRemove}>删除此项</button></span></header>{selection.collection === "recovery_hints" && <div className="typed-grid"><TextField value={value.failure_code} onChange={(next) => update("failure_code", next)} path={`${path}.failure_code`} label="失败代码（稳定身份）" readOnly /><div className="form-field-full"><TextField value={value.hint} onChange={(next) => update("hint", next)} path={`${path}.hint`} label="失败恢复策略" multiline required /></div></div>}</article>;
}

function selectedCollection(value: JsonObject, selection: RootOwnerSelection | null): { index: number; value: JsonObject } | null {
  return selection?.owner === "collection" ? rootCollectionItem(value, selection) : null;
}

function collectionLength(value: JsonObject, collection: RootCollectionSelection["collection"]): number {
  const items = value[collection];
  return Array.isArray(items) ? items.length : 0;
}

function CollectionEmptyState({ label }: { label: string }) {
  return <div className="collection-empty-state"><strong>{label}</strong><p>请从左侧列表选择或新建一项。</p></div>;
}

export function MasterDetailInitializationEditor({ value, document, selection, onChange, onCollectionChange, onCollectionRemove, onCollectionMove }: { value: JsonObject; document: JsonObject; selection: RootOwnerSelection | null; onChange: Change; onCollectionChange: (value: JsonObject) => void; onCollectionRemove: () => void; onCollectionMove?: (direction: MoveDirection) => void }) {
  const selected = selectedCollection(value, selection);
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  if (selection?.owner === "singleton") {
    return <div className="typed-specialized-editor"><section className="root-singleton-card"><div className="root-singleton-heading"><div><p className="panel-kicker">初始化</p><h4>初始化入口</h4></div></div><div className="typed-grid"><ReferenceField value={value.start_node_key} onChange={(next) => update("start_node_key", next)} path="initialization.start_node_key" label="起始节点" domain="node" document={document} required /><ReferenceField value={value.primary_actor_key} onChange={(next) => update("primary_actor_key", next)} path="initialization.primary_actor_key" label="主要参与者" domain="actor" document={document} required /></div></section></div>;
  }
  return <div className="typed-specialized-editor">{selected && selection?.owner === "collection" ? <CollectionDetailEditor section="initialization" selection={selection} index={selected.index} value={selected.value} document={document} onChange={onCollectionChange} onRemove={onCollectionRemove} onMove={selection.collection === "recovery_hints" ? onCollectionMove : undefined} moveCount={collectionLength(value, selection.collection)} /> : <CollectionEmptyState label="请选择初始化配置或集合项" />}</div>;
}

export function MasterDetailPlanningEditor(props: { page: "instructions" | "recovery"; value: JsonObject; document: JsonObject; selection: RootOwnerSelection | null; onChange: Change; onCollectionChange: (value: JsonObject) => void; onCollectionRemove: () => void; onCollectionMove?: (direction: MoveDirection) => void; onInstructionChange?: (index: number, content: string) => void; onInstructionRemove?: (index: number) => void; onInstructionMove?: (index: number, direction: MoveDirection) => void }) {
  const { page, value, selection, onCollectionChange, onInstructionChange } = props;
  if (page === "instructions") {
    if (selection?.owner !== "instruction") return <CollectionEmptyState label="请选择或新增一条规划指引。" />;
    const instructions = stringsOf(value.instructions);
    const content = instructions[selection.index];
    if (content === undefined) return <CollectionEmptyState label="请选择或新增一条规划指引。" />;
    return <div className="typed-specialized-editor planning-detail-fields"><TextField value={content} onChange={(next) => onInstructionChange?.(selection.index, next)} path={`planning.instructions.${selection.index}`} label="指引内容" multiline /></div>;
  }
  const selected = selectedCollection(value, selection);
  if (!selected || selection?.owner !== "collection") return <div className="typed-specialized-editor"><CollectionEmptyState label="请选择或新增一条失败恢复策略。" /></div>;
  const path = `planning.${selection.collection}.${selected.index}`;
  return <div className="typed-specialized-editor planning-detail-fields"><div className="typed-grid"><TextField value={selected.value.failure_code} onChange={() => undefined} path={`${path}.failure_code`} label="失败代码（稳定身份）" readOnly /><div className="form-field-full"><TextField value={selected.value.hint} onChange={(next) => onCollectionChange(setField(selected.value, "hint", next))} path={`${path}.hint`} label="恢复策略" multiline required /></div></div></div>;
}

export function MasterDetailPublicKnowledgeEditor({ value, selection }: { value: JsonObject; document: JsonObject; selection: RootOwnerSelection | null; onCollectionChange: (value: JsonObject) => void; onCollectionRemove: () => void; onCollectionMove?: (direction: MoveDirection) => void }) {
  const selected = selectedCollection(value, selection);
  if (!selected || selection?.owner !== "collection") return <div className="typed-specialized-editor"><p>资源所有者迁移后，旧版来源提示行仅支持只读。</p><FieldActionRow><OwnerLink to="../resources">前往资源定义</OwnerLink></FieldActionRow></div>;
  const item = selected.value;
  return <div className="typed-specialized-editor">
    <p>此旧版行仅支持只读。正式配置归资源所有者。</p>
    <div className="readonly-field"><code>{String(item.resource_key ?? "")}</code><span>{String(item.primary_region_key ?? "")}</span></div>
    <FieldActionRow><OwnerLink to="../resources">前往资源定义</OwnerLink></FieldActionRow>
  </div>;
}
function DoctrineEditor({ value, path, onChange, onDeleteNested, parentKey }: { value: unknown; path: string; onChange: (value: JsonObject[], intent?: IdentityCreationIntent) => void; onDeleteNested?: NestedDeleteHandler; parentKey: string }) {
  const doctrine = arrayOf(value);
  const [creationOpen, setCreationOpen] = useState(false);
  const [selectedDoctrineKey, setSelectedDoctrineKey] = useState<string | null>(null);
  const createDoctrine = (values: IdentityCreationValues): string | null => {
    const key = values.key?.trim() ?? "";
    if (!/^[a-z][a-z0-9_]{0,79}$/.test(key)) return "教义键必须是小写稳定键。";
    if (doctrine.some((item) => item.key === key)) return "此参与者已经存在相同的教义项。";
    setSelectedDoctrineKey(key);
    onChange([...doctrine, { key }], "identity-create");
    setCreationOpen(false);
    return null;
  };
  return <><section className="nested-list">
    <div className="typed-array-heading"><h4>{editorLabel("Doctrine")}</h4><button type="button" className="small" onClick={() => setCreationOpen(true)}>＋ {editorLabel("Add doctrine")}</button></div>
    {doctrine.map((item, index) => <ListCard key={String(item.key)} title={`行为准则 · ${String(item.key ?? "")}`} summary={cardSummary(item.value)} defaultExpanded={selectedDoctrineKey === String(item.key ?? "")} onRemove={onDeleteNested ? () => onDeleteNested({ parentKind: "actor", parentKey, collection: "doctrine", nestedKey: String(item.key ?? ""), subject: `行为准则 ${String(item.key ?? "")}` }) : undefined} onMove={(direction) => onChange(moveItem(doctrine, index, direction))} moveIndex={index} moveCount={doctrine.length}>
      <IdentityValue value={item.key} path={`${path}.${index}.key`} label="教义键" />
      <ScalarField value={item.value} onChange={(next) => onChange(doctrine.map((old, oldIndex) => oldIndex === index ? setField(item, "value", next) : old))} path={`${path}.${index}.value`} label="值" required />
    </ListCard>)}
  </section>{creationOpen && <IdentityCreationDialog title="创建教义项" fields={[{ key: "key", label: "教义键", required: true, pattern: "[a-z][a-z0-9_]{0,79}" }]} onCancel={() => setCreationOpen(false)} onCreate={createDoctrine} />}</>;
}
export type AuthorityParameterOption = IdentityOption & { valueType: string; allowedValues: unknown[]; actionKeys: string[] };
export type AuthorityParameterCandidates = { options: AuthorityParameterOption[]; ambiguousParameterKeys: string[] };

// eslint-disable-next-line react-refresh/only-export-components
export function authorityPolicyParameterOptions(document: JsonObject, parentKind: "action" | "actor", parentKey: string, allowedActionKeys: string[] = []): AuthorityParameterCandidates {
  const actions = arrayOf(document.actions);
  const allowed = new Set(parentKind === "action" ? [parentKey] : allowedActionKeys);
  const groups = new Map<string, Array<{ actionKey: string; actionName: string; parameter: JsonObject; signature: string }>>();
  for (const action of actions) {
    const actionKey = typeof action.key === "string" ? action.key : "";
    if (!allowed.has(actionKey)) continue;
    const actionName = typeof action.name === "string" && action.name.trim() ? action.name.trim() : actionKey;
    for (const parameter of arrayOf(action.parameters)) {
      const key = typeof parameter.key === "string" ? parameter.key : "";
      if (!key) continue;
      const signature = JSON.stringify({ value_type: parameter.value_type ?? null, required: parameter.required ?? null, allowed_values: parameter.allowed_values ?? [], minimum: parameter.minimum ?? null, maximum: parameter.maximum ?? null, semantic_reference_type: parameter.semantic_reference_type ?? null });
      const group = groups.get(key) ?? [];
      group.push({ actionKey, actionName, parameter, signature });
      groups.set(key, group);
    }
  }
  const options: AuthorityParameterOption[] = [];
  const ambiguousParameterKeys: string[] = [];
  for (const [key, group] of groups) {
    if (new Set(group.map((item) => item.signature)).size > 1) {
      ambiguousParameterKeys.push(key);
      continue;
    }
    const first = group[0].parameter;
    const parameterName = typeof first.name === "string" && first.name.trim() ? first.name.trim() : key;
    const sourceName = parentKind === "actor" && group.length > 1 ? ` · ${group.map((item) => item.actionName).join(", ")}` : "";
    options.push({ key, name: `${parameterName}${sourceName}`, valueType: typeof first.value_type === "string" ? first.value_type : "", allowedValues: Array.isArray(first.allowed_values) ? first.allowed_values : [], actionKeys: group.map((item) => item.actionKey) });
  }
  return { options, ambiguousParameterKeys: ambiguousParameterKeys.sort() };
}

function AuthorityPolicyEditor({ value, path, onChange, document, parentKind = "action", parentKey = "", allowedActionKeys = [], onDeleteNested, scenarioId, focusPath }: { value: unknown; path: string; onChange: (value: unknown, intent?: IdentityCreationIntent) => void; document: JsonObject; parentKind?: "action" | "actor"; parentKey?: string; allowedActionKeys?: string[]; onDeleteNested?: NestedDeleteHandler; scenarioId?: string; focusPath?: string | null }) {
  const policy = clone(value);
  const limits = arrayOf(policy.autonomous_limits);
  const approvals = arrayOf(policy.approval_required_values);
  const [creationKind, setCreationKind] = useState<"limit" | "approval" | null>(null);
  const [selectedPolicyIdentity, setSelectedPolicyIdentity] = useState<string | null>(null);
  const policyFocus = focusPath?.match(/authority_policy\.(autonomous_limits|approval_required_values)\.([^.]+)/);
  const focusedPolicyIdentity = policyFocus ? `${policyFocus[1]}:${policyFocus[2]}` : null;
  const update = (key: string, next: unknown) => onChange(setField(policy, key, next));
  const candidates = authorityPolicyParameterOptions(document, parentKind, parentKey, allowedActionKeys);
  const limitOptions = candidates.options.filter((option) => option.valueType === "INTEGER");
  const options = creationKind === "limit" ? limitOptions : candidates.options;
  const ownerHref = parentKind === "actor"
    ? (scenarioId ? `/scenarios/${scenarioId}/edit/actions` : "../actions")
    : (scenarioId ? `/scenarios/${scenarioId}/edit/actions/${encodeURIComponent(parentKey)}?focus_path=parameters` : "../actions");
  const creationFields: IdentityCreationField[] = creationKind === "limit"
    ? [{ key: "parameter_key", label: "行动参数", type: "select", required: true, options, emptyMessage: "暂无符合条件的整数行动参数。", ownerHref }]
    : [
      { key: "parameter_key", label: "行动参数", type: "select", required: true, options, emptyMessage: "暂无符合条件的行动参数。", ownerHref },
    ];
  const createPolicyRow = (values: IdentityCreationValues): string | null => {
    if (!creationKind) return "请选择要创建的政策行。";
    const parameterKey = values.parameter_key?.trim() ?? "";
    const option = options.find((item) => item.key === parameterKey);
    if (!option) return "请选择一个现有且符合条件的行动参数。";
    const collectionKey = creationKind === "limit" ? "autonomous_limits" : "approval_required_values";
    const rows = creationKind === "limit" ? limits : approvals;
    if (rows.some((item) => item.parameter_key === parameterKey)) return `此政策已经包含参数 ${parameterKey} 的配置。`;
    if (creationKind === "limit") {
      if (option.valueType !== "INTEGER") return "自主限制必须使用整数参数。";
      setSelectedPolicyIdentity(`autonomous_limits:${parameterKey}`);
      onChange(setField(policy, collectionKey, [...limits, { parameter_key: parameterKey }]), "identity-create");
    } else {
      setSelectedPolicyIdentity(`approval_required_values:${parameterKey}`);
      onChange(setField(policy, collectionKey, [...approvals, { parameter_key: parameterKey }]), "identity-create");
    }
    setCreationKind(null);
    return null;
  };
  const renderRows = (rows: JsonObject[], collection: "autonomous_limits" | "approval_required_values") => rows.map((item, index) => <ListCard key={String(item.parameter_key)} title={`${collection === "autonomous_limits" ? "自主限制" : "需要批准的值"} · ${String(item.parameter_key ?? "")}`} summary={cardSummary(item.parameter_key, collection === "autonomous_limits" ? item.maximum : "")} defaultExpanded={selectedPolicyIdentity === `${collection}:${String(item.parameter_key ?? "")}` || focusedPolicyIdentity === `${collection}:${String(item.parameter_key ?? "")}`} focusPath={`${path}.${collection}.${String(item.parameter_key ?? "")}`} onRemove={onDeleteNested ? () => onDeleteNested({ parentKind, parentKey, collection: `authority_policy.${collection}`, nestedKey: String(item.parameter_key ?? ""), subject: `${collection === "autonomous_limits" ? "自主限制" : "需要批准的值"} ${String(item.parameter_key ?? "")}` }) : undefined} onMove={(direction) => update(collection, moveItem(rows, index, direction))} moveIndex={index} moveCount={rows.length}>
    <IdentityValue value={item.parameter_key} path={`${path}.${collection}.${index}.parameter_key`} label="行动参数" />
    <div className="typed-grid">
    {collection === "autonomous_limits"
      ? <NumberField value={item.maximum} onChange={(next) => update(collection, limits.map((old, oldIndex) => oldIndex === index ? setField(item, "maximum", next) : old))} path={`${path}.${collection}.${index}.maximum`} label="最大值" required />
      : <ScalarListField value={item.values} onChange={(next) => update(collection, approvals.map((old, oldIndex) => oldIndex === index ? setField(item, "values", next) : old))} path={`${path}.${collection}.${index}.values`} label="要求值" required />}
    </div>
    <FieldActionRow>
    {parentKind === "actor" && candidates.options.find((option) => option.key === item.parameter_key)?.actionKeys.map((actionKey) => <OwnerLink className="field-owner-link" key={actionKey} to={scenarioId ? `/scenarios/${scenarioId}/edit/actions/${encodeURIComponent(actionKey)}?focus_path=${encodeURIComponent(`parameters.${String(item.parameter_key)}`)}` : `../actions/${encodeURIComponent(actionKey)}?focus_path=${encodeURIComponent(`parameters.${String(item.parameter_key)}`)}`}>{`前往${String(arrayOf(document.actions).find((action) => action.key === actionKey)?.name ?? actionKey)}的行动参数`}</OwnerLink>)}
    {parentKind === "action" && <OwnerLink className="field-owner-link" to={scenarioId ? `/scenarios/${scenarioId}/edit/actions/${encodeURIComponent(parentKey)}?focus_path=${encodeURIComponent(`parameters.${String(item.parameter_key)}`)}` : `../actions/${encodeURIComponent(parentKey)}?focus_path=${encodeURIComponent(`parameters.${String(item.parameter_key)}`)}`}>前往行动参数</OwnerLink>}
    </FieldActionRow>
  </ListCard>);
  return <section className="typed-specialized-editor authority-policy-editor">
    {parentKind === "actor" && candidates.ambiguousParameterKeys.length > 0 && <p className="field-error" role="alert">当前参与者允许的行动中存在参数定义歧义：{candidates.ambiguousParameterKeys.join(", ")}. 请先在行动属主中统一参数定义，再添加政策行。</p>}
    <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("Autonomous limits")}</h4><button type="button" className="small" onClick={() => setCreationKind("limit")}>＋ {editorLabel("Add limit")}</button></div>
      {limits.length === 0 && <p className="typed-help">请选择一个合法的整数行动参数，不会自动选择。</p>}
      {renderRows(limits, "autonomous_limits")}
    </section>
    <section className="nested-list"><div className="typed-array-heading"><h4>{editorLabel("需要批准的值")}</h4><button type="button" className="small" onClick={() => setCreationKind("approval")}>＋ {editorLabel("Add approval rule")}</button></div>
      {approvals.length === 0 && <p className="typed-help">请从当前参与者允许的行动中选择参数，不会自动选择。</p>}
      {renderRows(approvals, "approval_required_values")}
    </section>
    {creationKind && <IdentityCreationDialog key={creationKind} title={creationKind === "limit" ? "创建自主限制" : "创建需要批准的值"} fields={creationFields} onCancel={() => setCreationKind(null)} onCreate={createPolicyRow} />}
  </section>;
}

function ActionAuthorityPolicyEditor(props: { value: unknown; path: string; onChange: (value: unknown, intent?: IdentityCreationIntent) => void; document: JsonObject; onDeleteNested?: NestedDeleteHandler; parentKey: string; scenarioId?: string; focusPath?: string | null }) {
  return <AuthorityPolicyEditor {...props} parentKind="action" />;
}
export function ScenarioOverviewEditor({ value, document, onChange }: { value: JsonObject; document: JsonObject; onChange: Change }) {
  const locality = clone(value.locality);
  const update = (key: string, next: unknown) => onChange(setField(value, key, next));
  const updateLocality = (key: string, next: unknown) => onChange(setField(value, "locality", setField(locality, key, next)));
  return <div className="typed-specialized-editor">
    <div className="typed-grid">
      <TextField value={value.key} onChange={(next) => update("key", next)} path="metadata.key" label="Scenario key" readOnly />
      <TextField value={value.name} onChange={(next) => update("name", next)} path="metadata.name" label="Scenario name" required />
      <TextField value={value.description} onChange={(next) => update("description", next)} path="metadata.description" label="说明" multiline />
    </div>
    <section className="nested-list locality-contract-editor">
      <div className="typed-array-heading"><h4>{editorLabel("Locality contract")}</h4></div>
      <div className="typed-grid">
        <BooleanField value={locality.enabled} onChange={(next) => updateLocality("enabled", next)} path="metadata.locality.enabled" label="Enabled" />
        <BooleanField value={locality.scoped_resources} onChange={(next) => updateLocality("scoped_resources", next)} path="metadata.locality.scoped_resources" label="Scoped resources" />
        <ReferenceField value={locality.region_node_type_key} onChange={(next) => updateLocality("region_node_type_key", next)} path="metadata.locality.region_node_type_key" label="Region node type" domain="node_type" document={document} required={locality.enabled === true} />
        <ReferenceField value={locality.facility_node_type_key} onChange={(next) => updateLocality("facility_node_type_key", next)} path="metadata.locality.facility_node_type_key" label="Facility node type" domain="node_type" document={document} required={locality.enabled === true} />
        <ReferenceField value={locality.transport_node_type_key} onChange={(next) => updateLocality("transport_node_type_key", next)} path="metadata.locality.transport_node_type_key" label="Transport node type" domain="node_type" document={document} required={locality.enabled === true} />
        <ReferenceField value={locality.located_in_relation_type_key} onChange={(next) => updateLocality("located_in_relation_type_key", next)} path="metadata.locality.located_in_relation_type_key" label="Located-in relation type" domain="relation_type" document={document} required={locality.enabled === true} />
        <ReferenceField value={locality.transport_endpoint_relation_type_key} onChange={(next) => updateLocality("transport_endpoint_relation_type_key", next)} path="metadata.locality.transport_endpoint_relation_type_key" label="Endpoint relation type" domain="relation_type" document={document} required={locality.enabled === true} />
        <FactKeyField value={locality.passability_fact_key} onChange={(next) => updateLocality("passability_fact_key", next)} path="metadata.locality.passability_fact_key" label="Passability fact" document={document} />
      </div>
      <p className="typed-help">局部性契约是区域、设施和交通节点族语义的唯一依据。场景发布后请保持稳定键不变。</p>
    </section>
  </div>;
}

export {
  ActionEditor,
  ActionAuthorityPolicyEditor,
  AuthorityPolicyEditor,
  DerivedStateEditor,
  DoctrineEditor,
  GoalResolutionEditor,
  InitializationEditor,
  PlanningEditor,
  PublicKnowledgeEditor,
  PublicReferenceEditor,
  RuleEditor,
};
