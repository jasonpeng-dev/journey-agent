import { describe, expect, it } from "vitest";

import { replaceObject, sectionObjects, updateObjectName } from "./editor";
import { addObject, defaultArrayItem } from "./templates";

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
});
