import { ApiError } from "../../api";
import { sectionForKind, type EntityKind, type JsonObject } from "../../editor";
import { editorLocatorFromValidation, editorLocatorHref, type EditorLocator } from "../../editor-locator";
import type { CompletenessItem, CompletenessResult, InitializationPreview, Locator, ValidationIssue, ValidationResult } from "../../types";
import { diagnosticMessage, fieldLabel, kindLabels } from "../../ui";
import { initializationPreviewIssuePresentations } from "./initialization-preview-errors";

export type AuthoringIssueSource = "completeness" | "reference" | "validation" | "initialization";
export type AuthoringIssue = {
  id: string;
  sources: AuthoringIssueSource[];
  domain: string;
  severity: "ERROR" | "WARNING";
  title: string;
  message: string;
  locator: Locator | null;
  referenceLocator: Locator | null;
  referenceOwner: string | null;
  href: string | null;
  actionLabel: string | null;
  ownerHref: string | null;
  ownerActionLabel: string | null;
  blocksValidation: boolean;
  blocksPublish: boolean;
  blocksInitialization: boolean;
};

type ProjectionInput = {
  result: CompletenessResult;
  document: JsonObject;
  scenarioId: string;
  initializationPreview?: InitializationPreview | null;
  initializationError?: unknown;
  currentValidation?: ValidationResult | null;
};

