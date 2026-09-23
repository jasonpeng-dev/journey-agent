import type { CompletenessItem, CompletenessResult, Locator } from "../../types";

const entityKinds = new Set([
  "node_type", "node", "relation_type", "relation", "resource", "role", "actor",
  "interaction", "action", "rule", "derived_state", "public_reference",
]);

export function completenessLocatorHref(locator: Locator | null | undefined, scenarioId: string): string | null {
  if (!locator) return null;
  if (locator.object_kind === "metadata") return `/scenarios/${scenarioId}/edit/overview${locator.field_path ? `?focus_path=${encodeURIComponent(locator.field_path)}` : ""}`;
  if (locator.object_kind === "initialization") {
    const [collection, identity] = (locator.field_path ?? "").split(".");
    if (collection && identity) return `/scenarios/${scenarioId}/edit/initialization?domain=resources&group=${encodeURIComponent(collection.replaceAll("_", "-"))}&item=${encodeURIComponent(`pool:${identity}`)}`;
    return `/scenarios/${scenarioId}/edit/initialization`;
  }
  if (locator.object_kind === "planning") {
    const [collection, identity] = (locator.field_path ?? "").split(".");
    if (collection === "recovery_hints" && identity) return `/scenarios/${scenarioId}/edit/planning-recovery?owner=collection&collection=recovery_hints&item=${encodeURIComponent(JSON.stringify([identity]))}`;
    return `/scenarios/${scenarioId}/edit/planning-instructions`;
  }
  if (locator.object_kind === "public_knowledge") {
    if (locator.object_key && locator.field_path?.startsWith("resource_source_hints")) return `/scenarios/${scenarioId}/edit/public-knowledge?owner=collection&collection=resource_source_hints&item=${encodeURIComponent(JSON.stringify([locator.object_key]))}`;
    return `/scenarios/${scenarioId}/edit/public-knowledge`;
  }
  if (entityKinds.has(locator.object_kind) && locator.object_key) {
    const section = locator.object_kind === "relation_type" ? "relation-types" : locator.object_kind === "public_reference" ? "public-references" : locator.object_kind === "derived_state" ? "derived-states" : locator.object_kind === "node_type" ? "node-types" : locator.object_kind === "resource" ? "resources" : locator.object_kind === "role" ? "roles" : locator.object_kind === "actor" ? "actors" : locator.object_kind === "interaction" ? "interactions" : locator.object_kind === "action" ? "actions" : locator.object_kind === "rule" ? "rules" : locator.object_kind === "relation" ? "relations" : "world-entities";
    return `/scenarios/${scenarioId}/edit/${section}/${encodeURIComponent(locator.object_key)}${locator.field_path ? `?focus_path=${encodeURIComponent(locator.field_path)}` : ""}`;
  }
  if (locator.object_kind.startsWith("action_") && locator.object_key) {
    const parentKey = locator.object_key.split(":", 1)[0];
    return `/scenarios/${scenarioId}/edit/actions/${encodeURIComponent(parentKey)}${locator.field_path ? `?focus_path=${encodeURIComponent(locator.field_path)}` : ""}`;
  }
  return null;
}

export function findingsForObject(result: CompletenessResult, kind: string, key: string): CompletenessItem[] {
  return result.items.filter((item) => {
    if (item.level === "COMPLETE") return false;
    const locator = item.locator;
    if (!locator?.object_key) return false;
    if (locator.object_kind === kind && locator.object_key === key) return true;
    return kind === "action" && locator.object_kind.startsWith("action_") && locator.object_key.split(":", 1)[0] === key;
  });
}
