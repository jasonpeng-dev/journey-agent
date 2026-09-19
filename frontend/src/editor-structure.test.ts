import { describe, expect, it } from "vitest";

import { sectionStructure, sectionStructureRegistry, type SectionMode } from "./editor-structure";
import { rootFieldRegistry } from "./editor-registry";

const expectedModes: Record<SectionMode, string[]> = {
  SINGLETON: ["overview", "goal-resolution"],
  COLLECTION: [
    "node-types",
    "world-entities",
    "relations",
    "resources",
    "roles",
    "actors",
    "interactions",
    "actions",
    "rules",
    "objectives",
    "derived-states",
    "public-knowledge",
    "public-references",
  ],
  HYBRID: ["initialization", "planning"],
  BROWSER: ["world"],
  WORKFLOW: ["validation"],
};

describe("section structure registry", () => {
  it("freezes the 19 routes into the five product modes", () => {
    expect(Object.keys(sectionStructureRegistry)).toHaveLength(19);
    for (const [mode, sections] of Object.entries(expectedModes)) {
      expect(Object.values(sectionStructureRegistry).filter((item) => item.mode === mode).map((item) => item.section)).toEqual(sections);
    }
  });

  it("derives owners from the existing schema and collection authorities", () => {
    expect(sectionStructure("overview").owners).toEqual({ rootPath: ["metadata"], entityKinds: [], rootCollections: [] });
    expect(sectionStructure("relations").owners).toEqual({ rootPath: null, entityKinds: ["relation_type", "relation"], rootCollections: [] });
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
    expect(sectionStructure("world")).toMatchObject({
      mode: "BROWSER",
      master: { source: "topology", create: "none" },
      workspace: { renderer: "topology" },
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
});
