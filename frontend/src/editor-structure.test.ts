import { describe, expect, it } from "vitest";

import { sectionStructure, sectionStructureRegistry, type SectionMode } from "./editor-structure";
import { entityRegistry, rootFieldRegistry } from "./editor-registry";
import { editorTaxonomyGroups, sectionLabels } from "./ui";

const expectedModes: Record<SectionMode, string[]> = {
  SINGLETON: ["overview", "goal-resolution"],
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
    "planning-instructions",
    "planning-recovery",
    "public-knowledge",
    "public-references",
  ],
  HYBRID: ["planning"],
  BROWSER: ["world", "initialization"],
  WORKFLOW: ["configuration-check", "validation"],
};

describe("section structure registry", () => {
  it("freezes the authoring and legacy routes into the five product modes", () => {
    expect(Object.keys(sectionStructureRegistry)).toHaveLength(22);
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
      rootCollections: ["resource_initial_states", "resource_pools", "region_resource_knowledge"],
    });
    expect(sectionStructure("public-knowledge").owners.rootCollections).toEqual(["resource_source_hints"]);
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
    expect(sectionStructure("public-knowledge")).toMatchObject({
      mode: "COLLECTION",
      master: { source: "root-collections" },
    });
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
    expect(entityRegistry.resource.fields.map((field) => field.path)).not.toContain("initial_value");
  });

  it("separates configuration checks from validation and publishing", () => {
    const group = editorTaxonomyGroups.find((entry) => entry.label === "检查与发布");
    expect(group?.items).toEqual(["configuration-check", "validation"]);
    expect(sectionLabels["configuration-check"]).toBe("配置检查");
    expect(sectionLabels.validation).toBe("验证与发布");
  });
});
