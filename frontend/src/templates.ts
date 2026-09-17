import type { EntityKind, JsonObject } from "./editor";
import { addObject as addRegisteredObject, collectionDefaults, sectionRegistry } from "./editor";

export const kindsBySection: Record<string, EntityKind[]> = Object.fromEntries(
  sectionRegistry.map((section) => [section.id, section.entityKinds ?? []]),
);
// The legacy world route is a browser-only topology surface. Authoring belongs
// to the dedicated World Model sections below it.
kindsBySection.world = [];
kindsBySection.interactions = ["interaction"];

export function addObject(document: JsonObject, kind: EntityKind): { document: JsonObject; key: string } {
  return addRegisteredObject(document, kind);
}

export function defaultArrayItem(field: string): unknown {
  if (field === "conditions") return { kind: "FACT_EQUALS", node: { kind: "EXPLICIT", node_key: "" }, fact_key: "", value: true };
  if (field === "effects") return { kind: "EMIT_OUTCOME", outcome_code: "Success", retryable: false };
  if (field.includes("requirements")) return { key: "requirement", node_key: "node", fact_key: "fact", accepted_values: [true], description: "" };
  if (field === "facts") return { key: "new_fact", name: "New fact", description: "", value_type: "BOOLEAN", initial_value: false, initial_visibility: "KNOWN", allowed_values: [] };
  if (field === "relations") return { key: "new_relation", source_node_key: "", relation_type_key: "", target_node_key: "", initial_visibility: "VISIBLE" };
  if (field === "parameters") return { key: "parameter", name: "Parameter", value_type: "STRING", required: true, allowed_values: [] };
  if (field === "dependencies") return { kind: "FACT", node_key: "", fact_key: "", accepted_values: [true] };
  if (field === "resource_pools") return { pool_key: "new_pool", resource_key: "", quantity: 0, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false };
  return "";
}

export { collectionDefaults };
