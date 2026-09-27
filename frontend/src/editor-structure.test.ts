import { describe, expect, it } from "vitest";

import { sectionStructure, sectionStructureRegistry, type SectionMode } from "./editor-structure";
import { entityRegistry, rootFieldRegistry } from "./editor-registry";
import { editorTaxonomyGroups, sectionLabels } from "./ui";

const expectedModes: Record<SectionMode, string[]> = {
  SINGLETON: ["overview"],
  COLLECTION: [
    "node-types",
    "world-entities",
    "relation-types",
    "relations",
    "resources",
    "roles",
    "actors",
    "interactions",
    "actions",
    "rules",
    "derived-states",
    "goal-resolution",
    "planning-instructions",
    "planning-recovery",
    "terminology-references",
  ],
  HYBRID: ["planning"],
  BROWSER: ["world", "initialization"],
  WORKFLOW: ["configuration-check", "validation"],
};

describe("section structure registry", () => {
  it("uses the final five product groups without duplicate responsibility shells", () => {
    expect(editorTaxonomyGroups).toEqual([
      { label: "概览", items: ["overview"] },
      { label: "世界模型", items: ["world", "node-types", "world-entities", "relation-types", "relations", "resources"] },
      { label: "角色与行动", items: ["roles", "actors", "interactions", "actions", "rules"] },
      { label: "目标与规划", items: ["derived-states", "goal-resolution", "planning-instructions", "terminology-references"] },
      { label: "初始化与发布", items: ["initialization", "configuration-check", "validation"] },
    ]);
  });

  it("freezes the authoring and legacy routes into the five product modes", () => {
    expect(Object.keys(sectionStructureRegistry)).toHaveLength(21);
    for (const [mode, sections] of Object.entries(expectedModes)) {
      expect(Object.values(sectionStructureRegistry).filter((item) => item.mode === mode).map((item) => item.section)).toEqual(sections);
    }
  });

  it("derives owners from the existing schema and collection authorities", () => {
    expect(sectionStructure("overview").owners).toEqual({ rootPath: ["metadata"], entityKinds: [], rootCollections: [] });
    expect(sectionStructure("relation-types").owners).toEqual({ rootPath: null, entityKinds: ["relation_type"], rootCollections: [] });
    expect(sectionStructure("relations").owners).toEqual({ rootPath: null, entityKinds: ["relation"], rootCollections: [] });
    expect(sectionStructure("initialization").owners).toEqual({
      rootPath: ["initialization"],
      entityKinds: [],
      rootCollections: ["resource_pools", "region_resource_knowledge"],
    });
    expect(sectionStructure("resources").owners.entityKinds).toEqual(["resource"]);
    expect(sectionStructure("terminology-references").owners.entityKinds).toEqual(["public_reference"]);
    expect(sectionStructure("public-knowledge")).toMatchObject({ section: "resources", mode: "COLLECTION" });
    expect(sectionStructure("public-references")).toMatchObject({ section: "terminology-references", mode: "COLLECTION" });
  });

  it("does not infer shell mode or controls from current collection contents", () => {
    expect(sectionStructure("overview").master).toMatchObject({ visible: false, searchable: false, create: "none" });
    expect(sectionStructure("validation").master).toMatchObject({ visible: false, searchable: false, create: "none" });
    expect(sectionStructure("configuration-check")).toMatchObject({
      mode: "WORKFLOW",
      master: { visible: false, source: "none", create: "none" },
      workspace: { renderer: "configuration-check" },
    });
    expect(sectionStructure("world")).toMatchObject({
      mode: "BROWSER",
      master: { source: "topology", create: "none" },
      workspace: { renderer: "topology" },
    });
    expect(sectionStructure("initialization")).toMatchObject({
      mode: "BROWSER",
      master: { visible: false, source: "none", create: "none" },
      workspace: { renderer: "initialization" },
    });
    expect(sectionStructure("resources")).toMatchObject({ mode: "COLLECTION", master: { source: "entities" } });
  });

  it("declares only reachable Advanced JSON ownership without duplicate root fields", () => {
    expect(sectionStructure("overview").capabilities.advancedJson).toBe("none");
    expect(sectionStructure("initialization").capabilities.advancedJson).toBe("none");
    expect(sectionStructure("actions").capabilities.advancedJson).toBe("nested-only");
    expect(sectionStructure("rules").capabilities.advancedJson).toBe("unknown-variant");
    expect(Object.values(rootFieldRegistry).flat().filter((field) => field.type === "json" || field.advanced)).toEqual([]);
  });

  it("removes initialization-only controls from design registries", () => {
    expect(entityRegistry.node.fields.map((field) => field.path)).not.toEqual(expect.arrayContaining(["initial_access", "initial_visibility"]));
    expect(entityRegistry.actor.fields.map((field) => field.path)).not.toEqual(expect.arrayContaining(["initial_node_key", "command_reachability"]));
    expect(entityRegistry.relation.fields.map((field) => field.path)).not.toContain("initial_visibility");
    expect(entityRegistry.resource.fields.map((field) => field.path)).toEqual(expect.arrayContaining(["initial_value", "minimum"]));
  });

  it("separates configuration checks from validation and publishing", () => {
    const group = editorTaxonomyGroups.find((entry) => entry.label === "初始化与发布");
    expect(group?.items).toEqual(["initialization", "configuration-check", "validation"]);
    expect(sectionLabels["configuration-check"]).toBe("配置检查");
    expect(sectionLabels.validation).toBe("验证与发布");
  });

  it("places Public References under the Goals and Planning taxonomy", () => {
    const group = editorTaxonomyGroups.find((entry) => entry.label === "目标与规划");
    expect(group?.items).toContain("terminology-references");
    expect(editorTaxonomyGroups.some((entry) => String(entry.label) === "公开信息")).toBe(false);
    expect(sectionLabels["terminology-references"]).toBe("术语与引用");
  });
});
