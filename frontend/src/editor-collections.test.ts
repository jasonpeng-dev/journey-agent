import { describe, expect, it } from "vitest";

import {
  appendRootCollectionItem,
  moveRootCollectionItem,
  removeRootCollectionItem,
  replaceRootCollectionItem,
  rootCollectionIdentity,
  rootCollectionIdentityLabel,
  rootCollectionItem,
  rootCollectionSelectionForPath,
  type RootCollectionKey,
} from "./editor-collections";
import type { JsonObject } from "./editor";

const identityCases: Array<[RootCollectionKey, JsonObject, string]> = [
  ["recovery_hints", { failure_code: "BLOCKED" }, JSON.stringify(["BLOCKED"])],
  ["resource_initial_states", { resource_key: "water", scope_node_key: "north" }, JSON.stringify(["water", "north"])],
  ["resource_pools", { pool_key: "north_water" }, JSON.stringify(["north_water"])],
  ["region_resource_knowledge", { region_key: "north" }, JSON.stringify(["north"])],
];

describe("root collection durable identity", () => {
  it.each(identityCases)("derives the canonical %s identity", (collection, value, identity) => {
    expect(rootCollectionIdentity(collection, value)).toBe(identity);
  });

  it("presents semantic collection identities without exposing canonical JSON", () => {
    expect(rootCollectionIdentityLabel("resource_initial_states", { resource_key: "water", scope_node_key: "north" }))
      .toBe("资源 · water / 作用域 · north");
    expect(rootCollectionIdentityLabel("resource_initial_states", { resource_key: "water", scope_node_key: null }))
      .toBe("资源 · water / 作用域 · 全局");
    expect(rootCollectionIdentityLabel("resource_pools", { pool_key: "north_water" })).toBe("资源池 · north_water");
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

  it("moves an author-visible root item without changing its durable identity", () => {
    const selection = { owner: "collection" as const, collection: "recovery_hints" as const, identity: JSON.stringify(["SECOND"]) };
    const moved = moveRootCollectionItem({ recovery_hints: [{ failure_code: "FIRST" }, { failure_code: "SECOND" }] }, selection, "up");
    expect(moved).toMatchObject({ ok: true, selection });
    if (!moved.ok) return;
    expect(moved.root.recovery_hints).toEqual([{ failure_code: "SECOND" }, { failure_code: "FIRST" }]);
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
    expect(replaceRootCollectionItem(root, selection, { failure_code: "RENAMED", hint: "two" })).toMatchObject({ ok: false });
    expect(replaceRootCollectionItem(root, selection, { failure_code: "SECOND", hint: "updated" })).toMatchObject({
      ok: true,
      selection: { identity: JSON.stringify(["SECOND"]) },
      root: { recovery_hints: [{ failure_code: "FIRST", hint: "one" }, { failure_code: "SECOND", hint: "updated" }] },
    });
  });

  it.each(identityCases)("creates a uniquely addressable %s item", (collection, firstItem, firstIdentity) => {
    const suffix = "second";
    const secondItem: JsonObject = collection === "recovery_hints"
      ? { failure_code: "SECOND", hint: "Second" }
      : collection === "resource_initial_states"
        ? { resource_key: "oil", scope_node_key: null, value: 0, reserved_value: 0 }
        : collection === "resource_pools"
          ? { pool_key: `${firstItem.pool_key}_${suffix}`, resource_key: "oil" }
          : { region_key: "south", resource_inventory_visibility: "VISIBLE", resource_survey_completed: false };
    const first = appendRootCollectionItem({}, collection, firstItem);
    expect(first.ok).toBe(true);
    if (!first.ok || !first.selection) return;
    expect(first.selection.identity).toBe(firstIdentity);
    const second = appendRootCollectionItem(first.root, collection, secondItem);
    expect(second.ok).toBe(true);
    if (!second.ok || !second.selection) return;
    expect(second.selection.identity).not.toBe(first.selection.identity);
    expect(rootCollectionItem(second.root, second.selection)).not.toBeNull();
  });

  it.each(identityCases)("rejects an incomplete %s identity at creation", (collection) => {
    expect(appendRootCollectionItem({}, collection, {})).toMatchObject({ ok: false });
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
