export type JsonObject = Record<string, unknown>;
export type IdentityCreationIntent = "identity-create";

export const sections = [
  "overview",
  "node-types",
  "world-entities",
  "relation-types",
  "relations",
  "resources",
  "roles",
  "actors",
  "interactions",
  "actions",
  "rules",
  "derived-states",
  "initialization",
  "goal-resolution",
  "planning-instructions",
  "planning-recovery",
  "terminology-references",
  "configuration-check",
  "validation",
] as const;

export const legacySections = ["world", "actors", "interactions", "planning", "public-knowledge", "public-references"] as const;

export type EditorSection = (typeof sections)[number] | (typeof legacySections)[number];

export type EntityKind =
  | "node_type"
  | "node"
  | "relation_type"
  | "relation"
  | "resource"
  | "role"
  | "actor"
  | "interaction"
  | "action"
  | "rule"
  | "derived_state"
  | "public_reference";

export type DraftObject = {
  kind: EntityKind;
  key: string;
  name: string;
  value: JsonObject;
  path: string[];
};

export type SectionDefinition = {
  id: EditorSection;
  labelKey: string;
  entityKinds?: EntityKind[];
  rootPath?: string[];
};

export const sectionRegistry: SectionDefinition[] = [
  { id: "overview", labelKey: "overview", rootPath: ["metadata"] },
  { id: "node-types", labelKey: "node_types", entityKinds: ["node_type"] },
  { id: "world-entities", labelKey: "world_entities", entityKinds: ["node"] },
  { id: "relation-types", labelKey: "relation_types", entityKinds: ["relation_type"] },
  { id: "relations", labelKey: "relations", entityKinds: ["relation"] },
  { id: "resources", labelKey: "resources", entityKinds: ["resource"] },
  { id: "roles", labelKey: "roles", entityKinds: ["role"] },
  { id: "actors", labelKey: "actors", entityKinds: ["actor"] },
  { id: "interactions", labelKey: "interactions", entityKinds: ["interaction"] },
  { id: "actions", labelKey: "actions", entityKinds: ["action"] },
  { id: "rules", labelKey: "rules", entityKinds: ["rule"] },
  { id: "derived-states", labelKey: "derived_states", entityKinds: ["derived_state"] },
  { id: "initialization", labelKey: "initialization", rootPath: ["initialization"] },
  { id: "goal-resolution", labelKey: "goal_resolution", rootPath: ["goal_resolution"] },
  { id: "planning-instructions", labelKey: "planning_instructions", rootPath: ["planning"] },
  { id: "planning-recovery", labelKey: "planning_recovery", rootPath: ["planning"] },
  { id: "terminology-references", labelKey: "terminology_references", entityKinds: ["public_reference"] },
  { id: "configuration-check", labelKey: "configuration_check" },
  { id: "validation", labelKey: "validation" },
];

const COLLECTION_PATHS: Record<EntityKind, string[]> = {
  node_type: ["world", "node_types"],
  node: ["world", "nodes"],
  relation_type: ["world", "relation_types"],
  relation: ["world", "relations"],
  resource: ["world", "resources"],
  role: ["actors", "roles"],
  actor: ["actors", "actor_profiles"],
  interaction: ["interactions"],
  action: ["actions"],
  rule: ["rules"],
  derived_state: ["derived_states"],
  public_reference: ["public_references"],
};

const KEY_FIELDS: Record<EntityKind, string> = {
  node_type: "key",
  node: "key",
  relation_type: "key",
  relation: "key",
  resource: "key",
  role: "key",
  actor: "key",
  interaction: "key",
  action: "key",
  rule: "key",
  derived_state: "key",
  public_reference: "ref_key",
};

function readPath(value: unknown, path: string[]): unknown {
  let current = value;
  for (const part of path) {
    if (!current || typeof current !== "object") return undefined;
    current = (current as JsonObject)[part];
  }
  return current;
}

/**
 * Resolve an authored human label without ever deriving one from a stable key.
 * The canonical V2 schema uses `name`; the other fields keep this display
 * helper safe for compatible authored documents that expose a label variant.
 */
