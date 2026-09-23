import { useEffect, useId, useState, type ReactNode } from "react";

import { type ReferenceDomain } from "../../editor-registry";
import { displayEnumValue, editorLabel } from "../../ui";
import { moveItem, type MoveDirection } from "../../editor-order";
import { referenceOptions, type ReferenceOption } from "./ReferencePicker";
import { fieldId } from "./FormUtils";

export type ControlSize = "compact" | "standard" | "wide" | "full";

type FormFieldProps = {
  label: string;
  path: string;
  children: ReactNode;
  headingAddon?: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
  machineValue?: string;
  size?: ControlSize;
};

function normalizedLabel(label: string): string {
  return editorLabel(label);
}

export function FormField({ label, path, children, headingAddon, help, error, machineValue, size = "standard" }: FormFieldProps) {
  const id = fieldId(path);
  const required = label.endsWith(" *");
  const displayLabel = normalizedLabel(required ? label.slice(0, -2) : label);
  return <div className={`form-field form-field-${size}${error ? " form-field-error" : ""}`} data-field-path={path}>
    <div className="form-field-heading">
      <label htmlFor={id}>{displayLabel}{required && <> <span className="required-marker">*</span></>}</label>
      {machineValue && <code className="machine-key">{machineValue}</code>}
      {headingAddon}
    </div>
    {children}
    {help && <small className="typed-help">{help}</small>}
    {error && <small className="field-error" role="alert">{error}</small>}
  </div>;
}

type InputProps = {
  value: unknown;
  onChange: (value: string) => void;
  path: string;
  label: string;
  help?: ReactNode;
  error?: ReactNode;
  size?: ControlSize;
  placeholder?: string;
  readOnly?: boolean;
};

export function TextInput({ value, onChange, path, label, help, error, size = "standard", placeholder, readOnly = false }: InputProps) {
  return <FormField label={label} path={path} help={help} error={error} size={size}>
    <input className={`editor-control${readOnly ? " is-readonly" : ""}`} id={fieldId(path)} value={typeof value === "string" ? value : ""} placeholder={placeholder} readOnly={readOnly} aria-readonly={readOnly || undefined} onChange={(event) => onChange(event.target.value)} />
  </FormField>;
}

export function TextArea({ value, onChange, path, label, help, error, size = "full", placeholder, readOnly = false }: InputProps) {
  return <FormField label={label} path={path} help={help} error={error} size={size}>
    <textarea className={`editor-control editor-textarea${readOnly ? " is-readonly" : ""}`} id={fieldId(path)} value={typeof value === "string" ? value : ""} placeholder={placeholder} readOnly={readOnly} aria-readonly={readOnly || undefined} rows={4} onChange={(event) => onChange(event.target.value)} />
  </FormField>;
}

type NumberInputProps = Omit<InputProps, "value" | "onChange"> & {
  value: unknown;
  onChange: (value: number | null) => void;
  integer?: boolean;
};

export function NumberInput({ value, onChange, path, label, help, error, size = "compact", integer = true }: NumberInputProps) {
  return <FormField label={label} path={path} help={help} error={error} size={size}>
    <input className="editor-control" id={fieldId(path)} type="number" step={integer ? 1 : "any"} value={typeof value === "number" ? value : ""} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />
  </FormField>;
}

type BooleanControlProps = Omit<FormFieldProps, "children"> & {
  value: unknown;
  onChange: (value: boolean) => void;
  layout?: "form" | "stacked";
};

