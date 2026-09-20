import { describe, expect, it } from "vitest";

import { referenceOptions } from "./components/editor/ReferencePicker";
import { addObject as addEditorObject, filterDraftObjects, nodeSemanticView, objectIdentity, replaceObject, ruleDisplayTitle, sectionDefinition, sectionObjects, sectionRegistry, sections, updateObjectName } from "./editor";
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

  it("creates engine-supported AST shapes", () => {
    expect(defaultArrayItem("conditions")).toMatchObject({ kind: "FACT_EQUALS" });
    expect(defaultArrayItem("effects")).toMatchObject({ kind: "EMIT_OUTCOME" });
  });

  it("registers every V2 authoring section without adding semantic collections", () => {
    expect(sectionRegistry.map((item) => item.id)).toEqual([...sections]);
    expect(sectionRegistry.find((item) => item.id === "relation-types")?.entityKinds).toEqual(["relation_type"]);
    expect(sectionRegistry.find((item) => item.id === "relations")?.entityKinds).toEqual(["relation"]);
    expect(kindsBySection.roles).toEqual(["role"]);
    expect(kindsBySection.actors).toEqual(["actor"]);
    expect(sectionDefinition("world")?.entityKinds).toEqual(["node_type", "node", "relation_type", "relation", "resource"]);
    expect(entityRegistry.node.collectionPath).toEqual(["world", "nodes"]);
    expect(sectionRegistry.map((item) => String(item.id))).not.toContain("objectives");
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

  it("searches relation instances by authored endpoint and relation type names", () => {
    const relationDocument = {
      world: {
        nodes: [{ key: "north_a", name: "北部设施" }, { key: "north", name: "北部范围" }],
        relation_types: [{ key: "belongs_to", name: "归属", description: "设施归属范围" }],
        relations: [{ key: "north_a__belongs_to__north", source_node_key: "north_a", relation_type_key: "belongs_to", target_node_key: "north" }],
      },
    };
    const objects = sectionObjects(relationDocument, "relations");
    expect(objects[0].name).toBe("北部设施 → 北部范围 · 归属");
    expect(filterDraftObjects(objects, "北部设施")).toHaveLength(1);
    expect(filterDraftObjects(objects, "归属")).toHaveLength(1);
    expect(filterDraftObjects(objects, "belongs_to")).toHaveLength(1);
    expect(filterDraftObjects(sectionObjects(relationDocument, "relation-types"), "设施归属范围")).toHaveLength(1);
  });

  it("projects Rule titles from authored action, phase, trigger, and effects", () => {
    const ruleDocument = { actions: [{ key: "repair", name: "修复设施" }] };
    expect(ruleDisplayTitle(ruleDocument, { action_key: "repair", phase: "PREFLIGHT", trigger: "ACTION", effects: [{ kind: "EMIT_FAILURE" }] })).toBe("修复设施 · 前置校验");
    expect(ruleDisplayTitle(ruleDocument, { action_key: "repair", phase: "RESOLVE", trigger: "ACTION", effects: [{ kind: "EMIT_OUTCOME" }] })).toBe("修复设施 · 完成处理");
    expect(ruleDisplayTitle(ruleDocument, { phase: "RESOLVE", trigger: "STATE", effects: [{ kind: "SET_FACT" }] })).toBe("状态规则 · 状态处理");
    expect(ruleDisplayTitle(ruleDocument, { action_key: "missing", phase: "RESOLVE", trigger: "ACTION", effects: [{ kind: "EMIT_FAILURE" }] })).toBe("规则 · 失败处理");
  });

  it("searches Rule semantic titles while preserving machine identity", () => {
    const objects = sectionObjects({
      actions: [{ key: "repair", name: "修复设施" }],
      rules: [{ key: "repair_preflight", action_key: "repair", phase: "PREFLIGHT", trigger: "ACTION", effects: [{ kind: "EMIT_FAILURE" }] }],
    }, "rules");
    expect(objects[0]).toMatchObject({ name: "修复设施 · 前置校验", key: "repair_preflight" });
    expect(filterDraftObjects(objects, "修复设施")).toHaveLength(1);
    expect(filterDraftObjects(objects, "repair_preflight")).toHaveLength(1);
  });

  it("keeps sibling relation kinds distinct when their identities match", () => {
    const original = { world: {
      relation_types: [{ key: "same", name: "Same type" }],
      relations: [{ key: "same", source_node_key: "a", relation_type_key: "same", target_node_key: "b" }],
    } };
    const changed = replaceObject(original, "relations", "same", { key: "same", source_node_key: "b", relation_type_key: "same", target_node_key: "a" }, "relation");
    expect(sectionObjects(changed, "relation-types").find((item) => item.kind === "relation_type")?.value.name).toBe("Same type");
    expect(sectionObjects(changed, "relations").find((item) => item.kind === "relation")?.value.source_node_key).toBe("b");
  });

  it("creates and derives a complete PublicReference composite identity", () => {
    const added = addEditorObject({ world: { nodes: [{ key: "central", name: "Central" }], resources: [] }, public_references: [] }, "public_reference");
    const reference = sectionObjects(added.document, "public-references")[0];
    expect(reference.key).toBe("NODE:central:New reference");
    expect(objectIdentity("public_reference", reference.value)).toBe(reference.key);
  });
});