export function authoredDisplayName(value: JsonObject, fallback: string): string {
  for (const field of ["display_name", "name", "label"] as const) {
    const candidate = value[field];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return fallback;
}

export function objectIdentity(kind: EntityKind, value: JsonObject): string | null {
  if (kind === "relation") {
    const explicit = value.key;
    if (typeof explicit === "string" && explicit.length > 0) return explicit;
    const source = typeof value.source_node_key === "string" ? value.source_node_key : "";
    const relation = typeof value.relation_type_key === "string" ? value.relation_type_key : "";
    const target = typeof value.target_node_key === "string" ? value.target_node_key : "";
    return source && relation && target ? `${source}__${relation}__${target}` : null;
  }
  if (kind === "public_reference") {
    const refType = typeof value.ref_type === "string" ? value.ref_type : "";
    const refKey = typeof value.ref_key === "string" ? value.ref_key : "";
    const term = typeof value.term === "string" ? value.term : "";
    return refType && refKey && term ? `${refType}:${refKey}:${term}` : null;
  }
  const key = value[KEY_FIELDS[kind]];
  return typeof key === "string" && key.length > 0 ? key : null;
}

export function objectIdentitySignature(kind: EntityKind, value: JsonObject): string | null {
  const identity = objectIdentity(kind, value);
  if (!identity) return null;
  if (kind === "relation") {
    return JSON.stringify([
      identity,
      value.source_node_key ?? null,
      value.relation_type_key ?? null,
      value.target_node_key ?? null,
    ]);
  }
  return identity;
}

function appendNestedRows(value: JsonObject, field: string, identityField: string, identities: unknown[]): void {
  const rows = Array.isArray(value[field]) ? value[field] : [];
  for (const row of rows) {
    if (row && typeof row === "object" && !Array.isArray(row)) {
      identities.push([field, (row as JsonObject)[identityField] ?? null]);
    }
  }
}

function nestedIdentitySignature(kind: EntityKind, value: JsonObject): string[] {
  const identities: unknown[] = [];
  const appendKeys = (field: string, identityField: string) => appendNestedRows(value, field, identityField, identities);
  const appendPolicyKeys = () => {
    const policy = value.authority_policy;
    if (!policy || typeof policy !== "object" || Array.isArray(policy)) return;
    appendNestedRows(policy as JsonObject, "autonomous_limits", "parameter_key", identities);
    appendNestedRows(policy as JsonObject, "approval_required_values", "parameter_key", identities);
  };
  if (kind === "node") {
    appendKeys("facts", "key");
    for (const fact of Array.isArray(value.facts) ? value.facts : []) {
      if (!fact || typeof fact !== "object" || Array.isArray(fact)) continue;
      const factValue = fact as JsonObject;
      for (const label of Array.isArray(factValue.value_labels) ? factValue.value_labels : []) {
        if (label && typeof label === "object" && !Array.isArray(label)) {
          identities.push(["fact_value_label", factValue.key ?? null, (label as JsonObject).value ?? null]);
        }
      }
    }
  }
  if (kind === "actor") { appendKeys("doctrine", "key"); appendPolicyKeys(); }
  if (kind === "action") {
    appendKeys("parameters", "key");
    appendKeys("expected_outcomes", "code");
    appendKeys("operation_bindings", "role");
    for (const row of Array.isArray(value.target_actor_roles) ? value.target_actor_roles : []) {
      if (row && typeof row === "object" && !Array.isArray(row)) {
        const item = row as JsonObject;
        identities.push(["target_actor_roles", item.target_key ?? null, item.required_actor_role_key ?? null]);
      }
    }
    appendPolicyKeys();
  }
  if (kind === "derived_state") {
    for (const label of Array.isArray(value.value_labels) ? value.value_labels : []) {
      if (label && typeof label === "object" && !Array.isArray(label)) {
        identities.push(["value_label", (label as JsonObject).value ?? null]);
      }
    }
    for (const row of Array.isArray(value.dependencies) ? value.dependencies : []) {
      if (!row || typeof row !== "object" || Array.isArray(row)) continue;
      const item = row as JsonObject;
      identities.push(["dependencies", item.kind ?? null, item.node_key ?? null, item.fact_key ?? null,
        item.accepted_values ?? [], item.region_key ?? null, item.resource_key ?? null, item.minimum ?? null,
        item.derived_key ?? null, item.knowledge_gate ?? null]);
    }
  }
  return identities.map((identity) => JSON.stringify(identity)).sort();
}

function ruleQualifier(value: JsonObject): string {
  if (value.phase === "PREFLIGHT") return "前置校验";
  const effects = Array.isArray(value.effects) ? value.effects : [];
  if (effects.some((effect) => effect && typeof effect === "object" && (effect as JsonObject).kind === "EMIT_FAILURE")) return "失败处理";
  if (effects.some((effect) => effect && typeof effect === "object" && (effect as JsonObject).kind === "EMIT_OUTCOME")) return "完成处理";
  return value.trigger === "STATE" ? "状态处理" : "执行规则";
}

export function ruleDisplayTitle(document: JsonObject, value: JsonObject): string {
  const actionKey = typeof value.action_key === "string" ? value.action_key : "";
  const action = actionKey ? objectsAt(document, "action").find((item) => item.key === actionKey) : null;
  const owner = action?.name ?? (value.trigger === "STATE" ? "状态规则" : "规则");
  return `${owner} · ${ruleQualifier(value)}`;
}

function objectName(document: JsonObject, kind: EntityKind, value: JsonObject, key: string): string {
  const authoredName = authoredDisplayName(value, "");
  if (authoredName) return authoredName;
  if (kind === "rule") return ruleDisplayTitle(document, value);
  if (kind === "relation") {
    const sourceKey = String(value.source_node_key ?? "");
    const targetKey = String(value.target_node_key ?? "");
    const relationTypeKey = String(value.relation_type_key ?? "");
    const source = objectsAt(document, "node").find((item) => item.key === sourceKey);
    const target = objectsAt(document, "node").find((item) => item.key === targetKey);
    const relationType = objectsAt(document, "relation_type").find((item) => item.key === relationTypeKey);
    const endpoints = `${source?.name ?? sourceKey} → ${target?.name ?? targetKey}`;
    return relationType?.name ? `${endpoints} · ${relationType.name}` : endpoints;
  }
  if (kind === "public_reference" && typeof value.term === "string") return value.term;
  return key;
}

const legacySectionDefinitions: Record<string, SectionDefinition> = {
  world: { id: "world", labelKey: "world", entityKinds: ["node_type", "node", "relation_type", "relation", "resource"] },
  actors: { id: "actors", labelKey: "actors", entityKinds: ["role", "actor"] },
  interactions: { id: "interactions", labelKey: "interactions", entityKinds: ["interaction"] },
};

function objectsAt(document: JsonObject, kind: EntityKind): DraftObject[] {
  const raw = readPath(document, COLLECTION_PATHS[kind]);
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const value = item as JsonObject;
    const key = objectIdentity(kind, value);
    if (!key) return [];
    return [{
      kind,
      key,
      name: objectName(document, kind, value, key),
      value,
      path: [...COLLECTION_PATHS[kind], String(index)],
    }];
  });
}

