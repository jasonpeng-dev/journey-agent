import { defaultArrayItem } from "../templates";

const enums: Record<string, string[]> = {
  initial_access: ["LOCKED", "AVAILABLE"], access: ["LOCKED", "AVAILABLE"],
  initial_visibility: ["KNOWN", "HIDDEN"], visibility: ["KNOWN", "HIDDEN"],
  capabilities: ["PLAN", "EXECUTE_ACTION", "INSPECT_STATE"], allowed_actor_capabilities: ["PLAN", "EXECUTE_ACTION", "INSPECT_STATE"],
  execution_mode: ["IMMEDIATE", "ASYNC"], phase: ["PREFLIGHT", "RESOLVE"],
  value_type: ["STRING", "ENUM", "INTEGER", "BOOLEAN"],
  kind: ["ALL", "ANY", "NOT", "FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE", "RESOURCE_COMPARE", "PARAMETER_COMPARE", "NODE_VISIBLE", "NODE_ACCESSIBLE", "RELATION_EXISTS", "SET_FACT", "REVEAL_FACT", "HIDE_FACT", "REVEAL_NODE", "HIDE_NODE", "SET_NODE_ACCESS", "ADJUST_RESOURCE", "RESERVE_RESOURCE", "RELEASE_RESOURCE", "EMIT_OUTCOME", "EMIT_FAILURE", "WRITE_MEMORY_EVENT"],
  operator: ["EQ", "NE", "LT", "LTE", "GT", "GTE"], source: ["LITERAL", "PARAMETER"], direction: ["SOURCE", "TARGET"], relation_direction: ["SOURCE", "TARGET"],
};

type Props = { value: unknown; onChange: (value: unknown) => void; field?: string; depth?: number };

export function StructuredEditor({ value, onChange, field = "value", depth = 0 }: Props) {
  if (Array.isArray(value)) {
    return <div className="structured-array">{value.map((item, index) => <div className="array-item" key={index}><StructuredEditor field={field} value={item} depth={depth + 1} onChange={(next) => onChange(value.map((old, oldIndex) => oldIndex === index ? next : old))} /><button className="small danger" onClick={() => onChange(value.filter((_, oldIndex) => oldIndex !== index))}>Remove</button></div>)}<button className="small" onClick={() => onChange([...value, defaultArrayItem(field)])}>+ Add</button></div>;
  }
  if (value && typeof value === "object") {
    return <div className={`structured-object depth-${Math.min(depth, 3)}`}>{Object.entries(value as Record<string, unknown>).map(([key, child]) => <label className="structured-field" key={key}><span>{key.replaceAll("_", " ")}</span><StructuredEditor field={key} value={child} depth={depth + 1} onChange={(next) => onChange({ ...(value as Record<string, unknown>), [key]: next })} /></label>)}</div>;
  }
  const choices = enums[field];
  if (choices && typeof value === "string") return <select value={value} onChange={(event) => onChange(event.target.value)}>{choices.map((item) => <option key={item}>{item}</option>)}</select>;
  if (typeof value === "boolean") return <input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} />;
  if (typeof value === "number") return <input type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} />;
  if (value === null) return <button className="small" onClick={() => onChange("")}>Set value</button>;
  return <input value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />;
}
