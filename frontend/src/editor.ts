export type JsonObject = Record<string, unknown>;

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
  "planning",
  "public-knowledge",
  "public-references",
  "validation",
] as const;

export const legacySections = ["world", "actors", "interactions"] as const;

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
  { id: "planning", labelKey: "planning", rootPath: ["planning"] },
  { id: "public-knowledge", labelKey: "public_knowledge", rootPath: ["public_knowledge"] },
  { id: "public-references", labelKey: "public_references", entityKinds: ["public_reference"] },
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
    const collection = readPath(copy, target.path.slice(0, -1));
    if (Array.isArray(collection)) collection[Number(target.path.at(-1))] = structuredClone(value);
  }
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
    node_type: { key: "new_node_type", name: "New node type", description: "" },
    node: { key: "new_node", name: "New node", description: "", node_type_key: "", initial_access: "AVAILABLE", initial_visibility: "KNOWN", interaction_keys: [], facts: [] },
    relation_type: { key: "new_relation_type", name: "New relation type", description: "" },
     relation: { key: "new_relation", source_node_key: "", relation_type_key: "", target_node_key: "", initial_visibility: "VISIBLE" },
    resource: { key: "new_resource", name: "New resource", description: "", initial_value: 0, minimum: 0, maximum: null, reservation_supported: false, unit: null, display_unit: null },
    role: { key: "new_role", name: "New role", description: "", capabilities: ["EXECUTE_ACTION"] },
    actor: { key: "new_actor", name: "New actor", role_key: "", persona: "", doctrine: [], initial_node_key: "", allowed_action_keys: [], authority_policy: { autonomous_limits: [], approval_required_values: [] } },
    interaction: { key: "new_interaction", name: "New interaction", description: "" },
    action: { key: "new_action", name: "New action", description: "", required_interaction_key: "", execution_mode: "IMMEDIATE", parameters: [], allowed_actor_capabilities: ["EXECUTE_ACTION"], expected_outcomes: [{ code: "Success", name: "Success", success: true }], planning: { terminal_effects: [], supporting_effects: [], success_outcome_codes: ["Success"], wait_success_outcome_codes: [], hints: [] } },
    rule: { key: "new_rule", phase: "RESOLVE", trigger: "ACTION", action_key: "", priority: 0, condition: null, effects: [{ kind: "EMIT_OUTCOME", outcome_code: "Success", retryable: false }] },
    derived_state: { key: "new_derived_state", name: "New derived state", description: "", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] },
    public_reference: { term: "New reference", ref_type: "NODE", ref_key: "" },
  };
  return structuredClone(defaults[kind]);
}

export function addObject(document: JsonObject, kind: EntityKind): { document: JsonObject; key: string } {
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
  const base = kind === "relation" ? "new_relation" : kind === "public_reference" ? "new_reference" : String(collectionDefaults(kind).key);
  let key = base;
  let index = 2;
  while (collection.some((item) => item && objectIdentity(kind, item) === key)) key = `${base}_${index++}`;
  const value = collectionDefaults(kind);
  if (kind === "public_reference") {
    const nodes = objectsAt(copy, "node");
    const resources = objectsAt(copy, "resource");
    const target = nodes[0] ?? resources[0];
    value.ref_type = target?.kind === "resource" ? "RESOURCE" : "NODE";
    value.ref_key = target?.key ?? "new_reference_target";
    let term = "New reference";
    let termIndex = 2;
    while (collection.some((item) => item && objectIdentity(kind, { ...value, term }) === objectIdentity(kind, item))) term = `New reference ${termIndex++}`;
    value.term = term;
  } else value[KEY_FIELDS[kind]] = key;
  collection.push(value);
  return { document: copy, key: objectIdentity(kind, value) ?? key };
}

export function getByPath(value: unknown, path: string[]): unknown {
  return readPath(value, path);
}

export function setByPath(document: JsonObject, path: string[], value: unknown): JsonObject {
  return updateRoot(document, path, value);
}