export function entityCollectionPath(kind: EntityKind): string[] {
  return [...COLLECTION_PATHS[kind]];
}

export function sectionDefinition(section: string): SectionDefinition | undefined {
  return sectionRegistry.find((item) => item.id === section) ?? legacySectionDefinitions[section];
}

export function sectionObjects(document: JsonObject, section: string): DraftObject[] {
  const definition = sectionDefinition(section);
  if (!definition?.entityKinds) return [];
  return definition.entityKinds.flatMap((kind) => objectsAt(document, kind));
}

export function filterDraftObjects(objects: DraftObject[], search: string, kind = "all"): DraftObject[] {
  const query = search.trim().toLocaleLowerCase();
  return objects.filter((item) => {
    if (kind !== "all" && item.kind !== kind) return false;
    if (!query) return true;
    const description = typeof item.value.description === "string" ? item.value.description : "";
    const relationKeys = item.kind === "relation"
      ? [item.value.source_node_key, item.value.relation_type_key, item.value.target_node_key]
      : [];
    return [item.name, item.key, item.kind, description, ...relationKeys]
      .filter((value): value is string => typeof value === "string")
      .some((value) => value.toLocaleLowerCase().includes(query));
  });
}

export function draftObjectIdentity(item: Pick<DraftObject, "kind" | "path">): string {
  return `${item.kind}:${item.path.join("/")}`;
}

export function objectByKindAndKey(document: JsonObject, kind: EntityKind, key: string): DraftObject | null {
  return objectsAt(document, kind).find((item) => item.key === key) ?? null;
}

