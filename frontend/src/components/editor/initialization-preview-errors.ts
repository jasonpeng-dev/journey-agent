import type { EntityKind, JsonObject } from "../../editor";
import { sectionForKind } from "../../editor";
import { editorLocatorFromValidation, editorLocatorHref, type EditorLocator } from "../../editor-locator";
import type { InitializationPreviewIssue, Locator } from "../../types";
import { fieldLabel, kindLabels } from "../../ui";
import { ApiError } from "../../api";

type StructuredIssue = InitializationPreviewIssue;
export type PreviewIssueCategory = "current" | "dependency" | "other" | "unresolved";
export type PreviewIssuePresentation = {
  category: PreviewIssueCategory;
  title: string;
  groupKey: string;
  groupTitle: string;
  problemLabel: string;
  reason: string;
  href: string;
  actionLabel: string;
  ownerHref?: string;
  ownerActionLabel?: string;
  locator: Locator | null;
  referenceLocator?: Locator | null;
  referenceOwner?: EntityKind | null;
  rawPath: string;
  rawMessage: string;
};

type IssueTarget = { locator: Locator; targetOwner?: EntityKind; dependencyLabel?: string };

const entityCollections: Array<{ path: string[]; kind: EntityKind }> = [
  { path: ["world", "nodes"], kind: "node" },
  { path: ["world", "node_types"], kind: "node_type" },
  { path: ["world", "relations"], kind: "relation" },
  { path: ["world", "relation_types"], kind: "relation_type" },
  { path: ["world", "resources"], kind: "resource" },
  { path: ["actors", "roles"], kind: "role" },
  { path: ["actors", "actor_profiles"], kind: "actor" },
  { path: ["interactions"], kind: "interaction" },
  { path: ["actions"], kind: "action" },
  { path: ["rules"], kind: "rule" },
  { path: ["derived_states"], kind: "derived_state" },
  { path: ["public_references"], kind: "public_reference" },
];

const initializationKindLabels: Record<string, string> = {
  resource_pool: "资源池",
  region_resource_knowledge: "区域资源知识",
  legacy_resource: "兼容资源",
};

const referenceOwners: Record<string, { kind: EntityKind; label: string }> = {
  "node-types": { kind: "node_type", label: "节点类型" },
  node_type: { kind: "node_type", label: "节点类型" },
  interactions: { kind: "interaction", label: "交互" },
  interaction: { kind: "interaction", label: "交互" },
  roles: { kind: "role", label: "角色" },
  role: { kind: "role", label: "角色" },
  "world-entities": { kind: "node", label: "节点" },
  node: { kind: "node", label: "节点" },
  actions: { kind: "action", label: "行动" },
  action: { kind: "action", label: "行动" },
  "relation-types": { kind: "relation_type", label: "关系类型" },
  relation_type: { kind: "relation_type", label: "关系类型" },
};

