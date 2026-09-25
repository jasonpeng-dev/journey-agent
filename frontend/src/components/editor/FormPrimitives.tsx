import { useEffect, useId, useState, type ReactNode } from "react";

import { type ReferenceDomain } from "../../editor-registry";
import { editorLabel, platformEnumLabel, type PlatformEnumDomain } from "../../ui";
import { moveItem, type MoveDirection } from "../../editor-order";
import { referenceOptions, type ReferenceOption } from "./ReferencePicker";
import { fieldId } from "./FormUtils";
import { AuthoringActionButton, FieldActionRow } from "./AuthoringActionButton";

export type ControlSize = "compact" | "standard" | "wide" | "full";

type FormFieldProps = {
  label: string;
  path: string;
  children: ReactNode;
  required?: boolean;
  headingAddon?: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
  machineValue?: string;
  size?: ControlSize;
};

function normalizedLabel(label: string): string {
  return editorLabel(label);
}

export function FormField({ label, path, children, headingAddon, help, error, machineValue, required: explicitRequired, size = "standard" }: FormFieldProps) {
  const id = fieldId(path);
  const required = explicitRequired ?? label.endsWith(" *");
  const displayLabel = normalizedLabel(required && label.endsWith(" *") ? label.slice(0, -2) : label);
  return <div className={`form-field form-field-${size}${error ? " form-field-error" : ""}`} data-field-path={path}>
    <div className="form-field-heading">
      <label htmlFor={id}>{displayLabel}{required && <> <span className="required-marker">*</span></>}</label>
      {machineValue && <code className="machine-key">{machineValue}</code>}
    </div>
    {children}
    {help && <small className="typed-help">{help}</small>}
    {error && <small className="field-error" role="alert">{error}</small>}
    {headingAddon && <FieldActionRow>{headingAddon}</FieldActionRow>}
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
  required?: boolean;
  headingAddon?: ReactNode;
};

function requiredValueError(value: unknown, required: boolean | undefined, minItems?: number): string | undefined {
  if (!required) return undefined;
  if (value === null || value === undefined) return "此项为必填内容。";
  if (typeof value === "string" && value.trim() === "") return "此项为必填内容。";
  if (Array.isArray(value) && minItems !== undefined && value.length < minItems) return `至少需要 ${minItems} 个值。`;
  return undefined;
}

export function TextInput({ value, onChange, path, label, help, error, size = "standard", placeholder, readOnly = false, required, headingAddon }: InputProps) {
  return <FormField label={label} path={path} help={help} error={error ?? requiredValueError(value, required)} required={required} headingAddon={headingAddon} size={size}>
    <input className={`editor-control${readOnly ? " is-readonly" : ""}`} id={fieldId(path)} value={typeof value === "string" ? value : ""} placeholder={placeholder} readOnly={readOnly} aria-readonly={readOnly || undefined} onChange={(event) => onChange(event.target.value)} />
  </FormField>;
}

export function TextArea({ value, onChange, path, label, help, error, size = "full", placeholder, readOnly = false, required, headingAddon }: InputProps) {
  return <FormField label={label} path={path} help={help} error={error ?? requiredValueError(value, required)} required={required} headingAddon={headingAddon} size={size}>
    <textarea className={`editor-control editor-textarea${readOnly ? " is-readonly" : ""}`} id={fieldId(path)} value={typeof value === "string" ? value : ""} placeholder={placeholder} readOnly={readOnly} aria-readonly={readOnly || undefined} rows={4} onChange={(event) => onChange(event.target.value)} />
  </FormField>;
}

export function ReadonlyFieldDisplay({ value, className = "", ariaLabel }: { value: ReactNode; className?: string; ariaLabel?: string }) {
  return <output className={`readonly-field-display${className ? ` ${className}` : ""}`} aria-label={ariaLabel}>{value}</output>;
}