export function updateObjectName(document: JsonObject, section: string, key: string, name: string, kind?: EntityKind): JsonObject {
  const copy = structuredClone(document) as JsonObject;
  const target = sectionObjects(copy, section).find((item) => item.key === key && (!kind || item.kind === kind));
  if (target) target.value.name = name;
  return copy;
}

export function replaceObject(document: JsonObject, section: string, key: string, value: JsonObject, kind?: EntityKind): JsonObject {
  const copy = structuredClone(document) as JsonObject;
  const target = sectionObjects(copy, section).find((item) => item.key === key && (!kind || item.kind === kind));
  if (target) {
    if (objectIdentitySignature(target.kind, target.value) !== objectIdentitySignature(target.kind, value)
      || JSON.stringify(nestedIdentitySignature(target.kind, target.value)) !== JSON.stringify(nestedIdentitySignature(target.kind, value))) {
      return copy;
    }
    const collection = readPath(copy, target.path.slice(0, -1));
    if (Array.isArray(collection)) collection[Number(target.path.at(-1))] = structuredClone(value);
  }
  return copy;
}

function isSingleNestedIdentityAddition(before: string[], after: string[]): boolean {
  if (after.length !== before.length + 1 || new Set(after).size !== after.length) return false;
  const remaining = [...after];
  for (const identity of before) {
    const index = remaining.indexOf(identity);
    if (index < 0) return false;
    remaining.splice(index, 1);
  }
  return remaining.length === 1 && !before.includes(remaining[0]);
}

function isCompleteNestedIdentityAddition(kind: EntityKind, before: string[], after: string[], value: JsonObject): boolean {
  const signature = after.find((identity) => !before.includes(identity));
  if (!signature) return false;
  let identity: unknown;
  try { identity = JSON.parse(signature) as unknown; } catch { return false; }
  if (!Array.isArray(identity) || typeof identity[0] !== "string") return false;
  const nonEmpty = (candidate: unknown) => typeof candidate === "string" && candidate.trim().length > 0;
  const rows = (field: string): JsonObject[] => Array.isArray(value[field]) ? (value[field] as unknown[]).filter((row): row is JsonObject => Boolean(row) && typeof row === "object" && !Array.isArray(row)) : [];
  const policyRows = (field: "autonomous_limits" | "approval_required_values"): JsonObject[] => {
    const policy = value.authority_policy;
    return policy && typeof policy === "object" && !Array.isArray(policy) && Array.isArray((policy as JsonObject)[field])
      ? ((policy as JsonObject)[field] as unknown[]).filter((row): row is JsonObject => Boolean(row) && typeof row === "object" && !Array.isArray(row))
      : [];
  };
  const discriminator = identity[0];
  if (discriminator === "facts" && kind === "node") {
    const key = identity[1];
    return nonEmpty(key) && /^[a-z][a-z0-9_]{0,79}$/.test(String(key));
  }
  if (discriminator === "parameters" && kind === "action") {
    const key = identity[1];
    return nonEmpty(key) && /^[a-z][a-z0-9_]{0,79}$/.test(String(key));
  }
  if (discriminator === "expected_outcomes" && kind === "action") {
    const code = identity[1];
    return nonEmpty(code) && /^[A-Za-z][A-Za-z0-9_]{0,99}$/.test(String(code));
  }
  if (discriminator === "operation_bindings" && kind === "action") {
    const role = identity[1];
    return nonEmpty(role) && /^[a-z][a-z0-9_]{0,79}$/.test(String(role));
  }
  if (discriminator === "target_actor_roles" && kind === "action") return nonEmpty(identity[1]) && nonEmpty(identity[2]);
  if (discriminator === "doctrine" && kind === "actor") {
    const key = identity[1];
    return nonEmpty(key) && /^[a-z][a-z0-9_]{0,79}$/.test(String(key));
  }
  if ((discriminator === "autonomous_limits" || discriminator === "approval_required_values") && (kind === "actor" || kind === "action")) {
    const row = policyRows(discriminator as "autonomous_limits" | "approval_required_values").find((candidate) => candidate.parameter_key === identity[1]);
    return nonEmpty(identity[1]) && Boolean(row);
  }
  if (discriminator === "fact_value_label" && kind === "node") {
    const fact = rows("facts").find((candidate) => candidate.key === identity[1]);
    const labels = Array.isArray(fact?.value_labels) ? fact.value_labels : [];
    return identity[2] !== null && identity[2] !== undefined && labels.some((row) => row && typeof row === "object" && !Array.isArray(row) && (row as JsonObject).value === identity[2]);
  }
  if (discriminator === "value_label" && kind === "derived_state") {
    const labels = rows("value_labels");
    return identity[1] !== null && identity[1] !== undefined && labels.some((row) => row.value === identity[1]);
  }
  if (discriminator === "dependencies" && kind === "derived_state") {
    const row = rows("dependencies").find((candidate) => JSON.stringify(["dependencies", candidate.kind ?? null, candidate.node_key ?? null, candidate.fact_key ?? null, candidate.accepted_values ?? [], candidate.region_key ?? null, candidate.resource_key ?? null, candidate.minimum ?? null, candidate.derived_key ?? null, candidate.knowledge_gate ?? null]) === signature);
    if (!row || !Array.isArray(row.accepted_values) || row.accepted_values.length === 0) return false;
    if (row.kind === "FACT") return nonEmpty(row.node_key) && nonEmpty(row.fact_key);
    if (row.kind === "RESOURCE_AT_LEAST") return nonEmpty(row.region_key) && nonEmpty(row.resource_key) && Number.isSafeInteger(row.minimum) && Number(row.minimum) >= 0;
    if (row.kind === "DERIVED_STATE") return nonEmpty(row.derived_key);
    return false;
  }
  return false;
}

