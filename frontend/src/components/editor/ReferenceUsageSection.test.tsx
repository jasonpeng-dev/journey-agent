import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { JsonObject } from "../../editor";
import type { Locator, ReferenceEdge } from "../../types";
import { ReferenceUsageSection } from "./ReferenceUsageSection";

const document: JsonObject = {
  world: {
    nodes: [
      { key: "north", name: "北区医院", node_type_key: "facility" },
      { key: "south", name: "南区医院", node_type_key: "facility" },
    ],
    relation_types: [{ key: "connects", name: "相连" }],
    relations: [{ source_node_key: "north", relation_type_key: "connects", target_node_key: "south" }],
  },
  interactions: [{ key: "inspect", name: "检查" }],
  actions: [{ key: "repair", name: "修复" }],
  actors: { actor_profiles: [{ key: "operator", name: "操作员" }] },
};

function edge(source: Locator, target: Locator): ReferenceEdge {
  return { source, target };
}

function renderUsage(target: Locator, references: ReferenceEdge[]) {
  return render(
    <MemoryRouter>
      <ReferenceUsageSection references={references} target={target} document={document} scenarioId="scenario-1" />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("ReferenceUsageSection", () => {
  it("omits the usage block when the typed index has no consumers", () => {
    renderUsage({ object_kind: "node", object_key: "north", field_path: null }, []);
    expect(screen.queryByRole("region", { name: "使用情况" })).not.toBeInTheDocument();
  });

  it("deduplicates Relation endpoint edges and links to the exact Relation locator", () => {
    const relationKey = "north__connects__south";
    renderUsage(
      { object_kind: "node", object_key: "north", field_path: null },
      [
        edge({ object_kind: "relation", object_key: relationKey, field_path: "source_node_key" }, { object_kind: "node", object_key: "north", field_path: null }),
        edge({ object_kind: "relation", object_key: relationKey, field_path: "target_node_key" }, { object_kind: "node", object_key: "north", field_path: null }),
      ],
    );

    expect(screen.getByText("被 1 个关系实例使用")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /前往北区医院/ })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/relations/north__connects__south?focus_path=source_node_key&kind=relation",
    );
    expect(screen.queryByText("source_node_key")).not.toBeInTheDocument();
    expect(screen.queryByText("target_node_key")).not.toBeInTheDocument();
    expect(screen.queryByText(/技术详情|Schema path/)).not.toBeInTheDocument();
  });

  it("groups multiple consumers, opens an accessible dialog, and restores keyboard focus on Escape", async () => {
    const target = { object_kind: "interaction", object_key: "inspect", field_path: null };
    renderUsage(target, [
      edge({ object_kind: "node", object_key: "north", field_path: "interaction_keys.0" }, target),
      edge({ object_kind: "node", object_key: "north", field_path: "interaction_keys.1" }, target),
      edge({ object_kind: "node", object_key: "south", field_path: "interaction_keys.0" }, target),
      edge({ object_kind: "action", object_key: "repair", field_path: "required_interaction_key" }, target),
    ]);

    const nodeGroup = screen.getByRole("button", { name: "查看 2 个世界实体" });
    expect(nodeGroup).toHaveClass("authoring-action-button");
    expect(nodeGroup).toHaveClass("usage-group-action");
    expect(nodeGroup.parentElement).toHaveClass("usage-group");
    expect(nodeGroup).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(nodeGroup);

    const dialog = await screen.findByRole("dialog", { name: "使用“检查”的世界实体" });
    expect(within(dialog).getByText("北区医院")).toBeInTheDocument();
    expect(within(dialog).getByText("南区医院")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "前往北区医院" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/world-entities/north?focus_path=interaction_keys.0",
    );
    expect(within(dialog).getByRole("link", { name: "前往南区医院" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/world-entities/south?focus_path=interaction_keys.0",
    );
    expect(screen.getByRole("link", { name: "前往修复" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/actions/repair?focus_path=required_interaction_key",
    );

    expect(screen.queryByText("interaction_keys.0")).not.toBeInTheDocument();
    expect(screen.queryByText("interaction_keys.1")).not.toBeInTheDocument();
    expect(screen.queryByText(/技术详情|Schema path/)).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(window.document.activeElement).toBe(nodeGroup));
  });

  it("keeps medium-priority Actor parameter usage collapsed and navigates to its exact policy row", () => {
    const target = { object_kind: "action_parameter", object_key: "repair:count", field_path: "parameters.count" };
    renderUsage(target, [
      edge(
        { object_kind: "actor", object_key: "operator", field_path: "authority_policy.autonomous_limits.0.parameter_key" },
        target,
      ),
    ]);

    const details = window.document.querySelector(".usage-secondary-details") as HTMLDetailsElement;
    expect(details).toBeInTheDocument();
    expect(details.open).toBe(false);
    fireEvent.click(within(details).getByText("其他使用情况"));
    expect(within(details).getByRole("link", { name: "前往操作员" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/actors/operator?focus_path=authority_policy.autonomous_limits.0.parameter_key",
    );
  });

  it("shows Action target-role usage on an Actor as a high-priority consumer", () => {
    const target = { object_kind: "actor", object_key: "operator", field_path: null };
    renderUsage(target, [
      edge({ object_kind: "action", object_key: "repair", field_path: "target_actor_roles.0.target_key" }, target),
    ]);

    expect(screen.getByRole("link", { name: "前往修复" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/actions/repair?focus_path=target_actor_roles.0.target_key",
    );
    expect(window.document.querySelector(".usage-secondary-details")).not.toBeInTheDocument();
  });
});