export function BooleanControl({ value, onChange, label, path, headingAddon, help, error, machineValue, size = "compact", layout = "form" }: BooleanControlProps) {
  const id = fieldId(path);
  const control = <select aria-label={layout === "stacked" ? normalizedLabel(label) : undefined} className={layout === "stacked" ? undefined : "editor-control"} id={id} value={value === true ? "true" : "false"} onChange={(event) => onChange(event.target.value === "true")}>
      <option value="true">是</option>
      <option value="false">否</option>
    </select>;
  if (layout === "stacked") return <label className={`initialization-field${error ? " form-field-error" : ""}`} data-field-path={path}>
    <span>{normalizedLabel(label)}{machineValue && <code className="machine-key">{machineValue}</code>}{headingAddon}</span>
    {control}
    {help && <small className="typed-help">{help}</small>}
    {error && <small className="field-error" role="alert">{error}</small>}
  </label>;
  return <FormField label={label} path={path} headingAddon={headingAddon} help={help} error={error} machineValue={machineValue} size={size}>
    {control}
  </FormField>;
}

type SelectProps = Omit<FormFieldProps, "children"> & {
  value: unknown;
  onChange: (value: string) => void;
  options: readonly ReferenceOption[];
  placeholder?: string;
  showMachineValue?: boolean;
  disabled?: boolean;
};

export function OptionSelect({ value, onChange, options, label, path, help, error, machineValue, size = "standard", placeholder = "请选择…", showMachineValue = true, disabled = false }: SelectProps) {
  const selected = typeof value === "string" ? value : "";
  return <FormField label={label} path={path} help={help} error={error} machineValue={machineValue} size={size}>
    <select className={`editor-control${disabled ? " is-readonly" : ""}`} id={fieldId(path)} value={selected} disabled={disabled} aria-readonly={disabled || undefined} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {options.map((option) => <option key={option.key} value={option.key}>{option.name}{showMachineValue ? ` · ${option.key}` : ""}</option>)}
      {selected && !options.some((option) => option.key === selected) && <option value={selected}>{selected}（当前引用无法解析）</option>}
    </select>
  </FormField>;
}

type EnumSelectProps = Omit<SelectProps, "options"> & { choices: readonly string[] };

export function EnumSelect({ choices, ...props }: EnumSelectProps) {
  return <OptionSelect {...props} showMachineValue={false} options={choices.map((item) => ({ key: item, name: displayEnumValue("generic", item) }))} />;
}

type ReferencePickerProps = Omit<SelectProps, "options"> & {
  domain: ReferenceDomain;
  document: Record<string, unknown>;
  options?: readonly ReferenceOption[];
};

export function ReferencePicker({ document, domain, options, ...props }: ReferencePickerProps) {
  return <OptionSelect {...props} options={options ?? referenceOptions(document, domain)} />;
}

type MultiValuePickerProps = Omit<FormFieldProps, "children"> & {
  value: unknown;
  onChange: (value: string[]) => void;
  options: readonly ReferenceOption[];
  emptyLabel?: string;
};

export function MultiValuePicker({ value, onChange, options, label, path, help, error, machineValue, size = "wide", emptyLabel = "未选择" }: MultiValuePickerProps) {
  const selected = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputId = `${fieldId(path)}-search`;
  const selectedSet = new Set(selected);
  const available = options.filter((option) => !selectedSet.has(option.key) && `${option.name} ${option.key}`.toLowerCase().includes(query.trim().toLowerCase()));
  const optionFor = (key: string): ReferenceOption => options.find((option) => option.key === key) ?? { key, name: key };
  const add = (key: string) => {
    if (!selectedSet.has(key)) onChange([...selected, key]);
    setQuery("");
  };
  const remove = (key: string) => onChange(selected.filter((item) => item !== key));

  return <FormField label={label} path={path} help={help} error={error} machineValue={machineValue} size={size}>
    <div className="multi-value-picker">
      <div className="multi-value-chips" aria-live="polite">
        {selected.map((key) => <span className="value-chip" key={key}>{optionFor(key).name}<code>{key}</code><button type="button" aria-label={`${optionFor(key).name} 移除`} onClick={() => remove(key)}>×</button></span>)}
        {selected.length === 0 && <span className="multi-value-empty">{emptyLabel}</span>}
      </div>
      <button id={fieldId(path)} type="button" className="picker-trigger" aria-label={`${normalizedLabel(label)}：添加`} aria-expanded={open} aria-controls={`${inputId}-list`} onClick={() => setOpen((current) => !current)}>＋ 添加</button>
      {open && <div className="multi-value-menu" id={`${inputId}-list`} role="listbox">
        <input className="editor-control picker-search" id={inputId} value={query} placeholder="搜索…" aria-label="搜索可选值" onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "Enter" && available[0]) { event.preventDefault(); add(available[0].key); }
        }} autoFocus />
        {available.length === 0 ? <p className="multi-value-empty">没有匹配项</p> : available.map((option) => <button type="button" role="option" aria-selected={false} className="picker-option" key={option.key} onClick={() => add(option.key)}>{option.name}<code>{option.key}</code></button>)}
      </div>}
    </div>
  </FormField>;
}