/** Apply one complete nested identity submitted by its creation dialog. */
export function appendNestedIdentityFromDialog(document: JsonObject, section: string, key: string, value: JsonObject, kind: EntityKind): JsonObject | null {
  const copy = structuredClone(document) as JsonObject;
  const target = sectionObjects(copy, section).find((item) => item.key === key && item.kind === kind);
  if (!target || objectIdentitySignature(kind, target.value) !== objectIdentitySignature(kind, value)) return null;
  const before = nestedIdentitySignature(kind, target.value);
  const after = nestedIdentitySignature(kind, value);
  if (!isSingleNestedIdentityAddition(before, after) || !isCompleteNestedIdentityAddition(kind, before, after, value)) return null;
  const collection = readPath(copy, target.path.slice(0, -1));
  const index = Number(target.path.at(-1));
  if (!Array.isArray(collection) || !Number.isInteger(index) || index < 0 || index >= collection.length) return null;
  collection[index] = structuredClone(value);
  return copy;
}

export function updateRoot(document: JsonObject, path: string[], value: unknown): JsonObject {
  const copy = structuredClone(document) as JsonObject;
  let current = copy;
  path.forEach((part, index) => {
    if (index === path.length - 1) current[part] = structuredClone(value);
    else {
      if (!current[part] || typeof current[part] !== "object" || Array.isArray(current[part])) current[part] = {};
      current = current[part] as JsonObject;
    }
  });
  return copy;
}

export function updateSectionRoot(document: JsonObject, section: string, value: unknown): JsonObject {
  const definition = sectionDefinition(section);
  return definition?.rootPath ? updateRoot(document, definition.rootPath, value) : structuredClone(document) as JsonObject;
}

export function sectionRoot(document: JsonObject, section: string): unknown {
  const definition = sectionDefinition(section);
  return definition?.rootPath ? readPath(document, definition.rootPath) ?? {} : null;
}

export function sectionForKind(kind: string): EditorSection {
  const section = sectionRegistry.find((item) => item.entityKinds?.includes(kind as EntityKind));
  return section?.id ?? "overview";
}

export function nodeSemanticView(document: JsonObject, view: "all" | "regions" | "facilities" | "transports"): DraftObject[] {
  const nodes = objectsAt(document, "node");
  if (view === "all") return nodes;
  const metadata = (document.metadata ?? {}) as JsonObject;
  const locality = (metadata.locality ?? {}) as JsonObject;
  const typeKey = view === "regions" ? locality.region_node_type_key : view === "facilities" ? locality.facility_node_type_key : locality.transport_node_type_key;
  return typeKey ? nodes.filter((node) => node.value.node_type_key === typeKey) : [];
}

