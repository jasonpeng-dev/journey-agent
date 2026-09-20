import { describe, expect, it } from "vitest";

import { editorLocatorFromValidation, editorLocatorHref } from "./editor-locator";

const document = {
  metadata: { key: "demo" },
  initialization: { resource_pools: [{ pool_key: "pool-a" }] },
  planning: { recovery_hints: [{ failure_code: "BLOCKED", hint: "Retry" }] },
  public_knowledge: { resource_source_hints: [{ resource_key: "water" }] },
};

describe("owner-aware editor locators", () => {
  it("maps singleton, split relation entities, root collection, and public reference locators", () => {
    expect(editorLocatorFromValidation({ object_kind: "metadata", object_key: null, field_path: "name" }, document)).toEqual({ owner: "singleton", section: "overview", fieldPath: "name" });
    expect(editorLocatorFromValidation({ object_kind: "action", object_key: "repair", field_path: "description" }, document)).toEqual({ owner: "entity", section: "actions", kind: "action", objectKey: "repair", fieldPath: "description" });
    expect(editorLocatorFromValidation({ object_kind: "relation_type", object_key: "located_in", field_path: "name" }, document)).toEqual({ owner: "entity", section: "relation-types", kind: "relation_type", objectKey: "located_in", fieldPath: "name" });
    expect(editorLocatorFromValidation({ object_kind: "relation", object_key: "same", field_path: "source_node_key" }, document)).toEqual({ owner: "entity", section: "relations", kind: "relation", objectKey: "same", fieldPath: "source_node_key" });
    expect(editorLocatorFromValidation({ object_kind: "planning", object_key: null, field_path: "recovery_hints.0.hint" }, document)).toEqual({ owner: "root-collection", section: "planning", collection: "recovery_hints", identity: JSON.stringify(["BLOCKED"]), fieldPath: "recovery_hints.0.hint" });
    expect(editorLocatorFromValidation({ object_kind: "public_reference", object_key: "REGION:central:Central", field_path: "term" }, document)).toEqual({ owner: "entity", section: "public-references", kind: "public_reference", objectKey: "REGION:central:Central", fieldPath: "term" });
  });

  it("serializes each owner without collapsing kind or collection identity", () => {
    expect(editorLocatorHref({ owner: "entity", section: "relations", kind: "relation", objectKey: "same", fieldPath: "target_node_key" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/relations/same?focus_path=target_node_key");
    expect(editorLocatorHref({ owner: "entity", section: "relations", kind: "relation_type", objectKey: "located_in", fieldPath: "name" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/relation-types/located_in?focus_path=name");
    expect(editorLocatorHref({ owner: "root-collection", section: "initialization", collection: "resource_pools", identity: JSON.stringify(["pool-a"]), fieldPath: "resource_pools.0.quantity" }, "scenario-1"))
      .toContain("owner=collection&collection=resource_pools&item=%5B%22pool-a%22%5D");
    expect(editorLocatorHref({ owner: "browser", section: "world", contextKind: "node", contextKey: "central" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/world?context=node&key=central");
    expect(editorLocatorHref({ owner: "workflow", section: "validation", issuePath: "world.nodes.0.key" }, "scenario-1"))
      .toBe("/scenarios/scenario-1/edit/validation?focus_path=world.nodes.0.key");
  });
});
