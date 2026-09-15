import type { EntityKind, JsonObject } from "../../editor";
import { entityRegistry, type ReferenceDomain } from "../../editor-registry";
import { sectionObjects } from "../../editor";

export type ReferenceOption = { key: string; name: string };

function objectName(value: JsonObject, fallback: string): string {
  return typeof value.name === "string" && value.name.trim() ? value.name : fallback;
}

function arrayOf(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function entityOptions(document: JsonObject, kind: EntityKind): ReferenceOption[] {
  return sectionObjects(document, entityRegistry[kind].section)
    .filter((item) => item.kind === kind)
    .map((item) => ({ key: item.key, name: item.name }));
}

/**
 * Stable-key options for every authored reference domain.
 * This is deliberately a pure document projection so the editor can use it
 * for both a saved Draft and an unsaved Working Copy.
 */
export function referenceOptions(document: JsonObject, domain: ReferenceDomain): ReferenceOption[] {
  if (domain === "fact") {
    return sectionObjects(document, "world")
      .filter((item) => item.kind === "node")
      .flatMap((node) => arrayOf(node.value.facts).flatMap((fact) => {
        if (typeof fact.key !== "string") return [];
        const key = `${node.key}.${fact.key}`;
        return [{ key, name: `${objectName(node.value, node.key)} · ${objectName(fact, fact.key)}` }];
      }));
  }

  if (domain === "resource_pool") {
    const initialization = document.initialization;
    const pools = initialization && typeof initialization === "object" && !Array.isArray(initialization)
      ? (initialization as JsonObject).resource_pools
      : [];
    return arrayOf(pools).flatMap((pool) => typeof pool.pool_key === "string"
      ? [{ key: pool.pool_key, name: objectName(pool, pool.pool_key) }]
      : []);
  }

  const kind = domain as EntityKind;
  return entityRegistry[kind] ? entityOptions(document, kind) : [];
}

