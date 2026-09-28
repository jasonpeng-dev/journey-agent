import { useState, type FormEvent } from "react";

import { EditorDialog } from "./EditorDialog";
import { AuthoringActionButton, FieldActionRow } from "./AuthoringActionButton";
import { BooleanControl, NumberInput, OptionSelect, TextInput } from "./FormPrimitives";
import { parseTypedScalarInput, typedScalarDisplay, typedScalarToken, type TypedValueDomain } from "./typed-values";

const UNSELECTED_REQUIRED_REFERENCE = "__identity_creation_unselected__";

export type IdentityOption = { key: string; name: string };
export type IdentityCreationValues = Record<string, string>;
type Conditional<T> = T | ((values: IdentityCreationValues) => T);

export type IdentityCreationField = {
  key: string;
  label: string;
  type?: "text" | "select" | "textarea" | "typed-scalar";
  required?: Conditional<boolean>;
  visible?: (values: IdentityCreationValues) => boolean;
  help?: string;
  placeholder?: string;
  pattern?: string;
  options?: readonly IdentityOption[] | ((values: IdentityCreationValues) => readonly IdentityOption[]);
  scalarDomain?: (values: IdentityCreationValues) => TypedValueDomain | null;
  resetOnChange?: readonly string[];
  emptyMessage?: string;
  ownerHref?: string | ((values: IdentityCreationValues) => string);
  ownerLabel?: string;
  omitEmptyOption?: boolean;
};

type Props = {
  title: string;
  fields: readonly IdentityCreationField[];
  onCancel: () => void;
  onCreate: (values: IdentityCreationValues) => string | null | void;
  validate?: (values: IdentityCreationValues) => string | null;
  submitLabel?: string;
};

function requiredFor(field: IdentityCreationField, values: IdentityCreationValues): boolean {
  return typeof field.required === "function" ? field.required(values) : Boolean(field.required);
}

function visibleFor(field: IdentityCreationField, values: IdentityCreationValues): boolean {
  return field.visible?.(values) ?? true;
}

function optionsFor(field: IdentityCreationField, values: IdentityCreationValues): readonly IdentityOption[] {
  return typeof field.options === "function" ? field.options(values) : field.options ?? [];
}

