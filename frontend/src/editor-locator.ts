import { objectByKindAndKey, sectionForKind, sectionRoot, type EditorSection, type EntityKind, type JsonObject } from "./editor";
import { rootCollectionSelectionForPath, type RootCollectionKey } from "./editor-collections";
import type { Locator } from "./types";

export type EditorLocator =
  | { owner: "singleton"; section: EditorSection; fieldPath: string | null }
  | { owner: "entity"; section: EditorSection; kind: EntityKind; objectKey: string; fieldPath: string | null }
  | { owner: "root-collection"; section: EditorSection; collection: RootCollectionKey; identity: string; fieldPath: string | null }
  | { owner: "initialization-item"; section: "initialization"; domain: string; group: string; item: string; fieldPath: string | null }
  | { owner: "planning-instruction"; section: "planning-instructions"; index: number; fieldPath: string }
  | { owner: "quick-input"; section: "goal-resolution"; index: number; fieldPath: string }
  | { owner: "browser"; section: "world"; contextKind: "scope" | "node" | "relation"; contextKey: string }
  | { owner: "workflow"; section: "validation"; issuePath: string | null };

const rootSections: Record<string, EditorSection> = {
  metadata: "overview",
  initialization: "initialization",
  goal_resolution: "goal-resolution",
  planning: "planning-instructions",
  engine_contract: "overview",
};

const entityKinds = new Set<EntityKind>([
  "node_type", "node", "relation_type", "relation", "resource", "role", "actor",
  "interaction", "action", "rule", "derived_state", "public_reference",
]);

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function rowsAt(document: JsonObject, root: string, collection: string): Record<string, unknown>[] {
  const values = record(document[root])?.[collection];
  return Array.isArray(values) ? values.map(record).filter((value): value is Record<string, unknown> => value !== null) : [];
}

function stableValue(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : "";
}

function initializationItem(
  domain: string,
  group: string,
  item: string,
  fieldPath: string | null,
): EditorLocator {
  return { owner: "initialization-item", section: "initialization", domain, group, item, fieldPath };
}

