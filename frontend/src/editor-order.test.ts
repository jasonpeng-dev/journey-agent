import { describe, expect, it } from "vitest";

import { moveItem } from "./editor-order";

describe("authoring order primitive", () => {
  it("swaps only adjacent items and preserves identity values", () => {
    const items = [{ key: "first" }, { key: "second" }, { key: "third" }];
    expect(moveItem(items, 1, "up").map((item) => item.key)).toEqual(["second", "first", "third"]);
    expect(moveItem(items, 1, "down").map((item) => item.key)).toEqual(["first", "third", "second"]);
    expect(moveItem(items, 0, "up")).toEqual(items);
    expect(moveItem(items, 2, "down")).toEqual(items);
  });
});
