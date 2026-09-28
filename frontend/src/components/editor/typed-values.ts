export type TypedScalarValue = string | number | boolean;

export type TypedValueDomain = {
  value_type?: unknown;
  allowed_values?: unknown;
  value_labels?: unknown;
};

function isScalar(value: unknown): value is TypedScalarValue {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function valueLabelEntries(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

/** A typed token keeps enum option identity distinct even when values stringify alike. */
export function typedScalarToken(value: TypedScalarValue): string {
  return JSON.stringify([typeof value, value]);
}

/** Parse the exact wire scalar selected by a typed authoring control. */
export function parseTypedScalarInput(raw: string, valueType: unknown, allowedValues: unknown = []): TypedScalarValue | null {
  if (valueType === "BOOLEAN") return raw === "true" ? true : raw === "false" ? false : null;
  if (valueType === "INTEGER") {
    if (!/^-?(0|[1-9][0-9]*)$/.test(raw)) return null;
    const value = Number(raw);
    return Number.isSafeInteger(value) ? value : null;
  }
  if (valueType === "ENUM") {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (!Array.isArray(parsed) || parsed.length !== 2 || !isScalar(parsed[1]) || parsed[0] !== typeof parsed[1]) return null;
      const allowed = Array.isArray(allowedValues) ? allowedValues : [];
      return allowed.find((candidate) => isScalar(candidate) && Object.is(candidate, parsed[1])) as TypedScalarValue | undefined ?? null;
    } catch {
      return null;
    }
  }
  return raw;
}

/** Resolve only a label authored for this exact typed scalar value. */
export function authoredValueLabel(value: unknown, labels: unknown, variant: "summary" | "detail" = "detail"): string | null {
  const match = valueLabelEntries(labels).find((entry) => Object.is(entry.value, value));
  if (!match) return null;
  const candidates = variant === "summary"
    ? [match.summary_label, match.label, match.detail_label]
    : [match.detail_label, match.label, match.summary_label];
  return candidates.find((candidate): candidate is string => typeof candidate === "string" && candidate.length > 0) ?? null;
}

/** Author-facing display for a value whose domain is already known by its owner. */
export function typedScalarDisplay(value: unknown, domain: TypedValueDomain, variant: "summary" | "detail" = "detail"): string {
  if (typeof value === "boolean") return value ? "是" : "否";
  const authored = authoredValueLabel(value, domain.value_labels, variant);
  if (authored) return authored;
  if (value === null || value === undefined) return "未设置";
  if (domain.value_type === "ENUM" && typeof value === "string") {
    const allowed = Array.isArray(domain.allowed_values) ? domain.allowed_values : [];
    if (allowed.length === 2 && allowed.includes("AVAILABLE") && allowed.includes("UNAVAILABLE")) {
      return value === "AVAILABLE" ? "是" : value === "UNAVAILABLE" ? "否" : value;
    }
  }
  return String(value);
}
