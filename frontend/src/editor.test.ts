import { describe, expect, it } from "vitest";

import { referenceOptions } from "./components/editor/ReferencePicker";
import { addObject as addEditorObject, filterDraftObjects, nodeSemanticView, objectIdentity, replaceObject, sectionDefinition, sectionObjects, sectionRegistry, sections, updateObjectName } from "./editor";
import { entityRegistry, factInitialValueMetadata } from "./editor-registry";
import { addObject, defaultArrayItem, kindsBySection } from "./templates";

describe("editor draft helpers", () => {
  const document = { world: { nodes: [{ key: "clinic", name: "Clinic" }] } };

  it("discovers section objects from the one Draft document", () => {
    expect(sectionObjects(document, "world")[0]).toMatchObject({ kind: "node", key: "clinic" });
  });

  it("updates a display name without changing the stable key or source", () => {
    const changed = updateObjectName(document, "world", "clinic", "Emergency Clinic");
    expect(sectionObjects(changed, "world")[0]).toMatchObject({ key: "clinic", name: "Emergency Clinic" });
    expect(sectionObjects(document, "world")[0].name).toBe("Clinic");
  });

  it("adds generic objects and edits their structured value", () => {
    const added = addObject({ world: { nodes: [] } }, "node");
    const node = sectionObjects(added.document, "world")[0];
    const changed = replaceObject(added.document, "world", node.key, { ...node.value, name: "Harbor" });
    expect(sectionObjects(changed, "world")[0].name).toBe("Harbor");
  });

  it("creates engine-supported AST and objective requirement shapes", () => {
    expect(defaultArrayItem("conditions")).toMatchObject({ kind: "FACT_EQUALS" });
    expect(defaultArrayItem("effects")).toMatchObject({ kind: "EMIT_OUTCOME" });
    expect(defaultArrayItem("completion_requirements")).toMatchObject({ node_key: "node", fact_key: "fact" });
  });

  it("registers every V2 authoring section without adding semantic collections", () => {
    expect(sectionRegistry.map((item) => item.id)).toEqual([...sections]);
    expect(sectionRegistry.find((item) => item.id === "relations")?.entityKinds).toEqual(["relation_type", "relation"]);
    expect(kindsBySection.roles).toEqual(["role"]);
    expect(kindsBySection.actors).toEqual(["actor"]);
    expect(sectionDefinition("world")?.entityKinds).toEqual(["node_type", "node", "relation_type", "relation", "resource"]);
    expect(entityRegistry.node.collectionPath).toEqual(["world", "nodes"]);
  });

  it("keeps Region, Facility, and Transport as Node semantic views", () => {
    const viewDocument = { metadata: { locality: { region_node_type_key: "region", facility_node_type_key: "facility", transport_node_type_key: "transport" } }, world: { nodes: [
      { key: "r", name: "R", node_type_key: "region" }, { key: "f", name: "F", node_type_key: "facility" }, { key: "t", name: "T", node_type_key: "transport" },
    ] } };
    expect(nodeSemanticView(viewDocument, "regions").map((item) => item.key)).toEqual(["r"]);
    expect(nodeSemanticView(viewDocument, "facilities").map((item) => item.key)).toEqual(["f"]);
    expect(nodeSemanticView(viewDocument, "transports").map((item) => item.key)).toEqual(["t"]);
  });

  it("selects a typed initial value editor for every Fact value type", () => {
    expect(factInitialValueMetadata("BOOLEAN").type).toBe("boolean");
    expect(factInitialValueMetadata("INTEGER").type).toBe("integer");
    expect(factInitialValueMetadata("STRING").type).toBe("text");
    expect(factInitialValueMetadata("ENUM", ["A", "B"]).enum).toEqual(["A", "B"]);
  });

  it("round-trips an edited typed field while preserving unhandled fields", () => {
    const original = { world: { nodes: [{ key: "clinic", name: "Clinic", node_type_key: "facility", custom_engine_field: { keep: true } }] }, extra_root: { keep: true } };
    const node = sectionObjects(original, "world")[0];
    const changed = replaceObject(original, "world", node.key, { ...node.value, name: "Renamed" });
    expect(changed.world).toMatchObject({ nodes: [{ key: "clinic", name: "Renamed", custom_engine_field: { keep: true } }] });
    expect(changed.extra_root).toEqual({ keep: true });
  });

  it("reference pickers display names but return stable keys", () => {
    const pickerDocument = { world: { node_types: [], nodes: [{ key: "central", name: "Central Node" }] } };
    expect(referenceOptions(pickerDocument, "node")).toEqual([{ key: "central", name: "Central Node" }]);
  });

  it("filters object lists by display name, stable key, and semantic kind", () => {
    const objects = sectionObjects({ world: { nodes: [{ key: "central", name: "Central Hospital", node_type_key: "facility" }, { key: "south_bridge", name: "South Bridge", node_type_key: "transport" }] } }, "world");
    expect(filterDraftObjects(objects, "hospital").map((item) => item.key)).toEqual(["central"]);
    expect(filterDraftObjects(objects, "south_bridge").map((item) => item.key)).toEqual(["south_bridge"]);
    expect(filterDraftObjects(objects, "", "node")).toHaveLength(2);
    expect(filterDraftObjects(objects, "", "relation")).toHaveLength(0);
  });

  it("keeps sibling relation kinds distinct when their identities match", () => {
    const original = { world: {
      relation_types: [{ key: "same", name: "Same type" }],
      relations: [{ key: "same", source_node_key: "a", relation_type_key: "same", target_node_key: "b" }],
    } };
    const changed = replaceObject(original, "relations", "same", { key: "same", source_node_key: "b", relation_type_key: "same", target_node_key: "a" }, "relation");
    expect(sectionObjects(changed, "relations").find((item) => item.kind === "relation_type")?.value.name).toBe("Same type");
    expect(sectionObjects(changed, "relations").find((item) => item.kind === "relation")?.value.source_node_key).toBe("b");
  });

  it("creates and derives a complete PublicReference composite identity", () => {
    const added = addEditorObject({ world: { nodes: [{ key: "central", name: "Central" }], resources: [] }, public_references: [] }, "public_reference");
    const reference = sectionObjects(added.document, "public-references")[0];
    expect(reference.key).toBe("NODE:central:New reference");
    expect(objectIdentity("public_reference", reference.value)).toBe(reference.key);
  });
});
