import type { EntityKind, JsonObject } from "./editor";
import { addObject as addRegisteredObject, collectionDefaults, sectionRegistry } from "./editor";

export const kindsBySection: Record<string, EntityKind[]> = Object.fromEntries(
  sectionRegistry.map((section) => [section.id, section.entityKinds ?? []]),
);
// The legacy world route is a browser-only topology surface. Authoring belongs
// to the dedicated World Model sections below it.
kindsBySection.world = [];
kindsBySection.interactions = ["interaction"];

export function addObject(document: JsonObject, kind: EntityKind, identity: JsonObject): { document: JsonObject; key: string } {
  return addRegisteredObject(document, kind, identity);
}

export function defaultArrayItem(field: string): unknown {
  if (field === "conditions") return { kind: "FACT_EQUALS", node: { kind: "EXPLICIT", node_key: "" }, fact_key: "", value: true };
  if (field === "effects") return { kind: "EMIT_OUTCOME", outcome_code: "" };
  // Identity-bearing arrays must go through their owner creation dialog.
  if (["facts", "relations", "parameters", "expected_outcomes", "operation_bindings", "target_actor_roles", "dependencies", "value_labels", "doctrine", "autonomous_limits", "approval_required_values", "resource_pools", "region_resource_knowledge", "resource_initial_states", "resource_source_hints", "public_references", "recovery_hints"].includes(field)) return null;
  return "";
}

export { collectionDefaults };
