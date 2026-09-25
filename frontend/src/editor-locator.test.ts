import { describe, expect, it } from "vitest";

import { editorLocatorFromValidation, editorLocatorHref } from "./editor-locator";

const document = {
  metadata: { key: "demo" },
  initialization: { start_node_key: "room", primary_actor_key: "medic", resource_pools: [{ pool_key: "pool-a", resource_key: "water", region_key: "central" }], region_resource_knowledge: [{ region_key: "central" }] },
  planning: { instructions: ["First instruction", "Second instruction"], recovery_hints: [{ failure_code: "BLOCKED", hint: "Retry" }] },
  world: { nodes: [{ key: "room", node_type_key: "facility", facts: [{ key: "ready" }] }], node_types: [{ key: "facility" }], relations: [{ source_node_key: "room", relation_type_key: "located_in", target_node_key: "central" }], resources: [{ key: "water", name: "Water", source_hint: { primary_region_key: "central" } }] },
  actors: { actor_profiles: [{ key: "medic", role_key: "medic_role" }] },
};

describe("owner-aware editor locators", () => {
  it("maps singleton, split relation entities, root collection, Resource source hints, and public references", () => {
    expect(editorLocatorFromValidation({ object_kind: "metadata", object_key: null, field_path: "name" }, document)).toEqual({ owner: "singleton", section: "overview", fieldPath: "name" });
    expect(editorLocatorFromValidation({ object_kind: "action", object_key: "repair", field_path: "description" }, document)).toEqual({ owner: "entity", section: "actions", kind: "action", objectKey: "repair", fieldPath: "description" });
    expect(editorLocatorFromValidation({ object_kind: "relation_type", object_key: "located_in", field_path: "name" }, document)).toEqual({ owner: "entity", section: "relation-types", kind: "relation_type", objectKey: "located_in", fieldPath: "name" });
    expect(editorLocatorFromValidation({ object_kind: "relation", object_key: "same", field_path: "source_node_key" }, document)).toEqual({ owner: "entity", section: "relations", kind: "relation", objectKey: "same", fieldPath: "source_node_key" });
    expect(editorLocatorFromValidation({ object_kind: "planning", object_key: null, field_path: "recovery_hints.0.hint" }, document)).toEqual({ owner: "root-collection", section: "planning-recovery", collection: "recovery_hints", identity: JSON.stringify(["BLOCKED"]), fieldPath: "recovery_hints.0.hint" });
    expect(editorLocatorFromValidation({ object_kind: "planning", object_key: null, field_path: "instructions.1" }, document)).toEqual({ owner: "planning-instruction", section: "planning-instructions", index: 1, fieldPath: "instructions.1" });
    expect(editorLocatorFromValidation({ object_kind: "resource", object_key: "water", field_path: "source_hint.primary_region_key" }, document)).toEqual({ owner: "entity", section: "resources", kind: "resource", objectKey: "water", fieldPath: "source_hint.primary_region_key" });
    expect(editorLocatorFromValidation({ object_kind: "public_knowledge", object_key: "water", field_path: "source_hint.primary_region_key" }, document)).toEqual({ owner: "entity", section: "resources", kind: "resource", objectKey: "water", fieldPath: "source_hint" });
    expect(editorLocatorFromValidation({ object_kind: "public_reference", object_key: "REGION:central:Central", field_path: "term" }, document)).toEqual({ owner: "entity", section: "terminology-references", kind: "public_reference", objectKey: "REGION:central:Central", fieldPath: "term" });
  });

  it("maps Initialization-owned fields to their canonical workspace item", () => {
    expect(editorLocatorFromValidation({ object_kind: "node", object_key: "room", field_path: "initial_access" }, document)).toEqual({ owner: "initialization-item", section: "initialization", domain: "nodes", group: "node-type:facility", item: "node:room", fieldPath: "world.nodes.room.initial_access" });
    expect(editorLocatorFromValidation({ object_kind: "node", object_key: "room", field_path: "facts.ready.initial_value" }, document)).toEqual({ owner: "initialization-item", section: "initialization", domain: "nodes", group: "node-type:facility", item: "node:room", fieldPath: "world.nodes.room.facts.ready.initial_value" });
    expect(editorLocatorFromValidation({ object_kind: "actor", object_key: "medic", field_path: "command_reachability" }, document)).toEqual({ owner: "initialization-item", section: "initialization", domain: "actors", group: "role:medic_role", item: "actor:medic", fieldPath: "actors.actor_profiles.medic.command_reachability" });
    expect(editorLocatorFromValidation({ object_kind: "relation", object_key: "room__located_in__central", field_path: "initial_visibility" }, document)).toEqual({ owner: "initialization-item", section: "initialization", domain: "relations", group: "relation-type:located_in", item: "relation:room__located_in__central", fieldPath: "world.relations.room__located_in__central.initial_visibility" });
    expect(editorLocatorFromValidation({ object_kind: "resource_pool", object_key: "pool-a", field_path: "quantity" }, document)).toEqual({ owner: "initialization-item", section: "initialization", domain: "resources", group: "resource-pools", item: "pool:pool-a:water:central", fieldPath: "initialization.resource_pools.pool-a.quantity" });
    expect(editorLocatorFromValidation({ object_kind: "region_resource_knowledge", object_key: "central", field_path: "resource_inventory_visibility" }, document)).toEqual({ owner: "initialization-item", section: "initialization", domain: "resources", group: "region-resource-knowledge", item: "region-knowledge:central", fieldPath: "initialization.region_resource_knowledge.central.resource_inventory_visibility" });
    expect(editorLocatorFromValidation({ object_kind: "initialization", object_key: null, field_path: "start_node_key" }, document)).toEqual({ owner: "initialization-item", section: "initialization", domain: "basic", group: "entry", item: "bootstrap-entry", fieldPath: "initialization.start_node_key" });
    expect(editorLocatorFromValidation({ object_kind: "node", object_key: "room", field_path: "name" }, document)?.owner).toBe("entity");
  });

  it("serializes each owner without collapsing kind or collection identity", () => {
    expect(editorLocatorHref({ owner: "entity", section: "relations", kind: "relation", objectKey: "same", fieldPath: "target_node_key" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/relations/same?focus_path=target_node_key&kind=relation");
    expect(editorLocatorHref({ owner: "entity", section: "relations", kind: "relation_type", objectKey: "located_in", fieldPath: "name" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/relation-types/located_in?focus_path=name");
    expect(editorLocatorHref({ owner: "root-collection", section: "initialization", collection: "resource_pools", identity: JSON.stringify(["pool-a"]), fieldPath: "resource_pools.0.quantity" }, "scenario-1"))
      .toContain("owner=collection&collection=resource_pools&item=%5B%22pool-a%22%5D");
    expect(editorLocatorHref({ owner: "browser", section: "world", contextKind: "node", contextKey: "central" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/world?context=node&key=central");
    expect(editorLocatorHref({ owner: "workflow", section: "validation", issuePath: "world.nodes.0.key" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/validation?focus_path=world.nodes.0.key");
    expect(editorLocatorHref({ owner: "planning-instruction", section: "planning-instructions", index: 1, fieldPath: "instructions.1" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/planning-instructions?owner=instruction&item=1&focus_path=instructions.1");
    expect(editorLocatorHref({ owner: "initialization-item", section: "initialization", domain: "nodes", group: "node-type:facility", item: "node:room", fieldPath: "world.nodes.room.initial_access" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/initialization?domain=nodes&group=node-type%3Afacility&item=node%3Aroom&focus_path=world.nodes.room.initial_access");
  });
});