export function IdentityDisplay({ value, path, label, help, secondaryLabel, secondaryValue }: { value: unknown; path?: string; label: string; help?: ReactNode; secondaryLabel?: string; secondaryValue?: string }) {
  const identity = typeof value === "string" || typeof value === "number" ? String(value) : "";
  return <div className="identity-display identity-value-field" data-field-path={path}>
    <span className="identity-display-label">{normalizedLabel(label)}</span>
    <ReadonlyFieldDisplay value={identity || "—"} className="stable-identity-value" ariaLabel={normalizedLabel(label)} />
    {secondaryValue && <small className="identity-display-secondary"><span>{secondaryLabel ?? "辅助标识"}</span><ReadonlyFieldDisplay value={secondaryValue} className="stable-identity-value" ariaLabel={secondaryLabel ?? "辅助标识"} /> </small>}
    {help && <small className="identity-display-help">{help}</small>}
  </div>;
}

type InitializationFieldProps = {
  label: string;
  path: string;
  children: ReactNode;
  required?: boolean;
  headingAddon?: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
};

/** Shared Initialization field header and control wrapper for all input types. */
export function InitializationField({ label, path, children, required = false, headingAddon, help, error }: InitializationFieldProps) {
  return <label className={`initialization-field${error ? " form-field-error" : ""}`} data-field-path={path}>
    <span>{normalizedLabel(label)}{required && <> <span className="required-marker">*</span></>}{headingAddon}</span>
    {children}
    {help && <small className="typed-help">{help}</small>}
    {error && <small className="field-error" role="alert">{error}</small>}
  </label>;
}

// Retain the former export as an API alias while all identity UI shares one static primitive.
export const IdentityValue = IdentityDisplay;

type NumberInputProps = Omit<InputProps, "value" | "onChange"> & {
  value: unknown;
  onChange: (value: number | null) => void;
  integer?: boolean;
};

export function NumberInput({ value, onChange, path, label, help, error, size = "compact", integer = true, required, headingAddon }: NumberInputProps) {
  return <FormField label={label} path={path} help={help} error={error ?? requiredValueError(value, required)} required={required} headingAddon={headingAddon} size={size}>
    <input className="editor-control" id={fieldId(path)} type="number" step={integer ? 1 : "any"} value={typeof value === "number" ? value : ""} onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />
  </FormField>;
}

type BooleanControlProps = Omit<FormFieldProps, "children"> & {
  value: unknown;
  onChange: (value: boolean) => void;
  allowEmpty?: boolean;
  onEmpty?: () => void;
  emptyLabel?: string;
  layout?: "form" | "initialization";
};

