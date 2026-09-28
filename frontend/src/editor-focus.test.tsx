import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useSearchParams } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { AuthoringActionButton } from "./components/editor/AuthoringActionButton";
import { NestedCard } from "./components/editor/FormPrimitives";
import type { JsonObject } from "./editor";
import { activateEditorFocusTarget, stableEditorFocusPath, useEditorFocusActivation } from "./editor-focus";

afterEach(cleanup);

const document: JsonObject = {
  world: { nodes: [{ key: "hub", facts: [{ key: "power", name: "Power" }] }] },
  actions: [{
    key: "repair_facility",
    parameters: [{ key: "parts" }],
    expected_outcomes: [{ code: "COMMUNICATIONS_REPAIRED", name: "Restored" }],
  }],
  derived_states: [{ key: "facility_ready", dependencies: [{ kind: "FACT", node_key: "hub", fact_key: "power" }] }],
  initialization: { resource_pools: [{ pool_key: "emergency" }] },
};

describe("shared editor focus activation", () => {
  it("translates indexed rows to stable identities for nested authoring families", () => {
    expect(stableEditorFocusPath("action.repair_facility.expected_outcomes.0.name", document)).toBe("action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED.name");
    expect(stableEditorFocusPath("action.repair_facility.parameters.0.key", document)).toBe("action.repair_facility.parameters.parts.key");
    expect(stableEditorFocusPath("node.hub.facts.0.name", document)).toBe("node.hub.facts.power.name");
    expect(stableEditorFocusPath("derived_state.facility_ready.dependencies.0.minimum", document)).toBe("derived_state.facility_ready.dependencies.FACT:hub:power.minimum");
    expect(stableEditorFocusPath("initialization.resource_pools.0.quantity", document)).toBe("initialization.resource_pools.emergency.quantity");
  });

  it("preserves a positional-only path instead of silently collapsing it to its parent", () => {
    const unstableDocument = { actions: [{ key: "inspect", parameters: [{ name: "legacy" }] }] } as JsonObject;
    expect(stableEditorFocusPath("action.inspect.parameters.0.name", unstableDocument)).toBe("action.inspect.parameters.0.name");
  });

  it("opens a collapsed stable row, focuses its field, and highlights the row", () => {
    const { container } = render(<NestedCard title="Expected outcome" focusPath="action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED" defaultExpanded={false}>
      <label data-field-path="action.repair_facility.expected_outcomes.0.name">Outcome name<input aria-label="Outcome name" /></label>
    </NestedCard>);

    let result: string | undefined;
    act(() => {
      result = activateEditorFocusTarget(container, "action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED.name", document);
    });
    act(() => activateEditorFocusTarget(container, "action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED.name", document));

    expect(result).toBe("expanded");
    expect(screen.getByRole("button", { name: /Expected outcome/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("textbox", { name: "Outcome name" })).toHaveFocus();
    expect(container.querySelector(".is-focus-highlighted")).toBeInTheDocument();
  });

  it("re-activates an exact target when its SPA action is clicked repeatedly on the same URL", async () => {
    const href = "/scenarios/s1/edit/actions/repair_facility?focus_path=expected_outcomes.COMMUNICATIONS_REPAIRED";
    const { container } = render(<MemoryRouter initialEntries={[href]}><Routes>
      <Route path="/scenarios/:scenarioId/edit/:section/:objectKey" element={<>
        <NestedCard title="Expected outcome" focusPath="action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED" defaultExpanded>
          <input aria-label="Outcome name" />
        </NestedCard>
        <AuthoringActionButton to={href}>定位到结果</AuthoringActionButton>
      </>} />
    </Routes></MemoryRouter>);

    fireEvent.click(screen.getByRole("link", { name: "定位到结果" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Outcome name" })).toHaveFocus());
    fireEvent.click(screen.getByRole("link", { name: "定位到结果" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Outcome name" })).toHaveFocus());
    expect(container.querySelector(".nested-editor")).toHaveClass("is-focus-highlighted");
  });

  it("runs focus activation on query-only SPA navigation", async () => {
    function RouteFocus() {
      const location = useLocation();
      const [params] = useSearchParams();
      const path = params.get("focus_path");
      const status = useEditorFocusActivation(path ? `action.repair_facility.${path}` : null, location.search, document);
      return <div data-editor-focus-scope tabIndex={-1}>
        {status === "stale" && <output>定位目标已失效</output>}
        <NestedCard title="Expected outcome" focusPath="action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED" defaultExpanded>
          <label data-field-path="action.repair_facility.expected_outcomes.0.name">Outcome name<input aria-label="Outcome name" /></label>
        </NestedCard>
      </div>;
    }
    render(<MemoryRouter initialEntries={["/actions/repair_facility"]}><Routes>
      <Route path="/actions/repair_facility" element={<><AuthoringActionButton to="?focus_path=expected_outcomes.COMMUNICATIONS_REPAIRED.name">定位到结果</AuthoringActionButton><RouteFocus /></>} />
    </Routes></MemoryRouter>);

    fireEvent.click(screen.getByRole("link", { name: "定位到结果" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Outcome name" })).toHaveFocus());
  });

  it("moves focus to the editor scope when a requested stable target no longer exists", async () => {
    function StaleFocus() {
      const status = useEditorFocusActivation("action.repair_facility.expected_outcomes.DELETED_RESULT.name", "stale", document);
      return <div data-editor-focus-scope tabIndex={-1}>{status === "stale" && <output>定位目标已失效</output>}<div data-focus-path="action.repair_facility.expected_outcomes.COMMUNICATIONS_REPAIRED" /></div>;
    }
    const { container } = render(<StaleFocus />);

    await waitFor(() => expect(screen.getByText("定位目标已失效")).toBeInTheDocument(), { timeout: 1500 });
    expect(container.querySelector("[data-editor-focus-scope]")).toHaveFocus();
  });
});