type ScalarValue = string | number | boolean;
type ScalarListEditorProps = Omit<FormFieldProps, "children"> & {
  value: unknown;
  onChange: (value: ScalarValue[]) => void;
};

export function ScalarListEditor({ value, onChange, label, path, help, error }: ScalarListEditorProps) {
  const items = Array.isArray(value) ? value.filter((item): item is ScalarValue => ["string", "number", "boolean"].includes(typeof item)) : [];
  const sample = items[0];
  const emptyValue: ScalarValue = typeof sample === "number" ? 0 : typeof sample === "boolean" ? false : "";
  return <section className="typed-array scalar-list-editor" aria-label={normalizedLabel(label)}>
    <div className="typed-array-heading"><h4>{normalizedLabel(label)}</h4><button type="button" className="small" onClick={() => onChange([...items, emptyValue])}>＋ 添加</button></div>
    {items.length === 0 && <p className="muted">暂无内容。</p>}
    {items.map((item, index) => <div className="typed-array-row" key={`${path}.${index}`}>
      {typeof item === "boolean" ? <BooleanControl label={`${label} ${index + 1}`} path={`${path}.${index}`} value={item} onChange={(next) => onChange(items.map((old, oldIndex) => oldIndex === index ? next : old))} /> : typeof item === "number" ? <NumberInput label={`${label} ${index + 1}`} path={`${path}.${index}`} value={item} onChange={(next) => onChange(items.map((old, oldIndex) => oldIndex === index ? next ?? 0 : old))} /> : <TextInput label={`${label} ${index + 1}`} path={`${path}.${index}`} value={item} onChange={(next) => onChange(items.map((old, oldIndex) => oldIndex === index ? next : old))} />}
      <span className="typed-array-actions"><ReorderControls index={index} count={items.length} onMove={(direction) => onChange(moveItem(items, index, direction))} /><button type="button" className="small danger" onClick={() => onChange(items.filter((_, oldIndex) => oldIndex !== index))}>移除</button></span>
    </div>)}
    {help && <small className="typed-help">{help}</small>}
    {error && <small className="field-error" role="alert">{error}</small>}
  </section>;
}

type ReorderControlsProps = {
  index: number;
  count: number;
  onMove?: (direction: MoveDirection) => void;
  className?: string;
};

export function ReorderControls({ index, count, onMove, className = "" }: ReorderControlsProps) {
  if (!onMove || count < 2) return null;
  return <span className={`reorder-controls${className ? ` ${className}` : ""}`} aria-label="调整顺序">
    <button type="button" className="small reorder-button" title="上移" aria-label="上移" disabled={index === 0} onClick={(event) => { event.stopPropagation(); onMove("up"); }}>↑</button>
    <button type="button" className="small reorder-button" title="下移" aria-label="下移" disabled={index === count - 1} onClick={(event) => { event.stopPropagation(); onMove("down"); }}>↓</button>
  </span>;
}

type NestedCardProps = {
  title: string;
  summary?: ReactNode;
  machineKey?: string;
  typeLabel?: string;
  identity?: ReactNode;
  children: ReactNode;
  onAdd?: () => void;
  onRemove?: () => void;
  removeLabel?: string;
  onMove?: (direction: "up" | "down") => void;
  moveIndex?: number;
  moveCount?: number;
  defaultExpanded?: boolean;
  expanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
};