function asObject(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function atPath(document: JsonObject, path: string[]): unknown {
  return path.reduce<unknown>((value, key) => asObject(value)?.[key], document);
}

function collectionItem(document: JsonObject, path: string[], index: number): JsonObject | null {
  const values = atPath(document, path);
  return Array.isArray(values) ? asObject(values[index]) : null;
}

function targetFromStructuredMetadata(issue: StructuredIssue): IssueTarget | null {
  const metadata = asObject(issue.locator);
  const canonicalOwner = typeof issue.canonical_owner === "string" ? issue.canonical_owner : "";
  const objectKind = typeof metadata?.object_kind === "string"
    ? metadata.object_kind
    : canonicalOwner === "initialization" ? "initialization" : "";
  if (!objectKind || objectKind === "legacy_resource") return null;
  let objectKey = typeof metadata?.object_key === "string" ? metadata.object_key : null;
  const fieldPath = typeof issue.field_path === "string"
    ? issue.field_path
    : typeof metadata?.field_path === "string" ? metadata.field_path : null;
  if (objectKind === "node" && fieldPath?.startsWith("facts.") && objectKey?.includes(":")) {
    objectKey = objectKey.slice(0, objectKey.indexOf(":"));
  }
  const locator: Locator = { object_kind: objectKind, object_key: objectKey, field_path: fieldPath };
  const referenceOwner = typeof issue.reference_owner === "string" ? referenceOwners[issue.reference_owner] : undefined;
  return {
    locator,
    targetOwner: referenceOwner?.kind,
    dependencyLabel: referenceOwner?.label,
  };
}

function pathPart(value: unknown): string | null {
  if (typeof value === "string") return value;
  return typeof value === "number" && Number.isInteger(value) ? String(value) : null;
}

function stableNestedPath(kind: EntityKind, item: JsonObject, rest: string[]): string[] {
  if (kind === "node" && rest[0] === "facts" && /^\d+$/.test(rest[1] ?? "")) {
    const fact = asObject(Array.isArray(item.facts) ? item.facts[Number(rest[1])] : null);
    if (fact && typeof fact.key === "string") return ["facts", fact.key, ...rest.slice(2)];
  }
  if (kind === "action") {
    const identityField: Record<string, string> = { parameters: "key", expected_outcomes: "code", operation_bindings: "role" };
    const identityFieldName = identityField[rest[0] ?? ""];
    if (identityFieldName && /^\d+$/.test(rest[1] ?? "")) {
      const rows = Array.isArray(item[rest[0]]) ? item[rest[0]] as unknown[] : [];
      const row = asObject(rows[Number(rest[1])]);
      const identity = row?.[identityFieldName];
      if (typeof identity === "string") return [rest[0], identity, ...rest.slice(2)];
    }
  }
  return rest;
}

function targetFromLocation(issue: StructuredIssue, document: JsonObject): IssueTarget | null {
  if (!Array.isArray(issue.loc)) {
    const locator = asObject(issue.locator);
    if (typeof locator?.object_kind === "string") {
      return { locator: {
        object_kind: locator.object_kind,
        object_key: typeof locator.object_key === "string" ? locator.object_key : null,
        field_path: typeof locator.field_path === "string" ? locator.field_path : null,
      } };
    }
    return null;
  }
  const location = issue.loc.map(pathPart).filter((part): part is string => Boolean(part)).filter((part, index) => !(index === 0 && part === "body"));
  for (const collection of entityCollections) {
    if (collection.path.some((part, index) => location[index] !== part)) continue;
    const rawIndex = location[collection.path.length];
    if (!rawIndex || !/^\d+$/.test(rawIndex)) return null;
    const index = Number(rawIndex);
    const item = collectionItem(document, collection.path, index);
    if (!item || (collection.kind !== "relation" && typeof item.key !== "string")) return null;
    const rest = stableNestedPath(collection.kind, item, location.slice(collection.path.length + 1));
    const key: string = collection.kind === "relation"
      ? (typeof item.key === "string" && item.key) || [item.source_node_key, item.relation_type_key, item.target_node_key].map(String).join("__")
      : String(item.key);
    return { locator: { object_kind: collection.kind, object_key: key, field_path: rest.length > 0 ? rest.join(".") : null } };
  }

  const initializationCollection = location[0] === "initialization" ? location[1] : null;
  if (["resource_pools", "region_resource_knowledge", "resource_initial_states"].includes(initializationCollection ?? "")) {
    const rawIndex = location[2];
    const index = rawIndex && /^\d+$/.test(rawIndex) ? Number(rawIndex) : -1;
    const initialization = asObject(document.initialization);
    const rows = initialization?.[initializationCollection!] as unknown;
    const row = Array.isArray(rows) ? asObject(rows[index]) : null;
    if (row && index >= 0) {
      const rest = location.slice(3);
      if (initializationCollection === "resource_pools") {
        return { locator: { object_kind: "resource_pool", object_key: String(row.pool_key ?? "") || null, field_path: rest.join(".") || null } };
      }
      if (initializationCollection === "region_resource_knowledge") {
        return { locator: { object_kind: "region_resource_knowledge", object_key: String(row.region_key ?? "") || null, field_path: rest.join(".") || null } };
      }
      const resourceKey = String(row.resource_key ?? "");
      const scopeKey = String(row.scope_node_key ?? "global");
      return { locator: { object_kind: "legacy_resource", object_key: resourceKey ? `${resourceKey}::${scopeKey}` : null, field_path: rest.join(".") || null } };
    }
  }

  const root = location[0];
  const rootKinds: Record<string, string> = {
    metadata: "metadata", initialization: "initialization", goal_resolution: "goal_resolution", planning: "planning",
  };
  if (root && rootKinds[root]) {
    return { locator: { object_kind: rootKinds[root], object_key: null, field_path: location.slice(1).join(".") || null } };
  }
  return null;
}

function targetFromReferenceMessage(issue: StructuredIssue): IssueTarget | null {
  if (typeof issue.msg !== "string") return null;
  const message = issue.msg.replace(/^Value error,\s*/i, "");
  const patterns: Array<{ pattern: RegExp; kind: EntityKind; field: string; targetOwner: EntityKind; label: string }> = [
    { pattern: /^Node ([a-z][a-z0-9_]*) type references unknown key /i, kind: "node", field: "node_type_key", targetOwner: "node_type", label: "节点类型" },
    { pattern: /^Node ([a-z][a-z0-9_]*) Interaction references unknown key /i, kind: "node", field: "interaction_keys", targetOwner: "interaction", label: "交互" },
    { pattern: /^Actor ([a-z][a-z0-9_]*) Role references unknown key /i, kind: "actor", field: "role_key", targetOwner: "role", label: "角色" },
    { pattern: /^Actor ([a-z][a-z0-9_]*) initial Node references unknown key /i, kind: "actor", field: "initial_node_key", targetOwner: "node", label: "初始节点" },
    { pattern: /^Actor ([a-z][a-z0-9_]*) allowed Action references unknown key /i, kind: "actor", field: "allowed_action_keys", targetOwner: "action", label: "允许的行动" },
    { pattern: /^Action ([a-z][a-z0-9_]*) Interaction references unknown key /i, kind: "action", field: "required_interaction_key", targetOwner: "interaction", label: "交互" },
    { pattern: /^Action ([a-z][a-z0-9_]*) target Node type references unknown key /i, kind: "action", field: "target_node_type_keys", targetOwner: "node_type", label: "目标节点类型" },
    { pattern: /^Action ([a-z][a-z0-9_]*) required Actor Role references unknown key /i, kind: "action", field: "required_actor_role_key", targetOwner: "role", label: "所需角色" },
  ];
  for (const candidate of patterns) {
    const match = message.match(candidate.pattern);
    if (match) return {
      locator: { object_kind: candidate.kind, object_key: match[1], field_path: candidate.field },
      targetOwner: candidate.targetOwner,
      dependencyLabel: candidate.label,
    };
  }
  return null;
}

function problemLabel(locator: Locator, document: JsonObject): string {
  const path = locator.field_path?.split(".").filter(Boolean) ?? [];
  const last = path.at(-1) ?? "";
  const labels: Record<string, string> = {
    initial_access: "初始访问状态",
    initial_visibility: locator.object_kind === "relation" ? "关系可见性" : "初始可见性",
    initial_node_key: "初始位置",
    command_reachability: "指挥可达性",
    initial_value: "初始值",
    start_node_key: "起始节点",
    primary_actor_key: "主要参与者",
    resource_key: "资源定义",
    quantity: "初始数量",
    reserved_value: "预留数量",
    availability: "可用状态",
    visibility: "可见性",
    survey_discoverable: "调查发现设置",
    resource_inventory_visibility: "资源库存可见性",
    resource_survey_completed: "资源调查状态",
    value: "要求值",
  };
  if (locator.object_kind === "node" && path[0] === "facts" && path.length > 2) {
    const nodes = atPath(document, ["world", "nodes"]);
    const node = Array.isArray(nodes) ? nodes.map(asObject).find((candidate) => candidate?.key === locator.object_key) ?? null : null;
    const fact = Array.isArray(node?.facts) ? node.facts.map(asObject).find((candidate) => candidate?.key === path[1]) : null;
    const factName = typeof fact?.name === "string" && fact.name.trim() ? fact.name : path[1];
    return `${factName} · ${labels[last] ?? fieldLabel(last)}`;
  }
  return labels[last] ?? (last ? fieldLabel(last) : "对象配置");
}

function authoringReason(issue: StructuredIssue, field: string, dependencyLabel?: string): string {
  if (dependencyLabel) return `此字段引用的${dependencyLabel}不存在或尚未完成配置。`;
  const type = typeof issue.type === "string" ? issue.type : "";
  if (type.includes("missing")) return `还需要填写${field}。`;
  if (type.includes("enum") || type.includes("literal")) return `请为${field}选择有效的现有项。`;
  if (type.includes("string_pattern") || type.includes("too_short") || type.includes("too_long")) return `请按字段要求填写${field}。`;
  return `${field}需要调整后才能生成此预览。`;
}

function structuredIssues(error: unknown): StructuredIssue[] {
  if (Array.isArray(error)) return error.filter((item): item is StructuredIssue => Boolean(asObject(item)));
  if (!(error instanceof ApiError) || error.code !== "SCENARIO_INITIALIZATION_PREVIEW_INVALID") return [];
  const details = asObject(error.details);
  return Array.isArray(details?.issues) ? details!.issues.filter((item): item is StructuredIssue => Boolean(asObject(item))) : [];
}

function targetForIssue(issue: StructuredIssue, document: JsonObject): IssueTarget | null {
  return targetFromStructuredMetadata(issue) ?? targetFromLocation(issue, document) ?? targetFromReferenceMessage(issue);
}

function initializationCollectionRows(document: JsonObject, name: string): JsonObject[] {
  const initialization = asObject(document.initialization);
  const rows = initialization?.[name];
  return Array.isArray(rows) ? rows.map(asObject).filter((row): row is JsonObject => Boolean(row)) : [];
}

function issueTargetsResource(target: IssueTarget, resourceKey: string, document: JsonObject): boolean {
  const { object_kind: kind, object_key: key } = target.locator;
  if (!key) return false;
  if (kind === "resource_pool") {
    return initializationCollectionRows(document, "resource_pools").some((row) => row.pool_key === key && row.resource_key === resourceKey);
  }
  if (kind === "legacy_resource") return key.split("::", 1)[0] === resourceKey;
  return false;
}

export function issueBelongsToFocusedObject(issue: StructuredIssue, focus: { kind: string; key: string; factKey?: string | null }, document: JsonObject): boolean {
  const target = targetForIssue(issue, document);
  if (!target) return false;
  const { object_kind: kind, object_key: key, field_path: fieldPath } = target.locator;
  if (focus.factKey) {
    const path = fieldPath?.split(".") ?? [];
    return focus.kind === "node" && kind === "node" && key === focus.key && path[0] === "facts" && path[1] === focus.factKey;
  }
  if (kind === focus.kind && key === focus.key) return true;
  return focus.kind === "resource" && issueTargetsResource(target, focus.key, document);
}

export function hasStructuredInitializationPreviewIssues(error: unknown): boolean {
  return structuredIssues(error).length > 0;
}

function presentIssues(issues: StructuredIssue[], entity: { kind: string; key: string } | null, document: JsonObject, scenarioId: string): PreviewIssuePresentation[] {
  return issues.map((issue) => {
    const target = targetForIssue(issue, document);
    if (!target) {
      const locator: EditorLocator = { owner: "workflow", section: "validation", issuePath: null };
      return {
        category: "unresolved",
        title: "场景还有一项跨对象配置需要检查",
        groupKey: "configuration-check",
        groupTitle: "场景配置",
        problemLabel: "跨对象配置",
        reason: "该问题无法安全归属到当前对象，预览没有把它误报为当前对象缺失。请在配置检查中查看可定位的问题。",
        href: editorLocatorHref(locator, scenarioId),
        actionLabel: "前往配置检查",
        locator: null,
        rawPath: Array.isArray(issue.loc) ? issue.loc.map(String).join(".") : "",
        rawMessage: typeof issue.msg === "string" ? issue.msg : "",
      };
    }
    const isCurrent = Boolean(entity && target.locator.object_kind === entity.kind && target.locator.object_key === entity.key);
    const category: PreviewIssueCategory = target.targetOwner ? "dependency" : isCurrent ? "current" : "other";
    const field = problemLabel(target.locator, document);
    const kindName = target.locator.object_kind === "initialization" ? "开局配置" : initializationKindLabels[target.locator.object_kind] ?? kindLabels[target.locator.object_kind] ?? target.locator.object_kind;
    const groupKindName = target.locator.object_kind === "node" ? "世界实体" : kindName;
    const currentTarget = editorLocatorFromValidation(target.locator, document);
    const href = currentTarget
      ? editorLocatorHref(currentTarget, scenarioId)
      : editorLocatorHref({ owner: "workflow", section: "validation", issuePath: null }, scenarioId);
    const ownerHref = target.targetOwner
      ? editorLocatorHref({ owner: "singleton", section: sectionForKind(target.targetOwner), fieldPath: null }, scenarioId)
      : undefined;
    const ownerName = target.locator.object_key
      ? (documentObjectName(document, target.locator.object_kind, target.locator.object_key) ?? target.locator.object_key)
      : kindName;
    const title = category === "current"
      ? `当前${kindName}需要完善${field}`
      : category === "dependency"
        ? `${kindName}「${ownerName}」引用的${target.dependencyLabel}需要完善`
        : `${kindName}「${ownerName}」需要完善${field}`;
    return {
      category,
      title,
      groupKey: target.locator.object_key ? `${target.locator.object_kind}:${target.locator.object_key}` : `${target.locator.object_kind}:${target.locator.field_path ?? "root"}`,
      groupTitle: target.locator.object_key ? `${groupKindName} · ${ownerName}` : groupKindName,
      problemLabel: field,
      reason: authoringReason(issue, field, target.dependencyLabel),
      href,
      actionLabel: isCurrent ? `定位到${field}` : category === "dependency" && entity ? `定位到${field}` : `前往${kindName}「${ownerName}」`,
      ownerHref,
      ownerActionLabel: target.targetOwner ? `前往${kindLabels[target.targetOwner] ?? target.targetOwner}` : undefined,
      locator: target.locator,
      referenceLocator: target.targetOwner
        ? { object_kind: target.targetOwner, object_key: null, field_path: null }
        : null,
      referenceOwner: target.targetOwner ?? null,
      rawPath: Array.isArray(issue.loc) ? issue.loc.map(String).join(".") : "",
      rawMessage: typeof issue.msg === "string" ? issue.msg : "",
    };
  });
}

export function initializationPreviewIssuePresentations(error: unknown, entity: { kind: string; key: string } | null, document: JsonObject, scenarioId: string): PreviewIssuePresentation[] {
  return presentIssues(structuredIssues(error), entity, document, scenarioId);
}

export function initializationPreviewIssuePresentationsForFocus(error: unknown, focus: { kind: string; key: string; factKey?: string | null }, document: JsonObject, scenarioId: string): PreviewIssuePresentation[] {
  const focusedIssues = structuredIssues(error).filter((issue) => issueBelongsToFocusedObject(issue, focus, document));
  return presentIssues(focusedIssues, focus, document, scenarioId);
}

function documentObjectName(document: JsonObject, kind: string, key: string): string | null {
  const collectionPath = (kind === "legacy_resource" ? entityCollections.find((item) => item.kind === "resource") : entityCollections.find((item) => item.kind === kind))?.path;
  if (!collectionPath) return null;
  const values = atPath(document, collectionPath);
  if (!Array.isArray(values)) return null;
  const normalizedKey = kind === "legacy_resource" ? key.split("::", 1)[0] : key;
  const object = values.map(asObject).find((item) => item?.key === normalizedKey);
  if (typeof object?.name === "string" && object.name.trim()) return object.name;
  if (typeof object?.term === "string" && object.term.trim()) return object.term;
  return null;
}
