import { describe, expect, it } from "vitest";

import {
  appendRootCollectionItem,
  removeRootCollectionItem,
  replaceRootCollectionItem,
  rootCollectionIdentity,
  rootCollectionItem,
  rootCollectionSelectionForPath,
  type RootCollectionKey,
} from "./editor-collections";
import type { JsonObject } from "./editor";

const identityCases: Array<[RootCollectionKey, JsonObject, string]> = [
  ["resource_source_hints", { resource_key: "water" }, JSON.stringify(["water"])],
  ["recovery_hints", { failure_code: "BLOCKED" }, JSON.stringify(["BLOCKED"])],
  ["resource_initial_states", { resource_key: "water", scope_node_key: "north" }, JSON.stringify(["water", "north"])],
  ["resource_pools", { pool_key: "north_water" }, JSON.stringify(["north_water"])],
  ["region_resource_knowledge", { region_key: "north" }, JSON.stringify(["north"])],
];

describe("root collection durable identity", () => {
  it.each(identityCases)("derives the canonical %s identity", (collection, value, identity) => {
    expect(rootCollectionIdentity(collection, value)).toBe(identity);
  });

  it("keeps selection on the same item after reorder and removal before it", () => {
    const selection = { owner: "collection" as const, collection: "recovery_hints" as const, identity: JSON.stringify(["SECOND"]) };
    const first = { failure_code: "FIRST", hint: "one" };
    const second = { failure_code: "SECOND", hint: "two" };
    const reordered = { recovery_hints: [second, first] };

    expect(rootCollectionItem(reordered, selection)).toEqual({ index: 0, value: second });

    const precedingSelection = { ...selection, identity: JSON.stringify(["FIRST"]) };
    const removed = removeRootCollectionItem({ recovery_hints: [first, second] }, precedingSelection);
    expect(removed.ok).toBe(true);
    if (!removed.ok) return;
    expect(rootCollectionItem(removed.root, selection)?.value).toEqual(second);
  });

  it("clears selection when the selected item is deleted", () => {
    const selection = { owner: "collection" as const, collection: "recovery_hints" as const, identity: JSON.stringify(["BLOCKED"]) };
    const removed = removeRootCollectionItem({ recovery_hints: [{ failure_code: "BLOCKED", hint: "retry" }] }, selection);
    expect(removed).toMatchObject({ ok: true, selection: null, root: { recovery_hints: [] } });
  });

  it("rejects duplicate identity mutations and follows a valid identity mutation", () => {
    const root = { recovery_hints: [{ failure_code: "FIRST", hint: "one" }, { failure_code: "SECOND", hint: "two" }] };
    const selection = { owner: "collection" as const, collection: "recovery_hints" as const, identity: JSON.stringify(["SECOND"]) };

    expect(replaceRootCollectionItem(root, selection, { failure_code: "FIRST", hint: "duplicate" })).toMatchObject({ ok: false });
    expect(replaceRootCollectionItem(root, selection, { failure_code: "RENAMED", hint: "two" })).toMatchObject({
      ok: true,
      selection: { identity: JSON.stringify(["RENAMED"]) },
    });
  });

  it.each(identityCases)("creates a uniquely addressable %s item", (collection) => {
    const first = appendRootCollectionItem({}, collection);
    expect(first.ok).toBe(true);
    if (!first.ok || !first.selection) return;
    const second = appendRootCollectionItem(first.root, collection);
    expect(second.ok).toBe(true);
    if (!second.ok || !second.selection) return;
    expect(second.selection.identity).not.toBe(first.selection.identity);
    expect(rootCollectionItem(second.root, second.selection)).not.toBeNull();
  });

  it("maps a validation array path to durable item identity", () => {
    const root = { resource_pools: [{ pool_key: "first" }, { pool_key: "target" }] };
    expect(rootCollectionSelectionForPath(root, "resource_pools.1.quantity")).toEqual({
      owner: "collection",
      collection: "resource_pools",
      identity: JSON.stringify(["target"]),
    });
  });
});
