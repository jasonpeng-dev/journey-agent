import { describe, expect, it } from "vitest";

import { ApiError } from "../../api";
import type { DraftObject, JsonObject } from "../../editor";
import { initializationPreviewIssuePresentations, issueBelongsToFocusedObject } from "./initialization-preview-errors";

const document: JsonObject = {
  world: {
    nodes: [
      { key: "north", name: "北区医院", node_type_key: "missing_type", facts: [{ key: "ready", name: "准备状态", value_type: "BOOLEAN" }] },
      { key: "south", name: "南区医院", node_type_key: "facility" },
    ],
    node_types: [{ key: "facility", name: "设施" }],
    relations: [{ source_node_key: "north", relation_type_key: "located_in", target_node_key: "south" }],
    resources: [{ key: "water", name: "饮用水" }],
  },
  actors: { actor_profiles: [{ key: "medic", name: "医护队", role_key: "responder" }] },
  initialization: { start_node_key: "north", primary_actor_key: "medic", resource_pools: [{ pool_key: "main_water", resource_key: "water", region_key: null, quantity: 4 }], region_resource_knowledge: [{ region_key: "north" }] },
};

const currentNode: DraftObject = {
  kind: "node",
  key: "north",
  name: "北区医院",
  value: document.world && typeof document.world === "object" && !Array.isArray(document.world)
    ? ((document.world as JsonObject).nodes as JsonObject[])[0]
    : {},
  path: ["node", "north"],
};