type NestedObjectHeaderProps = {
  typeLabel: string;
  identity?: ReactNode;
};

export function NestedObjectHeader({ typeLabel, identity }: NestedObjectHeaderProps) {
  const hasIdentity = identity !== undefined && identity !== null && identity !== "";
  return <span className="nested-object-heading"><span className="nested-object-type">{normalizedLabel(typeLabel)}</span>{hasIdentity && <strong className="nested-object-identity">{identity}</strong>}</span>;
}

export function NestedCard({ title, summary, machineKey, typeLabel, identity, children, onAdd, onRemove, removeLabel = "删除", onMove, moveIndex, moveCount, defaultExpanded = false, expanded: controlledExpanded, onExpandedChange }: NestedCardProps) {
  const [localExpanded, setLocalExpanded] = useState(defaultExpanded);
  const expanded = controlledExpanded ?? localExpanded;
  const setExpanded = (next: boolean) => {
    if (controlledExpanded === undefined) setLocalExpanded(next);
    onExpandedChange?.(next);
  };
  const toggleId = useId();
  const titleParts = title.split(" · ");
  const resolvedTypeLabel = typeLabel ?? titleParts[0];
  const resolvedIdentity = identity ?? (titleParts.length > 1 ? titleParts.slice(1).join(" · ") : machineKey);
  return <article className={`nested-editor nested-card${expanded ? " is-expanded" : ""}`}>
    <header className="nested-card-header">
      <button type="button" className="nested-card-toggle" aria-expanded={expanded} aria-controls={toggleId} onClick={() => setExpanded(!expanded)}>
        <span className="nested-card-title"><NestedObjectHeader typeLabel={resolvedTypeLabel} identity={resolvedIdentity} /></span>
        {summary && <span className="nested-card-summary">{summary}</span>}
      </button>
      <span className="button-row nested-card-actions">
        {onAdd && <button type="button" className="small" onClick={onAdd}>＋</button>}
        <ReorderControls index={moveIndex ?? 0} count={moveCount ?? 1} onMove={onMove} />
        {onRemove && <button type="button" className="small danger" onClick={onRemove}>{removeLabel}</button>}
      </span>
    </header>
    {expanded && <div className="nested-card-body" id={toggleId}>{children}</div>}
  </article>;
}

type AdvancedSectionProps = {
  value: unknown;
  onChange: (value: unknown) => void;
  path: string;
  label: string;
  help?: ReactNode;
  defaultExpanded?: boolean;
};

export function AdvancedSection({ value, onChange, path, label, help, defaultExpanded = false }: AdvancedSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [error, setError] = useState<string | null>(null);
  const panelId = useId();
  useEffect(() => setText(JSON.stringify(value ?? null, null, 2)), [value]);
  const commit = () => {
    try {
      setError(null);
      onChange(JSON.parse(text));
    } catch {
      setError("JSON 结构暂时无法解析，当前内容未写回草稿。");
    }
  };
  return <section className={`advanced-json-field advanced-section${expanded ? " is-expanded" : ""}`}>
    <button type="button" className="advanced-section-toggle" aria-expanded={expanded} aria-controls={panelId} onClick={() => setExpanded((current) => !current)}><span>{normalizedLabel(label)}</span><em>高级结构</em><span aria-hidden="true">{expanded ? "−" : "+"}</span></button>
    {!expanded && <small className="typed-help">高级结构已折叠；未展开时保持原内容。</small>}
    {expanded && <div id={panelId} className="advanced-section-body"><label className="advanced-json-label" htmlFor={fieldId(path)}>{normalizedLabel(label)} JSON</label><textarea className="editor-control advanced-json-control" id={fieldId(path)} rows={Math.min(14, Math.max(4, text.split("\n").length))} value={text} onChange={(event) => setText(event.target.value)} onBlur={commit} />{help && <small className="typed-help">{help}</small>}{error && <small className="field-error" role="alert">{error}</small>}</div>}
  </section>;
}