function initializationFieldLocator(locator: Locator, document: JsonObject): EditorLocator | null {
  const kind = locator.object_kind;
  const key = locator.object_key ?? "";
  const path = locator.field_path?.replace(/^\.+|\.+$/g, "") ?? "";

  if (kind === "initialization") {
    if (path.startsWith("resource_pools.")) {
      const [, requestedKey, ...tail] = path.split(".");
      const poolKey = requestedKey && /^\d+$/.test(requestedKey)
        ? stableValue(rowsAt(document, "initialization", "resource_pools")[Number(requestedKey)]?.pool_key)
        : requestedKey;
      return poolKey ? initializationFieldLocator({ object_kind: "resource_pool", object_key: poolKey, field_path: tail.join(".") }, document) : null;
    }
    if (path.startsWith("region_resource_knowledge.")) {
      const [, requestedKey, ...tail] = path.split(".");
      const regionKey = requestedKey && /^\d+$/.test(requestedKey)
        ? stableValue(rowsAt(document, "initialization", "region_resource_knowledge")[Number(requestedKey)]?.region_key)
        : requestedKey;
      return regionKey ? initializationFieldLocator({ object_kind: "region_resource_knowledge", object_key: regionKey, field_path: tail.join(".") }, document) : null;
    }
    if (path.startsWith("resource_initial_states.")) {
      const [, requestedKey, ...tail] = path.split(".");
      const state = requestedKey && /^\d+$/.test(requestedKey)
        ? rowsAt(document, "initialization", "resource_initial_states")[Number(requestedKey)]
        : null;
      const resourceKey = state
        ? `${stableValue(state.resource_key)}::${stableValue(state.scope_node_key)}`
        : requestedKey;
      return resourceKey ? initializationFieldLocator({ object_kind: "legacy_resource", object_key: resourceKey, field_path: tail.join(".") }, document) : null;
    }
    if (path === "start_node_key" || path === "primary_actor_key" || path.startsWith("initialization.")) {
      const field = path.startsWith("initialization.") ? path : `initialization.${path}`;
      return initializationItem("basic", "entry", "bootstrap-entry", field);
    }
  }

  if (kind === "node" && (path === "initial_access" || path === "initial_visibility" || /^facts\.[^.]+\.(initial_value|initial_visibility)$/.test(path))) {
    const node = rowsAt(document, "world", "nodes").find((item) => item.key === key);
    if (!node) return null;
    const typeKey = stableValue(node.node_type_key) || "unassigned";
    const fieldPath = path.startsWith("facts.") ? `world.nodes.${key}.${path}` : `world.nodes.${key}.${path}`;
    return initializationItem("nodes", `node-type:${typeKey}`, `node:${key}`, fieldPath);
  }

  if (kind === "actor" && ["initial_node_key", "command_reachability"].includes(path)) {
    const actor = rowsAt(document, "actors", "actor_profiles").find((item) => item.key === key);
    if (!actor) return null;
    const roleKey = stableValue(actor.role_key) || "unassigned";
    return initializationItem("actors", `role:${roleKey}`, `actor:${key}`, `actors.actor_profiles.${key}.${path}`);
  }

  if (kind === "relation" && path === "initial_visibility") {
    const relation = rowsAt(document, "world", "relations").find((item) => item.key === key) ?? rowsAt(document, "world", "relations").find((item) => [item.source_node_key, item.relation_type_key, item.target_node_key].map(String).join("__") === key);
    if (!relation) return null;
    const relationKey = stableValue(relation.key) || [relation.source_node_key, relation.relation_type_key, relation.target_node_key].map(String).join("__");
    const typeKey = stableValue(relation.relation_type_key) || "unassigned";
    return initializationItem("relations", `relation-type:${typeKey}`, `relation:${relationKey}`, `world.relations.${relationKey}.initial_visibility`);
  }

  if (kind === "resource_pool") {
    const pool = rowsAt(document, "initialization", "resource_pools").find((item) => item.pool_key === key);
    if (!pool) return null;
    const poolKey = stableValue(pool.pool_key);
    const resourceKey = stableValue(pool.resource_key);
    const regionKey = stableValue(pool.region_key) || "global";
    const relativePath = path.startsWith("initialization.resource_pools.") ? path.split(".").slice(3).join(".") : path;
    const focusPath = relativePath.startsWith("availability_requirement.") ? "availability_requirement" : relativePath;
    return initializationItem("resources", "resource-pools", resourceKey ? `pool:${poolKey}:${resourceKey}:${regionKey}` : `pool:${poolKey}`, focusPath ? `initialization.resource_pools.${poolKey}.${focusPath}` : null);
  }

  if (kind === "region_resource_knowledge") {
    const regionKey = stableValue(key);
    if (!regionKey) return null;
    const relativePath = path.startsWith("initialization.region_resource_knowledge.") ? path.split(".").slice(3).join(".") : path;
    return initializationItem("resources", "region-resource-knowledge", `region-knowledge:${regionKey}`, relativePath ? `initialization.region_resource_knowledge.${regionKey}.${relativePath}` : null);
  }

  if (kind === "legacy_resource" || (kind === "resource" && path === "initial_value")) {
    const [resourceKey] = key.split("::", 2);
    const normalizedResourceKey = stableValue(resourceKey);
    if (!normalizedResourceKey) return null;
    return { owner: "entity", section: "resources", kind: "resource", objectKey: normalizedResourceKey, fieldPath: "initial_value" };
  }

  return null;
}