function previewError(issues: Array<Record<string, unknown>>) {
  return new ApiError("The working document cannot produce an initialization preview", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", { issues });
}

describe("initialization preview failure guidance", () => {
  it("scopes issues by structured primary object and retains direct cross-owner blockers", () => {
    const nodeAAccess = { type: "missing", loc: ["world", "nodes", 0, "initial_access"], msg: "Field required" };
    const nodeBName = { type: "missing", loc: ["world", "nodes", 1, "name"], msg: "Field required" };
    const nodeATypeReference = {
      canonical_owner: "world-entities",
      reference_owner: "node-types",
      field_path: "node_type_key",
      locator: { section: "world-entities", object_kind: "node", object_key: "north", field_path: "node_type_key" },
      loc: [],
      msg: "A reference is not valid",
    };

    expect(issueBelongsToFocusedObject(nodeAAccess, { kind: "node", key: "north" }, document)).toBe(true);
    expect(issueBelongsToFocusedObject(nodeBName, { kind: "node", key: "north" }, document)).toBe(false);
    expect(issueBelongsToFocusedObject(nodeBName, { kind: "node", key: "south" }, document)).toBe(true);
    expect(issueBelongsToFocusedObject(nodeATypeReference, { kind: "node", key: "north" }, document)).toBe(true);
    expect(issueBelongsToFocusedObject(nodeATypeReference, { kind: "node", key: "south" }, document)).toBe(false);
  });

  it("uses stable Fact identity and resource-pool ownership for nested Initialization issues", () => {
    const factIssue = {
      identity: "fact:north:ready",
      field_path: "facts.ready.initial_value",
      locator: { section: "world-entities", object_kind: "node", object_key: "north:ready", field_path: "facts.ready.initial_value" },
      loc: [],
      msg: "Field required",
    };
    const poolIssue = {
      field_path: "quantity",
      locator: { section: "initialization", object_kind: "resource_pool", object_key: "main_water", field_path: "quantity" },
      loc: [],
      msg: "Field required",
    };

    expect(issueBelongsToFocusedObject(factIssue, { kind: "node", key: "north", factKey: "ready" }, document)).toBe(true);
    expect(issueBelongsToFocusedObject(factIssue, { kind: "node", key: "north", factKey: "other" }, document)).toBe(false);
    expect(issueBelongsToFocusedObject(poolIssue, { kind: "resource", key: "water" }, document)).toBe(true);
    expect(issueBelongsToFocusedObject(poolIssue, { kind: "resource", key: "unrelated" }, document)).toBe(false);
  });

  it("maps an incomplete newly-created current Node field to its exact authoring locator", () => {
    const [issue] = initializationPreviewIssuePresentations(previewError([
      { type: "missing", loc: ["world", "nodes", 0, "name"], msg: "Field required" },
    ]), currentNode, document, "scenario-1");

    expect(issue.category).toBe("current");
    expect(issue.title).toContain("当前");
    expect(issue.reason).toContain("名称");
    expect(issue.href).toBe("/scenarios/scenario-1/edit/world-entities/north?focus_path=name");
    expect(issue.actionLabel).toContain("定位到");
    expect(issue.reason).not.toContain("Field required");
    expect(issue.reason).not.toContain("world.nodes");
  });

  it("keeps a missing cross-owner NodeType actionable at the current field and canonical owner", () => {
    const [issue] = initializationPreviewIssuePresentations(previewError([
      { type: "value_error", loc: [], msg: "Value error, Node north type references unknown key missing_type" },
    ]), currentNode, document, "scenario-1");

    expect(issue.category).toBe("dependency");
    expect(issue.href).toBe("/scenarios/scenario-1/edit/world-entities/north?focus_path=node_type_key");
    expect(issue.ownerHref).toBe("/scenarios/scenario-1/edit/node-types");
    expect(issue.ownerActionLabel).toBe("前往节点类型");
    expect(issue.rawMessage).toContain("unknown key");
    expect(issue.reason).not.toContain("unknown key");
  });

  it("routes Node Initialization issues to the exact Initialization field instead of Design", () => {
    const accessIssue = initializationPreviewIssuePresentations(previewError([
      { type: "missing", loc: ["world", "nodes", 0, "initial_access"], msg: "Field required" },
    ]), currentNode, document, "scenario-1")[0];
    const visibilityIssue = initializationPreviewIssuePresentations(previewError([
      { type: "missing", loc: ["world", "nodes", 0, "initial_visibility"], msg: "Field required" },
    ]), currentNode, document, "scenario-1")[0];

    expect(accessIssue.href).toBe("/scenarios/scenario-1/edit/initialization?domain=nodes&group=node-type%3Amissing_type&item=node%3Anorth&focus_path=world.nodes.north.initial_access");
    expect(accessIssue.problemLabel).toBe("初始访问状态");
    expect(visibilityIssue.href).toBe("/scenarios/scenario-1/edit/initialization?domain=nodes&group=node-type%3Amissing_type&item=node%3Anorth&focus_path=world.nodes.north.initial_visibility");
    expect(visibilityIssue.problemLabel).toBe("初始可见性");
    expect(accessIssue.href).not.toContain("world-entities");
    expect(visibilityIssue.href).not.toContain("world-entities");
    expect(accessIssue.groupTitle).toBe("世界实体 · 北区医院");
  });

  it("resolves Initialization fields from Facts, Actors, Relations, pools, regions, and the bootstrap item", () => {
    const cases: Array<{ loc: Array<string | number>; expected: string }> = [
      { loc: ["world", "nodes", 0, "facts", 0, "initial_value"], expected: "/scenarios/scenario-1/edit/initialization?domain=nodes&group=node-type%3Amissing_type&item=node%3Anorth&focus_path=world.nodes.north.facts.ready.initial_value" },
      { loc: ["actors", "actor_profiles", 0, "initial_node_key"], expected: "/scenarios/scenario-1/edit/initialization?domain=actors&group=role%3Aresponder&item=actor%3Amedic&focus_path=actors.actor_profiles.medic.initial_node_key" },
      { loc: ["world", "relations", 0, "initial_visibility"], expected: "/scenarios/scenario-1/edit/initialization?domain=relations&group=relation-type%3Alocated_in&item=relation%3Anorth__located_in__south&focus_path=world.relations.north__located_in__south.initial_visibility" },
      { loc: ["initialization", "resource_pools", 0, "quantity"], expected: "/scenarios/scenario-1/edit/initialization?domain=resources&group=resource-pools&item=pool%3Amain_water%3Awater%3Aglobal&focus_path=initialization.resource_pools.main_water.quantity" },
      { loc: ["initialization", "region_resource_knowledge", 0, "resource_inventory_visibility"], expected: "/scenarios/scenario-1/edit/initialization?domain=resources&group=region-resource-knowledge&item=region-knowledge%3Anorth&focus_path=initialization.region_resource_knowledge.north.resource_inventory_visibility" },
      { loc: ["initialization", "start_node_key"], expected: "/scenarios/scenario-1/edit/initialization?domain=basic&group=entry&item=bootstrap-entry&focus_path=initialization.start_node_key" },
    ];
    for (const { loc, expected } of cases) {
      const [issue] = initializationPreviewIssuePresentations(previewError([{ type: "missing", loc, msg: "Field required" }]), currentNode, document, "scenario-1");
      expect(issue.href).toBe(expected);
    }
  });

  it("still identifies the current NodeType field when the reference is blank", () => {
    const [issue] = initializationPreviewIssuePresentations(previewError([
      { type: "value_error", loc: [], msg: "Value error, Node north type references unknown key " },
    ]), currentNode, document, "scenario-1");

    expect(issue.category).toBe("dependency");
    expect(issue.href).toBe("/scenarios/scenario-1/edit/world-entities/north?focus_path=node_type_key");
    expect(issue.ownerHref).toBe("/scenarios/scenario-1/edit/node-types");
    expect(issue.reason).toContain("节点类型");
  });

  it("attributes another object's invalid field to that object, not the current Node", () => {
    const [issue] = initializationPreviewIssuePresentations(previewError([
      { type: "string_too_short", loc: ["world", "nodes", 1, "name"], msg: "String should have at least 1 character" },
    ]), currentNode, document, "scenario-1");

    expect(issue.category).toBe("other");
    expect(issue.title).toContain("南区医院");
    expect(issue.href).toBe("/scenarios/scenario-1/edit/world-entities/south?focus_path=name");
    expect(issue.title).not.toContain("北区医院");
  });

  it("uses structured owner and locator metadata ahead of an ambiguous raw validation path", () => {
    const [issue] = initializationPreviewIssuePresentations(previewError([
      {
        type: "value_error",
        loc: ["world", "nodes", 1, "name"],
        msg: "A reference is not valid",
        canonical_owner: "world-entities",
        reference_owner: "node-types",
        field_path: "node_type_key",
        locator: { object_kind: "node", object_key: "north", field_path: "node_type_key" },
      },
    ]), currentNode, document, "scenario-1");

    expect(issue.category).toBe("dependency");
    expect(issue.href).toBe("/scenarios/scenario-1/edit/world-entities/north?focus_path=node_type_key");
    expect(issue.ownerHref).toBe("/scenarios/scenario-1/edit/node-types");
  });

  it("routes unattributed cross-object validation to the validation owner without exposing raw detail", () => {
    const [issue] = initializationPreviewIssuePresentations(previewError([
      { type: "value_error", loc: [], msg: "Value error, unsupported internal validator detail" },
    ]), currentNode, document, "scenario-1");

    expect(issue.category).toBe("unresolved");
    expect(issue.actionLabel).toBe("前往配置检查");
    expect(issue.href).toBe("/scenarios/scenario-1/edit/validation");
    expect(issue.reason).not.toContain("unsupported internal validator detail");
  });
});
