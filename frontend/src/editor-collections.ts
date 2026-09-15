import type { EditorSection, JsonObject } from "./editor";

export type RootCollectionKey =
  | "resource_source_hints"
  | "recovery_hints"
  | "resource_initial_states"
  | "resource_pools"
  | "region_resource_knowledge";

export type RootCollectionSelection = {
  collection: RootCollectionKey;
  index: number;
};

export type RootCollectionDefinition = {
  key: RootCollectionKey;
  label: string;
  singularLabel: string;
};

export type RootCollectionItem = RootCollectionSelection & {
  value: JsonObject;
  title: string;
  summary: string;
};

const definitions: Partial<Record<EditorSection, RootCollectionDefinition[]>> = {
  "public-knowledge": [
    { key: "resource_source_hints", label: "资源来源提示", singularLabel: "资源来源提示" },
  ],
  planning: [
    { key: "recovery_hints", label: "恢复提示", singularLabel: "恢复提示" },
  ],
  initialization: [
    { key: "resource_initial_states", label: "资源初始状态", singularLabel: "资源初始状态" },
    { key: "resource_pools", label: "资源池", singularLabel: "资源池" },
    { key: "region_resource_knowledge", label: "区域资源知识", singularLabel: "区域资源知识" },
  ],
};

function objectValue(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function compact(value: unknown, fallback: string): string {
  if (typeof value !== "string" || value.trim().length === 0) return fallback;
  return value.trim();
}

function itemTitle(collection: RootCollectionKey, value: JsonObject, index: number): string {
  if (collection === "resource_source_hints") return compact(value.resource_key, `未命名来源提示 ${index + 1}`);
  if (collection === "recovery_hints") return compact(value.failure_code, `未命名恢复提示 ${index + 1}`);
  if (collection === "resource_initial_states") return compact(value.resource_key, `未命名资源初始状态 ${index + 1}`);
  if (collection === "resource_pools") return compact(value.pool_key, `未命名资源池 ${index + 1}`);
  return compact(value.region_key, `未命名区域资源知识 ${index + 1}`);
}

function itemSummary(collection: RootCollectionKey, value: JsonObject): string {
  if (collection === "resource_source_hints") {
    return compact(value.primary_region_key, Array.isArray(value.candidate_region_keys) ? `${value.candidate_region_keys.length} 个候选区域` : "待配置区域");
  }
  if (collection === "recovery_hints") return compact(value.hint, "尚未填写提示");
  if (collection === "resource_initial_states") return compact(value.scope_node_key, `初始值 ${String(value.value ?? 0)}`);
  if (collection === "resource_pools") return compact(value.region_key ?? value.facility_key, compact(value.resource_key, "待配置资源"));
  return compact(value.region_key, "待配置区域");
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
        collection: definition.key,
        index,
        value: object,
        title: itemTitle(definition.key, object, index),
        summary: itemSummary(definition.key, object),
      }] : [];
    });
  });
}

export function rootCollectionDefault(collection: RootCollectionKey): JsonObject {
  if (collection === "resource_source_hints") return { resource_key: "", primary_region_key: null, candidate_region_keys: [] };
  if (collection === "recovery_hints") return { failure_code: "FAILURE", hint: "" };
  if (collection === "resource_initial_states") return { resource_key: "", scope_node_key: null, value: 0, reserved_value: 0 };
  if (collection === "resource_pools") return { pool_key: "new_pool", resource_key: "", region_key: null, facility_key: null, quantity: 0, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false };
  return { region_key: "", resource_inventory_visibility: "VISIBLE", resource_survey_completed: false };
}

export function rootCollectionLabel(collection: RootCollectionKey): string {
  return rootCollectionDefinitions("initialization").concat(
    rootCollectionDefinitions("planning"),
    rootCollectionDefinitions("public-knowledge"),
  ).find((definition) => definition.key === collection)?.singularLabel ?? collection;
}