export function editorLocatorFromValidation(locator: Locator, document: JsonObject): EditorLocator | null {
  const initializationLocator = initializationFieldLocator(locator, document);
  if (initializationLocator) return initializationLocator;
  if (locator.object_kind === "planning" && locator.field_path) {
    const instructionMatch = locator.field_path.match(/^instructions\.(\d+)(?:\.(.*))?$/);
    if (instructionMatch) {
      const index = Number(instructionMatch[1]);
      const instructions = record(document.planning)?.instructions;
      if (Number.isSafeInteger(index) && Array.isArray(instructions) && typeof instructions[index] === "string") {
        return { owner: "planning-instruction", section: "planning-instructions", index, fieldPath: `instructions.${index}${instructionMatch[2] ? `.${instructionMatch[2]}` : ""}` };
      }
    }
  }
  if (locator.object_kind === "goal_resolution" && locator.field_path) {
    const quickInputMatch = locator.field_path.match(/^quick_inputs\.(\d+)(?:\.(.*))?$/);
    if (quickInputMatch) {
      const index = Number(quickInputMatch[1]);
      const quickInputs = record(document.goal_resolution)?.quick_inputs;
      if (Number.isSafeInteger(index) && Array.isArray(quickInputs) && typeof quickInputs[index] === "string") {
        return { owner: "quick-input", section: "goal-resolution", index, fieldPath: `quick_inputs.${index}${quickInputMatch[2] ? `.${quickInputMatch[2]}` : ""}` };
      }
    }
  }
  if (locator.object_kind === "public_knowledge") {
    const resourceKey = locator.object_key;
    return resourceKey && objectByKindAndKey(document, "resource", resourceKey)
      ? { owner: "entity", section: "resources", kind: "resource", objectKey: resourceKey, fieldPath: "source_hint" }
      : { owner: "singleton", section: "resources", fieldPath: "source_hint" };
  }
  const rootSection = rootSections[locator.object_kind];
  if (rootSection) {
    const root = sectionRoot(document, rootSection);
    const selection = root && typeof root === "object" && !Array.isArray(root)
      ? rootCollectionSelectionForPath(root as JsonObject, locator.field_path)
      : null;
    if (selection) {
      return {
        owner: "root-collection",
        section: rootSection,
        collection: selection.collection,
        identity: selection.identity,
        fieldPath: locator.field_path,
      };
    }
    return { owner: "singleton", section: rootSection, fieldPath: locator.field_path };
  }
  if (entityKinds.has(locator.object_kind as EntityKind) && locator.object_key) {
    const kind = locator.object_kind as EntityKind;
    return { owner: "entity", section: sectionForKind(kind), kind, objectKey: locator.object_key, fieldPath: locator.field_path };
  }
  return null;
}

export function editorLocatorHref(locator: EditorLocator, scenarioId: string): string {
  if (locator.owner === "browser") {
    const query = new URLSearchParams({ context: locator.contextKind, key: locator.contextKey });
    return `/scenarios/${scenarioId}/edit/world?${query}`;
  }
  if (locator.owner === "workflow") {
    const query = new URLSearchParams();
    if (locator.issuePath) query.set("focus_path", locator.issuePath);
    return `/scenarios/${scenarioId}/edit/validation${query.size ? `?${query}` : ""}`;
  }
  const query = new URLSearchParams();
  if (locator.owner === "planning-instruction") {
    query.set("owner", "instruction");
    query.set("item", String(locator.index));
    query.set("focus_path", locator.fieldPath);
    return `/scenarios/${scenarioId}/edit/${locator.section}?${query}`;
  }
  if (locator.owner === "quick-input") {
    query.set("owner", "quick-input");
    query.set("item", String(locator.index));
    query.set("focus_path", locator.fieldPath);
    return `/scenarios/${scenarioId}/edit/${locator.section}?${query}`;
  }
  if (locator.owner === "initialization-item") {
    query.set("domain", locator.domain);
    query.set("group", locator.group);
    query.set("item", locator.item);
    if (locator.fieldPath) query.set("focus_path", locator.fieldPath);
    return `/scenarios/${scenarioId}/edit/${locator.section}?${query}`;
  }
  if (locator.fieldPath) query.set("focus_path", locator.fieldPath);
  if (locator.owner === "root-collection") {
    query.set("owner", "collection");
    query.set("collection", locator.collection);
    query.set("item", locator.identity);
  }
  if (locator.owner === "entity" && (locator.kind === "public_reference" || locator.kind === "relation")) {
    query.set("kind", locator.kind);
  }
  const objectPath = locator.owner === "entity" ? `/${encodeURIComponent(locator.objectKey)}` : "";
  const section = locator.owner === "entity" && locator.kind === "relation_type" ? "relation-types" : locator.section;
  return `/scenarios/${scenarioId}/edit/${section}${objectPath}${query.size ? `?${query}` : ""}`;
}
