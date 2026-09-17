import type { JsonObject } from "../../editor";
import { editorLabel } from "../../ui";
import { BooleanControl, EnumSelect, NumberInput, TextInput } from "./FormPrimitives";

type ValueLabelEditorProps = {
  value: unknown;
  valueType: unknown;
  allowedValues?: unknown;
  path: string;
  onChange: (value: JsonObject[]) => void;
};

function objectsOf(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function scalarDefault(valueType: string, allowedValues: unknown[]): string | number | boolean {
  if (valueType === "BOOLEAN") return false;
  if (valueType === "INTEGER") return 0;
  return typeof allowedValues[0] === "string" ? allowedValues[0] : "";
}

function ScalarValueInput({ value, valueType, allowedValues, path, onChange }: {
  value: unknown;
  valueType: string;
  allowedValues: unknown[];
  path: string;
  onChange: (value: string | number | boolean | null) => void;
}) {
  if (valueType === "BOOLEAN") {
    return <BooleanControl value={value} onChange={onChange} path={path} label="Canonical value" />;
  }
  if (valueType === "INTEGER") {
    return <NumberInput value={value} onChange={onChange} path={path} label="Canonical value" integer />;
  }
  if (valueType === "ENUM" && allowedValues.every((item) => typeof item === "string")) {
    return <EnumSelect value={value} onChange={onChange as (value: string) => void} path={path} label="Canonical value" choices={allowedValues.filter((item): item is string => typeof item === "string")} />;
  }
  return <TextInput value={value} onChange={onChange as (value: string) => void} path={path} label="Canonical value" />;
}

export function ValueLabelList({ value, valueType, allowedValues = [], path, onChange }: ValueLabelEditorProps) {
  const labels = objectsOf(value);
  const type = typeof valueType === "string" ? valueType : "STRING";
  const allowed = Array.isArray(allowedValues) ? allowedValues : [];
  const update = (index: number, key: string, next: unknown) => {
    onChange(labels.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: next } : item));
  };
  return <section className="nested-list value-label-list">
    <div className="typed-array-heading"><h4>{editorLabel("Value labels")}</h4><button type="button" className="small" onClick={() => onChange([...labels, { value: scalarDefault(type, allowed), label: "" }])}>＋ {editorLabel("Add value label")}</button></div>
    {labels.map((item, index) => <article className="nested-editor value-label-editor" key={`${path}.${index}`}>
      <div className="typed-grid">
        <ScalarValueInput value={item.value} valueType={type} allowedValues={allowed} path={`${path}.${index}.value`} onChange={(next) => update(index, "value", next)} />
        <TextInput value={item.label} onChange={(next) => update(index, "label", next)} path={`${path}.${index}.label`} label="Display label" />
      </div>
      <button type="button" className="small danger" onClick={() => onChange(labels.filter((_, itemIndex) => itemIndex !== index))}>{editorLabel("Remove")}</button>
    </article>)}
  </section>;
}
