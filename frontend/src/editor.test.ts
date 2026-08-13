import { describe, expect, it } from "vitest";

import { sectionObjects, updateObjectName } from "./editor";

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
});
