import type { EditorSection, JsonObject } from "./editor";
import { moveItem, type MoveDirection } from "./editor-order";

export type RootCollectionKey =
  | "recovery_hints"
  | "resource_initial_states"
  | "resource_pools"
  | "region_resource_knowledge";

export type RootCollectionSelection = {
  owner: "collection";
  collection: RootCollectionKey;
  identity: string;
};

export type RootSingletonOwnerKey = "initialization-entry" | "planning-instructions" | "planning-recovery";

export type RootSingletonSelection = {
  owner: "singleton";
  key: RootSingletonOwnerKey;
};

export type RootInstructionSelection = { owner: "instruction"; index: number };

export type RootQuickInputSelection = { owner: "quick-input"; index: number };

export type RootOwnerSelection = RootCollectionSelection | RootSingletonSelection | RootInstructionSelection | RootQuickInputSelection;

export type RootSingletonOwnerDefinition = {
  key: RootSingletonOwnerKey;
  label: string;
  summary: string;
};

export type RootCollectionDefinition = {
  key: RootCollectionKey;
  label: string;
  singularLabel: string;
};

export type RootCollectionItem = RootCollectionSelection & {
  index: number;
  value: JsonObject;
  title: string;
  summary: string;
  identityLabel: string;
};

const definitions: Partial<Record<EditorSection, RootCollectionDefinition[]>> = {
  initialization: [
    { key: "resource_initial_states", label: "资源初始状态", singularLabel: "资源初始状态" },
    { key: "resource_pools", label: "资源池", singularLabel: "资源池" },
    { key: "region_resource_knowledge", label: "区域资源知识", singularLabel: "区域资源知识" },
  ],
};

