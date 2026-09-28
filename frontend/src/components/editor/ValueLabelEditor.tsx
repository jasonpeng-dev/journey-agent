import { useState } from "react";

import type { IdentityCreationIntent, JsonObject } from "../../editor";
import { editorLabel } from "../../ui";
import { IdentityValue, ReorderControls, TextInput } from "./FormPrimitives";
import { IdentityCreationDialog, type IdentityCreationField, type IdentityCreationValues, type IdentityOption } from "./IdentityCreationDialog";

type ValueLabelEditorProps = {
  value: unknown;
  valueType: unknown;
  allowedValues?: unknown;
  path: string;
  onChange: (value: JsonObject[], intent?: IdentityCreationIntent) => void;
};

function objectsOf(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function sameScalar(left: unknown, right: unknown): boolean {
  return typeof left === typeof right && left === right;
}

function valueToken(value: unknown): string {
  return JSON.stringify([typeof value, value]);
}

function parseValue(type: string, token: string, allowed: unknown[]): string | number | boolean | null {
  if (type === "ENUM") {
    try {
      const parsed = JSON.parse(token) as unknown;
      if (!Array.isArray(parsed) || parsed.length !== 2 || parsed[0] !== typeof parsed[1]) return null;
      return allowed.find((candidate) => sameScalar(candidate, parsed[1])) as string | number | boolean | undefined ?? null;
    } catch {
      return null;
    }
  }
  if (type === "BOOLEAN") return token === "true" ? true : token === "false" ? false : null;
  if (type === "INTEGER") {
    if (!/^-?(0|[1-9][0-9]*)$/.test(token)) return null;
    const value = Number(token);
    return Number.isSafeInteger(value) ? value : null;
  }
  return token;
}

export function ValueLabelList({ value, valueType, allowedValues = [], path, onChange }: ValueLabelEditorProps) {
  const labels = objectsOf(value);
  const type = typeof valueType === "string" ? valueType : "STRING";
  const allowed = Array.isArray(allowedValues) ? allowedValues : [];
  const [creationOpen, setCreationOpen] = useState(false);
  const choices: IdentityOption[] = type === "ENUM"
    ? Array.from(new Map(allowed.map((item) => [valueToken(item), item])).entries()).map(([key, item]) => ({ key, name: String(item) }))
    : type === "BOOLEAN"
      ? [{ key: "true", name: "是" }, { key: "false", name: "否" }]
      : [];
  const creationFields: IdentityCreationField[] = [
    type === "ENUM" || type === "BOOLEAN"
      ? { key: "value_token", label: "规范值", type: "select", required: true, options: choices, emptyMessage: type === "ENUM" ? "事实或派生状态尚未定义允许的值。" : "请选择是或否。", ownerHref: "../world-entities" }
      : { key: "value_token", label: "规范值", required: true, pattern: type === "INTEGER" ? "-?(0|[1-9][0-9]*)" : undefined },
  ];
  const update = (index: number, key: string, next: unknown) => {
    onChange(labels.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: next } : item));
  };
  const createValueLabel = (values: IdentityCreationValues): string | null => {
    const canonical = parseValue(type, values.value_token ?? "", allowed);
    if (canonical === null) return "请输入符合该类型的规范值。";
    if (labels.some((item) => sameScalar(item.value, canonical))) return "该规范值已经存在标签。";
    onChange([...labels, { value: canonical }], "identity-create");
    setCreationOpen(false);
    return null;
  };
  return <section className="nested-list value-label-list">
    <div className="typed-array-heading"><h4>{editorLabel("Value labels")}</h4><button type="button" className="small" onClick={() => setCreationOpen(true)}>＋ {editorLabel("Add value label")}</button></div>
    {labels.map((item, index) => <article className="nested-editor value-label-editor" key={JSON.stringify([typeof item.value, item.value])}>
      <div className="typed-grid">
        <IdentityValue value={`${typeof item.value}: ${String(item.value)}`} path={`${path}.${index}.value`} label="规范值" />
        <TextInput value={item.label} onChange={(next) => update(index, "label", next)} path={`${path}.${index}.label`} label="显示标签" required />
        <TextInput value={item.summary_label} onChange={(next) => update(index, "summary_label", next || undefined)} path={`${path}.${index}.summary_label`} label="摘要标签" />
        <TextInput value={item.detail_label} onChange={(next) => update(index, "detail_label", next || undefined)} path={`${path}.${index}.detail_label`} label="详情标签" />
      </div>
      <span className="typed-array-actions"><ReorderControls index={index} count={labels.length} onMove={(direction) => onChange(direction === "up" ? labels.map((item, itemIndex) => itemIndex === index - 1 ? labels[index] : itemIndex === index ? labels[index - 1] : item) : labels.map((item, itemIndex) => itemIndex === index ? labels[index + 1] : itemIndex === index + 1 ? labels[index] : item))} /><button type="button" className="small danger" onClick={() => onChange(labels.filter((_, itemIndex) => itemIndex !== index))}>{editorLabel("Remove")}</button></span>
    </article>)}
    {creationOpen && <IdentityCreationDialog title="创建值标签" fields={creationFields} onCancel={() => setCreationOpen(false)} onCreate={createValueLabel} />}
  </section>;
}