export function BooleanControl({ value, onChange, label, path, headingAddon, help, error, machineValue, required, allowEmpty = false, onEmpty, emptyLabel = "请选择…", size = "compact", layout = "form" }: BooleanControlProps) {
  const id = fieldId(path);
  const resolvedError = error ?? requiredValueError(value, required);
  // Initialization-required booleans must always expose the two legal values;
  // authoring dialogs may still use an empty value while the row is incomplete.
  const canBeEmpty = allowEmpty && (layout !== "initialization" || !required);
  const control = <select aria-label={layout === "initialization" ? normalizedLabel(label) : undefined} className={layout === "initialization" ? undefined : "editor-control"} id={id} value={value === true ? "true" : value === false ? "false" : canBeEmpty ? "" : "false"} onChange={(event) => event.target.value === "" ? onEmpty?.() : onChange(event.target.value === "true")}>
      {canBeEmpty && <option value="">{emptyLabel}</option>}
      <option value="true">是</option>
      <option value="false">否</option>
    </select>;
  if (layout === "initialization") return <InitializationField label={label} path={path} headingAddon={headingAddon} help={help} error={resolvedError} required={required}>
    {control}
  </InitializationField>;
  return <FormField label={label} path={path} headingAddon={headingAddon} help={help} error={resolvedError} machineValue={machineValue} required={required} size={size}>
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

export function OptionSelect({ value, onChange, options, label, path, help, error, machineValue, required, headingAddon, size = "standard", placeholder = "请选择…", showMachineValue = true, disabled = false }: SelectProps) {
  const selected = typeof value === "string" ? value : "";
  const invalidSelection = Boolean(selected && !options.some((option) => option.key === selected));
  const resolvedError = error ?? (invalidSelection ? "请选择一个现有选项。" : requiredValueError(selected, required));
  return <FormField label={label} path={path} help={help} error={resolvedError} required={required} headingAddon={headingAddon} machineValue={machineValue} size={size}>
    <select className={`editor-control${disabled ? " is-readonly" : ""}`} id={fieldId(path)} value={selected} disabled={disabled} aria-readonly={disabled || undefined} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {options.map((option) => <option key={option.key} value={option.key}>{option.name}{showMachineValue ? ` · ${option.key}` : ""}</option>)}
      {selected && !options.some((option) => option.key === selected) && <option value={selected}>{selected}（当前引用无法解析）</option>}
    </select>
  </FormField>;
}

type EnumSelectProps = Omit<SelectProps, "options"> & { choices: readonly string[]; enumDomain?: PlatformEnumDomain };

export function EnumSelect({ choices, enumDomain, ...props }: EnumSelectProps) {
  return <OptionSelect {...props} showMachineValue={false} options={choices.map((item) => ({ key: item, name: platformEnumLabel(enumDomain, item) }))} />;
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
  minItems?: number;
};

export function MultiValuePicker({ value, onChange, options, label, path, help, error, machineValue, required, minItems, headingAddon, size = "wide", emptyLabel = "未选择" }: MultiValuePickerProps) {
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

  const invalidSelection = selected.some((key) => !options.some((option) => option.key === key));
  const resolvedError = error ?? (invalidSelection ? "请选择现有选项。" : requiredValueError(selected, required, minItems));
  return <FormField label={label} path={path} help={help} error={resolvedError} required={required} machineValue={machineValue} size={size}>
    <div className="multi-value-picker">
      <div className="multi-value-chips" aria-live="polite">
        {selected.map((key) => <span className="value-chip" key={key}>{optionFor(key).name}<code>{key}</code><button type="button" aria-label={`${optionFor(key).name} 移除`} onClick={() => remove(key)}>×</button></span>)}
        {selected.length === 0 && <span className="multi-value-empty">{emptyLabel}</span>}
      </div>
      <FieldActionRow className="multi-value-actions">
        <AuthoringActionButton id={fieldId(path)} intent="add" ariaLabel={`${normalizedLabel(label)}：添加`} ariaExpanded={open} ariaControls={`${inputId}-list`} onPress={() => setOpen((current) => !current)}>添加</AuthoringActionButton>
        {headingAddon}
      </FieldActionRow>
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
  minItems?: number;
};

export function ScalarListEditor({ value, onChange, label, path, help, error, required, minItems = 0 }: ScalarListEditorProps) {
  const items = Array.isArray(value) ? value.filter((item): item is ScalarValue => ["string", "number", "boolean"].includes(typeof item)) : [];
  const sample = items[0];
  const emptyValue: ScalarValue = typeof sample === "number" ? 0 : typeof sample === "boolean" ? false : "";
  const requiredMinimum = required ? Math.max(1, minItems) : minItems;
  const listError = error ?? (items.length < requiredMinimum ? `至少需要 ${requiredMinimum} 个值。` : undefined);
  return <section className={`typed-array scalar-list-editor${listError ? " form-field-error" : ""}`} aria-label={normalizedLabel(label)} data-field-path={path}>
    <div className="typed-array-heading"><h4>{normalizedLabel(label)}{required && <> <span className="required-marker">*</span></>}</h4><button type="button" className="small" onClick={() => onChange([...items, emptyValue])}>＋ 添加</button></div>
    {items.length === 0 && <p className="muted">暂无内容。</p>}
    {items.map((item, index) => <div className="typed-array-row" key={`${path}.${index}`}>
      {typeof item === "boolean" ? <BooleanControl label={`${label} ${index + 1}`} path={`${path}.${index}`} value={item} onChange={(next) => onChange(items.map((old, oldIndex) => oldIndex === index ? next : old))} /> : typeof item === "number" ? <NumberInput label={`${label} ${index + 1}`} path={`${path}.${index}`} value={item} onChange={(next) => onChange(items.map((old, oldIndex) => oldIndex === index ? next ?? 0 : old))} /> : <TextInput label={`${label} ${index + 1}`} path={`${path}.${index}`} value={item} onChange={(next) => onChange(items.map((old, oldIndex) => oldIndex === index ? next : old))} />}
      <span className="typed-array-actions"><ReorderControls index={index} count={items.length} onMove={(direction) => onChange(moveItem(items, index, direction))} /><button type="button" className="small danger" onClick={() => onChange(items.filter((_, oldIndex) => oldIndex !== index))}>移除</button></span>
    </div>)}
    {help && <small className="typed-help">{help}</small>}
    {listError && <small className="field-error" role="alert">{listError}</small>}
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
  focusPath?: string;
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

export function NestedCard({ title, summary, machineKey, typeLabel, identity, children, onAdd, onRemove, removeLabel = "删除", onMove, moveIndex, moveCount, defaultExpanded = false, focusPath, expanded: controlledExpanded, onExpandedChange }: NestedCardProps) {
  const [localExpanded, setLocalExpanded] = useState(defaultExpanded);
  const expanded = controlledExpanded ?? localExpanded;
  const setExpanded = (next: boolean) => {
    if (controlledExpanded === undefined) setLocalExpanded(next);
    onExpandedChange?.(next);
  };
  const toggleId = useId();
  useEffect(() => {
    if (defaultExpanded && controlledExpanded === undefined) setLocalExpanded(true);
  }, [controlledExpanded, defaultExpanded]);
  const titleParts = title.split(" · ");
  const resolvedTypeLabel = typeLabel ?? titleParts[0];
  const resolvedIdentity = identity ?? (titleParts.length > 1 ? titleParts.slice(1).join(" · ") : machineKey);
  return <article className={`nested-editor nested-card${expanded ? " is-expanded" : ""}`} data-focus-path={focusPath} tabIndex={focusPath ? -1 : undefined}>
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
  fallbackKind?: "unrecognized" | "compatibility";
};

export function AdvancedSection({ value, onChange, path, label, help, defaultExpanded = false, fallbackKind = "unrecognized" }: AdvancedSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [error, setError] = useState<string | null>(null);
  const panelId = useId();
  const fallbackLabel = fallbackKind === "compatibility" ? "兼容结构" : "未识别结构";
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
    <button type="button" className="advanced-section-toggle" aria-expanded={expanded} aria-controls={panelId} onClick={() => setExpanded((current) => !current)}><span>{fallbackLabel}</span><em>{fallbackKind === "compatibility" ? "兼容修复" : "未支持的结构"}</em><span aria-hidden="true">{expanded ? "−" : "+"}</span></button>
    <small className="typed-help">当前结构无法由可视编辑器完整编辑；此处 JSON 仅用于兼容或高级修复。已折叠时保持原内容。</small>
    {expanded && <div id={panelId} className="advanced-section-body"><label className="advanced-json-label" htmlFor={fieldId(path)}>{normalizedLabel(label)} JSON</label><textarea className="editor-control advanced-json-control" id={fieldId(path)} rows={Math.min(14, Math.max(4, text.split("\n").length))} value={text} onChange={(event) => setText(event.target.value)} onBlur={commit} />{help && <small className="typed-help">{help}</small>}{error && <small className="field-error" role="alert">{error}</small>}</div>}
  </section>;
}
