import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorldGraph, type TopologyContext, type TopologySelection } from "./components/WorldGraph";

afterEach(() => {
  document.body.innerHTML = "";
});

const documentValue = {
  metadata: { locality: { enabled: true, region_node_type_key: "scope", facility_node_type_key: "entity", transport_node_type_key: "link", located_in_relation_type_key: "belongs_to", transport_endpoint_relation_type_key: "connects" } },
  world: {
    node_types: [{ key: "scope", name: "Scope" }, { key: "entity", name: "Entity" }, { key: "link", name: "Link" }],
    nodes: [
      { key: "north", name: "North Scope", node_type_key: "scope" },
      { key: "south", name: "South Scope", node_type_key: "scope" },
      { key: "north_a", name: "North A", node_type_key: "entity" },
      { key: "south_a", name: "South A", node_type_key: "entity" },
      { key: "bridge", name: "Link", node_type_key: "link" },
    ],
    relations: [
      { key: "north_a__belongs_to__north", source_node_key: "north_a", relation_type_key: "belongs_to", target_node_key: "north" },
      { key: "south_a__belongs_to__south", source_node_key: "south_a", relation_type_key: "belongs_to", target_node_key: "south" },
      { key: "bridge__connects__north", source_node_key: "bridge", relation_type_key: "connects", target_node_key: "north" },
      { key: "bridge__connects__south", source_node_key: "bridge", relation_type_key: "connects", target_node_key: "south" },
    ],
  },
};

function Harness({ context, selection, onContextChange, onSelectionChange, onOpenEditor = vi.fn() }: { context: TopologyContext; selection: TopologySelection; onContextChange: (value: TopologyContext) => void; onSelectionChange: (value: TopologySelection) => void; onOpenEditor?: (key: string) => void }) {
  return <WorldGraph document={documentValue} context={context} selection={selection} onContextChange={onContextChange} onSelectionChange={onSelectionChange} onOpenEditor={onOpenEditor} />;
}

describe("WorldGraph topology navigation", () => {
  it("selects and enters a scope, then navigates through a boundary portal", () => {
    const onContextChange = vi.fn();
    const onSelectionChange = vi.fn();
    const { rerender } = render(<Harness context={{ kind: "overview" }} selection={null} onContextChange={onContextChange} onSelectionChange={onSelectionChange} />);

    fireEvent.click(screen.getByRole("button", { name: "North Scope" }));
    expect(onSelectionChange).toHaveBeenCalledWith({ kind: "scope", key: "north" });
    fireEvent.doubleClick(screen.getByRole("button", { name: "North Scope" }));
    expect(onContextChange).toHaveBeenCalledWith({ kind: "scope", scopeKey: "north" });

    rerender(<Harness context={{ kind: "scope", scopeKey: "north" }} selection={{ kind: "scope", key: "north" }} onContextChange={onContextChange} onSelectionChange={onSelectionChange} />);
    onContextChange.mockClear();
    onSelectionChange.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "前往South Scope" }));
    expect(onContextChange).not.toHaveBeenCalled();
    expect(onSelectionChange).toHaveBeenCalledWith({ kind: "portal", key: "north__south", scopeKey: "north", neighborScopeKey: "south" });

    fireEvent.doubleClick(screen.getByRole("button", { name: "前往South Scope" }));
    expect(onContextChange).toHaveBeenCalledWith({ kind: "scope", scopeKey: "south" });
  });

  it("renders relation filters from the current scope projection and opens the editor on entity double click", () => {
    const onContextChange = vi.fn();
    const onSelectionChange = vi.fn();
    const onOpenEditor = vi.fn();
    const { rerender } = render(<Harness context={{ kind: "scope", scopeKey: "north" }} selection={null} onContextChange={onContextChange} onSelectionChange={onSelectionChange} onOpenEditor={onOpenEditor} />);

    expect(screen.getByRole("button", { name: "connects" })).toBeInTheDocument();
    expect(screen.queryByText("BOUNDARY PORTAL")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "North A" }));
    expect(onSelectionChange).toHaveBeenCalledWith({ kind: "node", key: "north_a" });

    rerender(<Harness context={{ kind: "scope", scopeKey: "north" }} selection={{ kind: "node", key: "north_a" }} onContextChange={onContextChange} onSelectionChange={onSelectionChange} onOpenEditor={onOpenEditor} />);
    fireEvent.doubleClick(screen.getByRole("button", { name: "North A" }));
    expect(onOpenEditor).toHaveBeenCalledWith("north_a");
  });
});