function objectValue(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function identityString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** Canonical identities mirror ScenarioDefinitionV2 uniqueness invariants. */
export function rootCollectionIdentity(collection: RootCollectionKey, value: JsonObject): string | null {
  if (collection === "recovery_hints") {
    const failureCode = identityString(value.failure_code);
    return failureCode ? JSON.stringify([failureCode]) : null;
  }
  if (collection === "resource_initial_states") {
    const resourceKey = identityString(value.resource_key);
    const scopeNodeKey = value.scope_node_key === null || value.scope_node_key === undefined
      ? null
      : identityString(value.scope_node_key);
    return resourceKey && (scopeNodeKey !== null || value.scope_node_key == null)
      ? JSON.stringify([resourceKey, scopeNodeKey])
      : null;
  }
  if (collection === "resource_pools") {
    const poolKey = identityString(value.pool_key);
    return poolKey ? JSON.stringify([poolKey]) : null;
  }
  const regionKey = identityString(value.region_key);
  return regionKey ? JSON.stringify([regionKey]) : null;
}

export function rootCollectionReferencePath(collection: RootCollectionKey, value: JsonObject): string | null {
  return collection === "resource_pools" && identityString(value.pool_key)
    ? `resource_pools.${value.pool_key}`
    : null;
}

function collectionValues(root: JsonObject, collection: RootCollectionKey): JsonObject[] {
  const values = root[collection];
  return Array.isArray(values) ? values.flatMap((value) => {
    const object = objectValue(value);
    return object ? [object] : [];
  }) : [];
}

export function rootCollectionItem(
  root: JsonObject,
  selection: RootCollectionSelection,
): { index: number; value: JsonObject } | null {
  const values = collectionValues(root, selection.collection);
  const index = values.findIndex((value) => rootCollectionIdentity(selection.collection, value) === selection.identity);
  return index >= 0 ? { index, value: values[index] } : null;
}

export function rootCollectionSelectionForPath(root: JsonObject, fieldPath: string | null): RootCollectionSelection | null {
  if (!fieldPath) return null;
  const [collectionName, indexText] = fieldPath.split(".");
  const definition = Object.values(definitions).flatMap((items) => items ?? []).find((item) => item.key === collectionName);
  if (!definition || !/^\d+$/.test(indexText ?? "")) return null;
  const value = collectionValues(root, definition.key)[Number(indexText)];
  const identity = value ? rootCollectionIdentity(definition.key, value) : null;
  return identity ? { owner: "collection", collection: definition.key, identity } : null;
}

export type RootCollectionMutation =
  | { ok: true; root: JsonObject; selection: RootCollectionSelection | null }
  | { ok: false; reason: string };

export function replaceRootCollectionItem(
  root: JsonObject,
  selection: RootCollectionSelection,
  item: JsonObject,
): RootCollectionMutation {
  const selected = rootCollectionItem(root, selection);
  if (!selected) return { ok: false, reason: "当前集合项已不存在。" };
  const identity = rootCollectionIdentity(selection.collection, item);
  if (identity && identity !== selection.identity) return { ok: false, reason: "Collection identity is immutable after creation; delete and recreate the item." };
  if (!identity) return { ok: false, reason: "请先填写完整的集合项身份字段。" };
  const values = collectionValues(root, selection.collection);
  if (values.some((value, index) => index !== selected.index && rootCollectionIdentity(selection.collection, value) === identity)) {
    return { ok: false, reason: "集合中已存在相同身份的项目。" };
  }
  values[selected.index] = structuredClone(item);
  return {
    ok: true,
    root: { ...structuredClone(root), [selection.collection]: values },
    selection: { owner: "collection", collection: selection.collection, identity },
  };
}

export function removeRootCollectionItem(root: JsonObject, selection: RootCollectionSelection): RootCollectionMutation {
  const selected = rootCollectionItem(root, selection);
  if (!selected) return { ok: false, reason: "当前集合项已不存在。" };
  const values = collectionValues(root, selection.collection);
  values.splice(selected.index, 1);
  return { ok: true, root: { ...structuredClone(root), [selection.collection]: values }, selection: null };
}

export function moveRootCollectionItem(
  root: JsonObject,
  selection: RootCollectionSelection,
  direction: MoveDirection,
): RootCollectionMutation {
  const selected = rootCollectionItem(root, selection);
  if (!selected) return { ok: false, reason: "当前集合项已不存在。" };
  const values = collectionValues(root, selection.collection);
  return {
    ok: true,
    root: { ...structuredClone(root), [selection.collection]: moveItem(values, selected.index, direction) },
    selection,
  };
}

function compact(value: unknown, fallback: string): string {
  if (typeof value !== "string" || value.trim().length === 0) return fallback;
  return value.trim();
}

function itemTitle(collection: RootCollectionKey, value: JsonObject, index: number): string {
  if (collection === "recovery_hints") return compact(value.failure_code, `未命名失败恢复策略 ${index + 1}`);
  if (collection === "resource_initial_states") return compact(value.resource_key, `未命名资源初始状态 ${index + 1}`);
  if (collection === "resource_pools") return compact(value.pool_key, `未命名资源池 ${index + 1}`);
  return compact(value.region_key, `未命名区域资源知识 ${index + 1}`);
}

function itemSummary(collection: RootCollectionKey, value: JsonObject): string {
  if (collection === "recovery_hints") return compact(value.hint, "尚未填写提示");
  if (collection === "resource_initial_states") return compact(value.scope_node_key, `初始值 ${String(value.value ?? 0)}`);
  if (collection === "resource_pools") return compact(value.region_key ?? value.facility_key, compact(value.resource_key, "待配置资源"));
  return compact(value.region_key, "待配置区域");
}

/** A concise semantic identity for presentation; canonical JSON identities stay URL-only. */
export function rootCollectionIdentityLabel(collection: RootCollectionKey, value: JsonObject): string {
  if (collection === "recovery_hints") return `失败代码 · ${compact(value.failure_code, "待填写")}`;
  if (collection === "resource_initial_states") {
    const resource = compact(value.resource_key, "待填写");
    const scope = value.scope_node_key == null ? "全局" : compact(value.scope_node_key, "待填写");
    return `资源 · ${resource} / 作用域 · ${scope}`;
  }
  if (collection === "resource_pools") return `资源池 · ${compact(value.pool_key, "待填写")}`;
  return `区域 · ${compact(value.region_key, "待填写")}`;
}

export function rootCollectionDefinitions(section: string): RootCollectionDefinition[] {
  return definitions[section as EditorSection] ?? [];
}

export function rootCollectionItems(section: string, value: unknown): RootCollectionItem[] {
  const root = objectValue(value);
  if (!root) return [];
  return rootCollectionDefinitions(section).flatMap((definition) => {
    const values: unknown[] = Array.isArray(root[definition.key]) ? root[definition.key] as unknown[] : [];
    return values.flatMap((item, index) => {
      const object = objectValue(item);
      return object ? [{
        owner: "collection",
        collection: definition.key,
        identity: rootCollectionIdentity(definition.key, object) ?? JSON.stringify(["invalid", definition.key, object]),
        index,
        value: object,
        title: itemTitle(definition.key, object, index),
        summary: itemSummary(definition.key, object),
        identityLabel: rootCollectionIdentityLabel(definition.key, object),
      }] : [];
    });
  });
}

export function appendRootCollectionItem(root: JsonObject, collection: RootCollectionKey, item: JsonObject): RootCollectionMutation {
  const values = collectionValues(root, collection);
  const identity = rootCollectionIdentity(collection, item);
  if (!identity) return { ok: false, reason: "Complete the collection identity before creating the item." };
  if (values.some((existing) => rootCollectionIdentity(collection, existing) === identity)) {
    return { ok: false, reason: "An item with this identity already exists in the collection." };
  }
  const next = [...values, structuredClone(item)];
  return {
    ok: true,
    root: { ...structuredClone(root), [collection]: next },
    selection: { owner: "collection", collection, identity },
  };
}

export function rootSingletonOwner(section: string): RootSingletonOwnerDefinition | null {
  return rootSingletonOwners(section)[0] ?? null;
}

export function rootSingletonOwners(section: string): RootSingletonOwnerDefinition[] {
  if (section === "initialization") {
    return [{ key: "initialization-entry", label: "初始化入口", summary: "起始节点与主要参与者" }];
  }
  return [];
}

export function rootCollectionLabel(collection: RootCollectionKey): string {
  return rootCollectionDefinitions("initialization").find((definition) => definition.key === collection)?.singularLabel ?? collection;
}