export function IdentityCreationDialog({ title, fields, onCancel, onCreate, validate, submitLabel = "创建" }: Props) {
  const [values, setValues] = useState<IdentityCreationValues>(() => Object.fromEntries(fields.map((field) => [field.key, field.type === "select" && field.omitEmptyOption ? UNSELECTED_REQUIRED_REFERENCE : ""])));
  const [error, setError] = useState<string | null>(null);
  const update = (key: string, value: string) => setValues((current) => {
    const next = { ...current, [key]: value };
    for (const field of fields) if (field.resetOnChange?.includes(key)) next[field.key] = "";
    return next;
  });

  const requiredFieldsComplete = fields.filter((field) => visibleFor(field, values) && requiredFor(field, values)).every((field) => {
    const raw = values[field.key] ?? "";
    if (!raw.trim()) return false;
    if (field.type === "select") return optionsFor(field, values).some((option) => option.key === raw);
    if (field.type === "typed-scalar") {
      const domain = field.scalarDomain?.(values);
      return Boolean(domain && parseTypedScalarInput(raw, domain.value_type, domain.allowed_values) !== null);
    }
    return true;
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    for (const field of fields) {
      if (!visibleFor(field, values) || !requiredFor(field, values)) continue;
      const value = values[field.key]?.trim() ?? "";
      if (!value) {
        setError(`请填写${field.label}。`);
        return;
      }
      if (field.type === "select" && !optionsFor(field, values).some((option) => option.key === value)) {
        setError(field.emptyMessage ?? `请选择有效的${field.label}。`);
        return;
      }
      if (field.type === "typed-scalar") {
        const domain = field.scalarDomain?.(values);
        if (!domain || parseTypedScalarInput(value, domain.value_type, domain.allowed_values) === null) {
          setError(`请填写符合“${field.label}”类型的值。`);
          return;
        }
      }
    }
    const validationError = validate?.(values);
    if (validationError) {
      setError(validationError);
      return;
    }
    const creationError = onCreate(values);
    setError(creationError ?? null);
  };

  return <EditorDialog titleId="identity-creation-dialog-title" kicker="创建身份" title={title} onClose={onCancel} footer={<>
    <button type="button" className="editor-button editor-button-secondary" onClick={onCancel}>取消</button>
    <button type="submit" form="identity-creation-form" className="editor-button editor-button-primary" disabled={!requiredFieldsComplete}>{submitLabel}</button>
  </>}>
    <form id="identity-creation-form" className="identity-creation-form" onSubmit={submit}>
      {fields.filter((field) => visibleFor(field, values)).map((field, index) => {
        const options = optionsFor(field, values);
        const ownerHref = typeof field.ownerHref === "function" ? field.ownerHref(values) : field.ownerHref;
        const controlId = `identity-create-${index}-${field.key}`;
        const required = requiredFor(field, values);

        if (field.type === "typed-scalar") {
          const domain = field.scalarDomain?.(values);
          const raw = values[field.key] ?? "";
          const valueType = domain?.value_type;
          const allowed = Array.isArray(domain?.allowed_values) ? domain.allowed_values : [];
          if (valueType === "BOOLEAN") return <BooleanControl key={field.key} path={controlId} label={field.label} value={raw === "true" ? true : raw === "false" ? false : null} required={required} error={required && raw === "" ? "请选择“是”或“否”。" : undefined} allowEmpty onEmpty={() => update(field.key, "")} emptyLabel="请选择…" help={field.help} onChange={(value) => update(field.key, String(value))} />;
          if (valueType === "ENUM") return <OptionSelect key={field.key} path={controlId} label={field.label} value={raw} required={required} error={required && raw === "" ? "请选择一个要求值。" : undefined} placeholder="请选择…" help={field.help} showMachineValue={false} options={allowed.filter((candidate): candidate is string | number | boolean => typeof candidate === "string" || typeof candidate === "number" || typeof candidate === "boolean").map((candidate) => ({ key: typedScalarToken(candidate), name: typedScalarDisplay(candidate, domain ?? {}) }))} onChange={(value) => update(field.key, value)} />;
          if (valueType === "INTEGER") return <NumberInput key={field.key} path={controlId} label={field.label} value={raw === "" ? null : Number(raw)} required={required} error={required && raw === "" ? "请填写整数。" : undefined} integer help={field.help} onChange={(value) => update(field.key, value === null ? "" : String(value))} />;
          if (valueType === "STRING") return <TextInput key={field.key} path={controlId} label={field.label} value={raw} required={required} error={required && !raw.trim() ? "请填写此字段。" : undefined} placeholder={field.placeholder} help={field.help} onChange={(value) => update(field.key, value)} />;
          return <div className="initialization-field" key={field.key}><span>{field.label}{required && <> <span className="required-marker">*</span></>}</span><select id={controlId} disabled aria-label={field.label} value=""><option value="">请先选择目标对象</option></select>{field.help && <small className="typed-help">{field.help}</small>}</div>;
        }

        return <div className="initialization-field" key={field.key}>
          <span><label htmlFor={controlId}>{field.label}{required && <> <span className="required-marker">*</span></>}</label></span>
          {field.type === "select" ? <>
            <select id={controlId} value={values[field.key] ?? ""} required={required} autoFocus={index === 0} onChange={(event) => update(field.key, event.target.value)}>
              {field.omitEmptyOption && <option value={UNSELECTED_REQUIRED_REFERENCE} disabled>请选择已有项</option>}
              {!field.omitEmptyOption && <option value="">{field.placeholder ?? "请选择"}</option>}
              {options.map((option) => <option key={option.key} value={option.key}>{option.name}</option>)}
            </select>
            {options.length === 0 && <><small className="typed-help">{field.emptyMessage ?? "暂无可选项。"}</small>{ownerHref && <FieldActionRow><AuthoringActionButton intent="navigate" to={ownerHref} onPress={onCancel}>{field.ownerLabel ?? "前往所属对象"}</AuthoringActionButton></FieldActionRow>}</>}
          </> : field.type === "textarea" ? <textarea id={controlId} value={values[field.key] ?? ""} required={required} autoFocus={index === 0} onChange={(event) => update(field.key, event.target.value)} /> : <input id={controlId} value={values[field.key] ?? ""} required={required} autoFocus={index === 0} placeholder={field.placeholder} pattern={field.pattern ?? ((field.key === "key" || field.key === "pool_key" || field.key === "role") ? "[a-z][a-z0-9_]{0,79}" : undefined)} onChange={(event) => update(field.key, event.target.value)} />}
          {field.help && <small className="typed-help">{field.help}</small>}
        </div>;
      })}
      {error && <p className="field-error" role="alert">{error}</p>}
    </form>
  </EditorDialog>;
}