export function collectionDefaults(kind: EntityKind): JsonObject {
  const defaults: Record<EntityKind, JsonObject> = {
    node_type: { key: "", name: "", description: "" },
    node: { key: "", name: "", description: "", node_type_key: "", interaction_keys: [], facts: [] },
    relation_type: { key: "", name: "", description: "" },
    relation: { source_node_key: "", relation_type_key: "", target_node_key: "", initial_visibility: "VISIBLE" },
    resource: { key: "", name: "", description: "", maximum: null, reservation_supported: false, unit: null, display_unit: null },
    role: { key: "", name: "", description: "", capabilities: [] },
    actor: { key: "", name: "", role_key: "", persona: "", doctrine: [], initial_node_key: "", allowed_action_keys: [], authority_policy: { autonomous_limits: [], approval_required_values: [] }, command_reachability: "ONLINE" },
    interaction: { key: "", name: "", description: "" },
    action: { key: "", name: "", description: "", required_interaction_key: "", parameters: [], allowed_actor_capabilities: [], expected_outcomes: [], planning: { terminal_effects: [], target_terminal_effects: [], supporting_effects: [], success_outcome_codes: [], wait_success_outcome_codes: [], hints: [] }, behavior: "RULE", locality: "NONE", target_kind: "NODE" },
    rule: { key: "", trigger: "ACTION", action_key: "", condition: null, effects: [] },
    derived_state: { key: "", name: "", description: "", allowed_values: [], dependencies: [], value_labels: [], goal_addressable: false, goal_aliases: [], goal_examples: [] },
    public_reference: { term: "", ref_type: "", ref_key: "" },
  };
  return structuredClone(defaults[kind]);
}

const stableIdentityPattern = /^[a-z][a-z0-9_]{0,79}$/;

function referenceTargetExists(document: JsonObject, kind: string, key: string): boolean {
  if (kind === "region") return nodeSemanticView(document, "regions").some((item) => item.key === key);
  return sectionObjects(document, sectionForKind(kind)).some((item) => item.kind === kind && item.key === key);
}

export function addObject(document: JsonObject, kind: EntityKind, identity: JsonObject): { document: JsonObject; key: string } {
  const copy = structuredClone(document) as JsonObject;
  const path = COLLECTION_PATHS[kind];
  let current = copy;
  for (const part of path.slice(0, -1)) {
    if (!current[part] || typeof current[part] !== "object" || Array.isArray(current[part])) current[part] = {};
    current = current[part] as JsonObject;
  }
  const collectionKey = path.at(-1)!;
  if (!Array.isArray(current[collectionKey])) current[collectionKey] = [];
  const collection = current[collectionKey] as JsonObject[];
  const value = { ...collectionDefaults(kind), ...structuredClone(identity) };
  if (kind === "relation") {
    for (const [field, targetKind] of [["source_node_key", "node"], ["relation_type_key", "relation_type"], ["target_node_key", "node"]] as const) {
      const key = typeof value[field] === "string" ? value[field].trim() : "";
      if (!key || !referenceTargetExists(copy, targetKind, key)) throw new Error(`请选择有效的${field}引用。`);
    }
  } else if (kind === "public_reference") {
    const targetKinds: Record<string, string> = { NODE: "node", REGION: "region", RESOURCE: "resource", DERIVED_STATE: "derived_state", ACTION: "action", ACTOR: "actor" };
    const targetKind = typeof value.ref_type === "string" ? targetKinds[value.ref_type] : undefined;
    const targetKey = typeof value.ref_key === "string" ? value.ref_key.trim() : "";
    if (!targetKind || !targetKey || !referenceTargetExists(copy, targetKind, targetKey)) throw new Error("请选择有效的引用目标。");
    if (typeof value.term !== "string" || !value.term.trim()) throw new Error("请填写术语。");
  } else {
    const keyField = KEY_FIELDS[kind];
    const key = typeof value[keyField] === "string" ? value[keyField].trim() : "";
    if (!stableIdentityPattern.test(key)) throw new Error("稳定键必须以小写字母开头，并只使用小写字母、数字和下划线。");
    value[keyField] = key;
  }
  const key = objectIdentity(kind, value);
  if (!key) throw new Error("请先填写完整身份字段。");
  if (collection.some((item) => item && objectIdentity(kind, item) === key)) throw new Error("当前 owner 中已存在相同身份。");
  collection.push(value);
  return { document: copy, key };
}

export function getByPath(value: unknown, path: string[]): unknown {
  return readPath(value, path);
}

export function setByPath(document: JsonObject, path: string[], value: unknown): JsonObject {
  return updateRoot(document, path, value);
}