const entityKinds = new Set<EntityKind>([
  "node_type", "node", "relation_type", "relation", "resource", "role", "actor",
  "interaction", "action", "rule", "derived_state", "public_reference",
]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function objectLocator(locator: Locator | null | undefined): Locator | null {
  if (!locator || typeof locator.object_kind !== "string") return null;
  return {
    object_kind: locator.object_kind,
    object_key: typeof locator.object_key === "string" ? locator.object_key : null,
    field_path: typeof locator.field_path === "string" ? locator.field_path : null,
  };
}

function locatorKey(locator: Locator | null): string {
  return locator ? `${locator.object_kind}:${locator.object_key ?? ""}:${locator.field_path ?? ""}` : "unlocated";
}

const collectionForKind: Record<string, { root: string[]; identity: string }> = {
  node_type: { root: ["world", "node_types"], identity: "key" },
  node: { root: ["world", "nodes"], identity: "key" },
  relation_type: { root: ["world", "relation_types"], identity: "key" },
  relation: { root: ["world", "relations"], identity: "key" },
  resource: { root: ["world", "resources"], identity: "key" },
  role: { root: ["actors", "roles"], identity: "key" },
  actor: { root: ["actors", "actor_profiles"], identity: "key" },
  interaction: { root: ["interactions"], identity: "key" },
  action: { root: ["actions"], identity: "key" },
  rule: { root: ["rules"], identity: "key" },
  derived_state: { root: ["derived_states"], identity: "key" },
  public_reference: { root: ["public_references"], identity: "term" },
};

function fieldValue(document: JsonObject, locator: Locator | null): { known: boolean; value: unknown } {
  if (!locator?.object_key || !locator.field_path) return { known: false, value: undefined };
  const definition = collectionForKind[locator.object_kind];
  if (!definition) return { known: false, value: undefined };
  let collection: unknown = document;
  for (const part of definition.root) collection = asRecord(collection)?.[part];
  if (!Array.isArray(collection)) return { known: false, value: undefined };
  const entity = collection.map(asRecord).find((candidate) => {
    if (candidate?.[definition.identity] === locator.object_key) return true;
    return locator.object_kind === "relation"
      && [candidate?.source_node_key, candidate?.relation_type_key, candidate?.target_node_key].map(String).join("__") === locator.object_key;
  });
  if (!entity) return { known: false, value: undefined };

  let value: unknown = entity;
  for (const part of locator.field_path.split(".").filter(Boolean)) {
    if (Array.isArray(value)) {
      if (!/^\d+$/.test(part)) return { known: false, value: undefined };
      value = value[Number(part)];
      continue;
    }
    const parent = asRecord(value);
    if (!parent) return { known: false, value: undefined };
    value = parent[part];
  }
  return { known: true, value };
}

function isEmptyLocatedField(document: JsonObject, locator: Locator | null): boolean {
  const resolved = fieldValue(document, locator);
  return resolved.known && (
    resolved.value === null
    || resolved.value === undefined
    || (typeof resolved.value === "string" && !resolved.value.trim())
  );
}

function issueReasonKey(source: AuthoringIssueSource, code: string, type: string | null, message: string): string {
  if (code.startsWith("references.dangling") || /引用的对象不存在/.test(message)) return "missing-reference";
  if (type?.includes("missing") || /还需要填写|尚未填写|必填|必须/.test(message)) return "missing-value";
  if (type?.includes("enum") || type?.includes("literal") || type?.includes("pattern")) return `invalid-value:${type}`;
  if (source === "initialization" && /引用的/.test(message)) return "missing-reference";
  return code || message;
}

function domainFor(locator: Locator | null): string {
  const kind = locator?.object_kind ?? "";
  if (["node", "node_type", "relation", "relation_type", "resource", "resource_pool", "region_resource_knowledge", "legacy_resource"].includes(kind)) return "世界模型";
  if (["role", "actor"].includes(kind)) return "参与者";
  if (["interaction", "action", "rule"].includes(kind) || kind.startsWith("action_")) return "行为系统";
  if (["derived_state", "goal_resolution", "public_reference"].includes(kind)) return "目标系统";
  if (kind === "planning") return "规划策略";
  if (kind === "initialization") return "初始化";
  if (kind === "metadata") return "概览";
  return "场景结构";
}

function ownerTarget(kind: string | null, document: JsonObject): EditorLocator | null {
  if (!kind) return null;
  if (kind === "initialization" || kind === "resource_pool" || kind === "region_resource_knowledge" || kind === "legacy_resource") {
    return { owner: "singleton", section: "initialization", fieldPath: null };
  }
  if (kind === "planning") return { owner: "singleton", section: "planning-recovery", fieldPath: null };
  if (kind === "metadata") return { owner: "singleton", section: "overview", fieldPath: null };
  if (!entityKinds.has(kind as EntityKind)) return null;
  return editorLocatorFromValidation({ object_kind: kind, object_key: null, field_path: null }, document)
    ?? { owner: "singleton", section: sectionForKind(kind), fieldPath: null };
}

function ownerActionLabel(owner: string | null): string | null {
  if (!owner) return null;
  if (owner === "initialization" || owner === "resource_pool" || owner === "region_resource_knowledge" || owner === "legacy_resource") return "前往初始化";
  if (owner === "planning") return "前往规划策略";
  if (owner === "metadata") return "前往场景概览";
  return `前往${kindLabels[owner] ?? "所属对象"}`;
}

function targetName(locator: Locator | null, document: JsonObject): string {
  if (!locator) return "场景结构";
  const kind = kindLabels[locator.object_kind] ?? "场景配置";
  if (locator.object_key) {
    const collection: Array<{ path: string[]; kind: string }> = [
      { path: ["world", "nodes"], kind: "node" }, { path: ["world", "node_types"], kind: "node_type" },
      { path: ["world", "relations"], kind: "relation" }, { path: ["world", "relation_types"], kind: "relation_type" },
      { path: ["world", "resources"], kind: "resource" }, { path: ["actors", "roles"], kind: "role" },
      { path: ["actors", "actor_profiles"], kind: "actor" }, { path: ["interactions"], kind: "interaction" },
      { path: ["actions"], kind: "action" }, { path: ["rules"], kind: "rule" },
      { path: ["derived_states"], kind: "derived_state" }, { path: ["public_references"], kind: "public_reference" },
    ];
    for (const entry of collection) {
      if (entry.kind !== locator.object_kind) continue;
      let values: unknown = document;
      for (const part of entry.path) values = asRecord(values)?.[part];
      const found = Array.isArray(values) ? values.map(asRecord).find((candidate) => candidate?.key === locator.object_key || candidate?.term === locator.object_key) : null;
      const name = found?.name ?? found?.term;
      if (typeof name === "string" && name.trim()) return `${kind}「${name}」`;
    }
    return `${kind}「${locator.object_key}」`;
  }
  return kind;
}

function fieldName(locator: Locator | null): string {
  const field = locator?.field_path?.split(".").filter(Boolean).at(-1);
  return field ? fieldLabel(field) : "相关配置";
}

function validationMessage(issue: ValidationIssue, locator: Locator | null): string {
  const field = fieldName(locator);
  const type = issue.type ?? "";
  if (issue.code === "SCENARIO_DOCUMENT_SCHEMA_INVALID") {
    if (type.includes("missing")) return `请填写${field}。`;
    if (type.includes("enum") || type.includes("literal")) return `请为${field}选择有效选项。`;
    if (type.includes("too_short") || type.includes("string_pattern") || type.includes("too_long")) return `请按要求完善${field}。`;
    return `请检查${field}的填写内容。`;
  }
  const localized = diagnosticMessage(issue.code, "");
  return localized || `${targetName(locator, {})}需要调整${field}后才能通过检查。`;
}

function completenessCopy(item: CompletenessItem): string {
  if (item.key.startsWith("references.dangling")) return "此字段引用的对象不存在或无法解析，请选择有效对象或移除该引用。";
  return item.message;
}

function makeIssue(input: Omit<AuthoringIssue, "id">): AuthoringIssue {
  return { ...input, id: `${locatorKey(input.locator)}:${input.title}:${input.message}` };
}

function addDeduplicated(target: Map<string, AuthoringIssue>, issue: AuthoringIssue, code: string, type: string | null, document: JsonObject): void {
  const reasonKey = isEmptyLocatedField(document, issue.locator)
    ? "missing-value"
    : issueReasonKey(issue.sources[0], code, type, issue.message);
  const key = `${locatorKey(issue.locator)}:${reasonKey}`;
  const existing = target.get(key);
  if (!existing) {
    target.set(key, issue);
    return;
  }
  const sourceSet = new Set([...existing.sources, ...issue.sources]);
  const actionScore = (candidate: AuthoringIssue) => Number(Boolean(candidate.href))
    + Number(Boolean(candidate.locator?.field_path))
    + Number(Boolean(candidate.actionLabel?.startsWith("定位到")));
  const preferIncoming = actionScore(issue) > actionScore(existing)
    || actionScore(issue) === actionScore(existing)
      && issue.sources.includes("validation")
      && !existing.sources.includes("validation");
  const preferred = preferIncoming ? issue : existing;
  target.set(key, {
    ...preferred,
    id: existing.id,
    sources: [...sourceSet],
    referenceLocator: preferred.referenceLocator ?? existing.referenceLocator ?? issue.referenceLocator,
    referenceOwner: preferred.referenceOwner ?? existing.referenceOwner ?? issue.referenceOwner,
    ownerHref: preferred.ownerHref ?? existing.ownerHref ?? issue.ownerHref,
    ownerActionLabel: preferred.ownerActionLabel ?? existing.ownerActionLabel ?? issue.ownerActionLabel,
    blocksValidation: existing.blocksValidation || issue.blocksValidation,
    blocksPublish: existing.blocksPublish || issue.blocksPublish,
    blocksInitialization: existing.blocksInitialization || issue.blocksInitialization,
  });
}

function hrefFor(locator: Locator | null, document: JsonObject, scenarioId: string): string | null {
  if (!locator) return null;
  const resolved = editorLocatorFromValidation(locator, document);
  return resolved ? editorLocatorHref(resolved, scenarioId) : null;
}

function completenessIssue(item: CompletenessItem, document: JsonObject, scenarioId: string): AuthoringIssue {
  const locator = objectLocator(item.locator);
  const referenceLocator = objectLocator(item.reference_locator);
  const isReference = item.key.startsWith("references.dangling");
  const isNodeType = locator?.object_kind === "node" && locator.field_path === "node_type_key";
  const referenceOwner = item.reference_owner ?? referenceLocator?.object_kind ?? (isNodeType ? "node_type" : null);
  const owner = ownerTarget(referenceOwner, document);
  const name = targetName(locator, document);
  const field = fieldName(locator);
  const href = hrefFor(locator, document, scenarioId);
  return makeIssue({
    sources: [isReference ? "reference" : "completeness"],
    domain: domainFor(locator),
    severity: item.level === "OPTIONAL_ENHANCEMENT" || item.level === "LEGACY_FALLBACK" ? "WARNING" : "ERROR",
    title: isReference ? `修正${name}的引用` : `${name} · ${field}`,
    message: completenessCopy(item),
    locator,
    referenceLocator,
    referenceOwner,
    href,
    actionLabel: href ? locator?.field_path ? `定位到${field}` : `前往${name}` : null,
    ownerHref: isNodeType && owner
      ? editorLocatorHref(owner, scenarioId)
      : referenceOwner && owner ? editorLocatorHref(owner, scenarioId) : null,
    ownerActionLabel: isNodeType ? "前往节点类型" : ownerActionLabel(referenceOwner),
    blocksValidation: item.level === "INCOMPLETE_REQUIRED",
    blocksPublish: item.level === "INCOMPLETE_REQUIRED" && item.dependency_kind !== "RECOMMENDED",
    blocksInitialization: false,
  });
}

function validationIssue(issue: ValidationIssue, source: "validation", document: JsonObject, scenarioId: string): AuthoringIssue {
  const validationOwners: Record<string, string> = { actions: "action" };
  const locator = objectLocator(issue.locator) ?? (validationOwners[issue.path.split(".")[0]]
    ? { object_kind: validationOwners[issue.path.split(".")[0]], object_key: null, field_path: null }
    : null);
  const owner = locator && !locator.object_key ? ownerTarget(locator.object_kind, document) : null;
  const href = hrefFor(locator, document, scenarioId) ?? (owner ? editorLocatorHref(owner, scenarioId) : null);
  const name = targetName(locator, document);
  const field = fieldName(locator);
  return makeIssue({
    sources: [source],
    domain: domainFor(locator),
    severity: issue.severity,
    title: `${name} · ${field}`,
    message: validationMessage(issue, locator),
    locator,
    referenceLocator: null,
    referenceOwner: null,
    href,
    actionLabel: href ? locator?.field_path ? `定位到${field}` : `前往${name}` : null,
    ownerHref: null,
    ownerActionLabel: null,
    blocksValidation: issue.severity === "ERROR",
    blocksPublish: issue.severity === "ERROR",
    blocksInitialization: false,
  });
}

function initializationIssues(preview: InitializationPreview | null | undefined, error: unknown): unknown[] {
  const output: unknown[] = [];
  if (Array.isArray(preview?.issues)) output.push(...preview.issues);
  if (Array.isArray(error)) output.push(...error);
  else if (error instanceof ApiError && error.code === "SCENARIO_INITIALIZATION_PREVIEW_INVALID") {
    const details = asRecord(error.details);
    if (Array.isArray(details?.issues)) output.push(...details.issues);
  }
  return output;
}

export function projectAuthoringIssues({ result, document, scenarioId, initializationPreview, initializationError, currentValidation }: ProjectionInput): AuthoringIssue[] {
  const issues = new Map<string, AuthoringIssue>();
  for (const item of result.items) {
    if (item.level === "COMPLETE" || item.key === "scenario-definition.validation") continue;
    const projected = completenessIssue(item, document, scenarioId);
    addDeduplicated(issues, projected, item.key, null, document);
  }

  const validationIssues = new Map<string, ValidationIssue>();
  for (const issue of result.validation_issues ?? []) validationIssues.set(`${issue.code}:${issue.path}:${issue.type ?? ""}`, issue);
  for (const issue of currentValidation?.issues ?? []) validationIssues.set(`${issue.code}:${issue.path}:${issue.type ?? ""}`, issue);
  for (const issue of validationIssues.values()) {
    const projected = validationIssue(issue, "validation", document, scenarioId);
    addDeduplicated(issues, projected, issue.code, issue.type ?? null, document);
  }

  const previewIssueSource = initializationIssues(initializationPreview, initializationError);
  const presentations = initializationPreviewIssuePresentations(previewIssueSource, null, document, scenarioId);
  for (const [index, presentation] of presentations.entries()) {
    const locator = objectLocator(presentation.locator);
    const referenceLocator = objectLocator(presentation.referenceLocator);
    const referenceOwner = presentation.referenceOwner ?? null;
    const isActionable = presentation.category !== "unresolved" && Boolean(presentation.href);
    const projected = makeIssue({
      sources: ["initialization"],
      domain: domainFor(locator),
      severity: "ERROR",
      title: presentation.title,
      message: presentation.reason,
      locator,
      referenceLocator,
      referenceOwner,
      href: isActionable ? presentation.href : null,
      actionLabel: isActionable ? presentation.actionLabel : null,
      ownerHref: presentation.ownerHref ?? null,
      ownerActionLabel: presentation.ownerActionLabel ?? null,
      blocksValidation: false,
      blocksPublish: false,
      blocksInitialization: true,
    });
    addDeduplicated(issues, projected, `initialization:${presentation.rawPath || index}`, null, document);
  }

  return [...issues.values()].sort((left, right) => {
    const severityOrder = (left.severity === "ERROR" ? 0 : 1) - (right.severity === "ERROR" ? 0 : 1);
    return severityOrder || left.domain.localeCompare(right.domain, "zh-CN") || left.title.localeCompare(right.title, "zh-CN");
  });
}
