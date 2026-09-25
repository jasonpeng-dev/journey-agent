import { describe, expect, it } from "vitest";

import { referenceOptions } from "./components/editor/ReferencePicker";
import { addObject as addEditorObject, appendNestedIdentityFromDialog, filterDraftObjects, nodeSemanticView, objectIdentity, replaceObject, ruleDisplayTitle, sectionDefinition, sectionObjects, sectionRegistry, sections, updateObjectName } from "./editor";
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
    const added = addObject({ world: { node_types: [{ key: "facility", name: "Facility" }], nodes: [] } }, "node", { key: "harbor", name: "Harbor", node_type_key: "facility" });
    const node = sectionObjects(added.document, "world")[0];
    const changed = replaceObject(added.document, "world", node.key, { ...node.value, name: "Harbor" });
    expect(sectionObjects(changed, "world")[0].name).toBe("Harbor");
  });

  it("creates the first generic authoring objects in a blank scenario without inferred dependencies", () => {
    let blank = {};
    blank = addObject(blank, "node_type", { key: "facility", name: "Facility" }).document;
    blank = addObject(blank, "node", { key: "harbor", name: "Harbor", node_type_key: "facility" }).document;
    blank = addObject(blank, "role", { key: "operator", name: "Operator" }).document;
    blank = addObject(blank, "actor", { key: "mira", name: "Mira", role_key: "operator", initial_node_key: "harbor", persona: "Field operator" }).document;
    blank = addObject(blank, "interaction", { key: "inspect", name: "Inspect" }).document;
    blank = addObject(blank, "action", { key: "repair", name: "Repair", required_interaction_key: "inspect" }).document;

    expect(sectionObjects(blank, "node-types")).toHaveLength(1);
    expect(sectionObjects(blank, "world-entities")[0].value.node_type_key).toBe("facility");
    expect(sectionObjects(blank, "roles")).toHaveLength(1);
    expect(sectionObjects(blank, "actors")[0].value.role_key).toBe("operator");
    expect(sectionObjects(blank, "interactions")).toHaveLength(1);
    expect(sectionObjects(blank, "actions")[0].value.required_interaction_key).toBe("inspect");
  });

  it("validates Action and Actor stable keys at the creation boundary", () => {
    const withRoleAndNode = { world: { nodes: [{ key: "harbor" }] }, actors: { roles: [{ key: "operator" }] } };
    expect(() => addEditorObject(withRoleAndNode, "actor", { key: "Bad Actor", name: "Mira", role_key: "operator", initial_node_key: "harbor", persona: "Field operator" })).toThrow(/稳定键/);
    expect(() => addEditorObject({ interactions: [{ key: "inspect" }] }, "action", { key: "Bad Action", name: "Repair", required_interaction_key: "inspect" })).toThrow(/稳定键/);
  });

  it("creates engine-supported AST shapes", () => {
    expect(defaultArrayItem("conditions")).toMatchObject({ kind: "FACT_EQUALS" });
    expect(defaultArrayItem("effects")).toMatchObject({ kind: "EMIT_OUTCOME" });
    for (const field of ["facts", "parameters", "expected_outcomes", "operation_bindings", "dependencies", "resource_pools", "region_resource_knowledge", "recovery_hints"]) {
      expect(defaultArrayItem(field)).toBeNull();
    }
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

  it("respects authored relation type names without changing their stable keys", () => {
    const relationDocument = { world: { relation_types: [{ key: "supplies_power_to", name: "供电至" }] } };
    expect(referenceOptions(relationDocument, "relation_type")).toEqual([{ key: "supplies_power_to", name: "供电至" }]);
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

  it("prevents generic Relation endpoint edits after composite identity is complete", () => {
    const original = { world: {
      relation_types: [{ key: "same", name: "Same type" }],
      relations: [{ key: "same", source_node_key: "a", relation_type_key: "same", target_node_key: "b" }],
    } };
    const changed = replaceObject(original, "relations", "same", { key: "same", source_node_key: "b", relation_type_key: "same", target_node_key: "a" }, "relation");
    expect(sectionObjects(changed, "relation-types").find((item) => item.kind === "relation_type")?.value.name).toBe("Same type");
    expect(sectionObjects(changed, "relations").find((item) => item.kind === "relation")?.value.source_node_key).toBe("a");
  });

  it("requires the complete Relation composite identity at creation and locks it afterward", () => {
    expect(() => addEditorObject({ world: { nodes: [{ key: "a" }], relation_types: [{ key: "connected_to" }], relations: [] } }, "relation", {})).toThrow();
    const added = addEditorObject({ world: { nodes: [{ key: "a", name: "A" }, { key: "b", name: "B" }], relation_types: [{ key: "connected_to", name: "Connected to" }], relations: [] } }, "relation", {
      source_node_key: "a", relation_type_key: "connected_to", target_node_key: "b",
    });
    const locked = replaceObject(added.document, "relations", added.key, {
      ...sectionObjects(added.document, "relations")[0].value,
      source_node_key: "b", target_node_key: "a",
    }, "relation");
    expect(sectionObjects(added.document, "relations")[0].value).toMatchObject({ source_node_key: "a", relation_type_key: "connected_to", target_node_key: "b" });
    expect(sectionObjects(locked, "relations")[0].value).toMatchObject({ source_node_key: "a", relation_type_key: "connected_to", target_node_key: "b" });
  });

  it.each(["action", "derived_state"] as const)("blocks generic %s key changes", (kind) => {
    const key = kind === "action" ? "inspect" : "summary";
    const section = kind === "action" ? "actions" : "derived-states";
    const document = { [section === "actions" ? "actions" : "derived_states"]: [{ key, name: "Original" }] };
    const changed = replaceObject(document, section, key, { key: `${key}_renamed`, name: "Original" }, kind);
    expect(sectionObjects(changed, section).find((item) => item.kind === kind)?.key).toBe(key);
  });

  it("only appends nested identities through the explicit dialog mutation", () => {
    const original = { actions: [{ key: "repair", name: "Repair", parameters: [] }] };
    const withParameter = { ...original.actions[0], parameters: [{ key: "dose", name: "Dose", value_type: "INTEGER", required: true }] };
    expect(replaceObject(original, "actions", "repair", withParameter, "action")).toEqual(original);

    const created = appendNestedIdentityFromDialog(original, "actions", "repair", withParameter, "action");
    expect((created?.actions as Array<Record<string, unknown>>)[0].parameters).toEqual(withParameter.parameters);
    expect(appendNestedIdentityFromDialog(created!, "actions", "repair", {
      ...withParameter,
      parameters: [...withParameter.parameters, { key: "dose", name: "Duplicate", value_type: "INTEGER", required: true }],
    }, "action")).toBeNull();
    expect(appendNestedIdentityFromDialog(original, "actions", "repair", {
      ...withParameter,
      key: "other_action",
    }, "action")).toBeNull();
    expect(appendNestedIdentityFromDialog(original, "actions", "repair", {
      ...withParameter,
      parameters: [{ key: "", name: "", value_type: "STRING", required: true }],
    }, "action")).toBeNull();
    const incompleteDetail = appendNestedIdentityFromDialog(original, "actions", "repair", {
      ...withParameter,
      parameters: [{ key: "dose", name: "", value_type: "STRING", required: true }],
    }, "action");
    expect((incompleteDetail?.actions as Array<Record<string, unknown>>)[0].parameters).toEqual([
      { key: "dose", name: "", value_type: "STRING", required: true },
    ]);
  });

  it("creates and derives a complete PublicReference composite identity", () => {
    const added = addEditorObject({ world: { nodes: [{ key: "central", name: "Central" }], resources: [] }, public_references: [] }, "public_reference", { term: "New reference", ref_type: "NODE", ref_key: "central" });
    const reference = sectionObjects(added.document, "terminology-references")[0];
    expect(reference.key).toBe("NODE:central:New reference");
    expect(objectIdentity("public_reference", reference.value)).toBe(reference.key);
  });
});
