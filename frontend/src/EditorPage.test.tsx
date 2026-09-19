import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { EditorPage } from "./pages/EditorPage";

vi.mock("./api", () => ({
  ApiError: class ApiError extends Error {
    status = 500;
    code = "TEST_ERROR";
    details = null;
  },
  api: {
    draft: vi.fn(),
    analyzeWorkingCopyReferences: vi.fn(),
    saveDraft: vi.fn(),
  },
}));

const draft = {
  scenario_id: "scenario-1",
  revision: 1,
  definition_document: {
    metadata: { key: "scenario-1", name: "Test Scenario", locality: { enabled: true, region_node_type_key: "scope", facility_node_type_key: "entity", transport_node_type_key: "link", located_in_relation_type_key: "belongs_to", transport_endpoint_relation_type_key: "connects" } },
    world: {
      key: "scenario-1",
      name: "Test World",
      node_types: [{ key: "scope", name: "Scope" }, { key: "entity", name: "Entity" }, { key: "link", name: "Link" }],
      nodes: [{ key: "north", name: "North", node_type_key: "scope" }, { key: "north_a", name: "North A", node_type_key: "entity" }],
      relations: [{ key: "north_a__belongs_to__north", source_node_key: "north_a", relation_type_key: "belongs_to", target_node_key: "north" }],
      resources: [],
    },
  },
  validation_status: "VALID",
  validation_issues: [],
  content_hash: "hash",
  base_scenario_version_id: null,
  updated_at: "2026-09-15T00:00:00Z",
};

beforeEach(() => {
  vi.mocked(api.draft).mockResolvedValue(draft);
  vi.mocked(api.analyzeWorkingCopyReferences).mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [] });
  vi.mocked(api.saveDraft).mockResolvedValue(draft);
});

function LocationProbe() {
  return <output data-testid="editor-location">{useLocation().pathname}</output>;
}

function renderEditor(initialEntry = "/scenarios/scenario-1/edit/world") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initialEntry]}><Routes><Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} /><Route path="/scenarios/:scenarioId" element={<div>Scenario Detail</div>} /></Routes><LocationProbe /></MemoryRouter></QueryClientProvider>);
}

describe("EditorPage topology interaction contract", () => {
  it("auto-opens the Inspector on topology selection without dirtying the Working Copy", async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByRole("button", { name: "North" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "North" }));

    await waitFor(() => expect(screen.getByText("拓扑检查器")).toBeInTheDocument());
    expect(screen.getByText("范围摘要")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
    expect(screen.getByText("未修改")).toBeInTheDocument();
  });

  it("relocates return to the action bar and reuses the Working Copy dirty guard", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderEditor("/scenarios/scenario-1/edit/overview");
    const returnButton = await screen.findByRole("button", { name: "返回场景详情" });

    expect(screen.queryByText("返回场景")).not.toBeInTheDocument();
    const discard = screen.getByRole("button", { name: "放弃修改" });
    expect(discard.compareDocumentPosition(returnButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("button", { name: "显示检查器" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("Test Scenario"), { target: { value: "Changed Scenario" } });
    fireEvent.click(returnButton);
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1/edit/overview");
    expect(screen.getByDisplayValue("Changed Scenario")).toBeVisible();

    confirm.mockReturnValue(true);
    fireEvent.click(returnButton);
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1"));
  });

  it("returns directly to Scenario Detail when the Working Copy is clean", async () => {
    const confirm = vi.spyOn(window, "confirm");
    renderEditor("/scenarios/scenario-1/edit/overview");
    fireEvent.click(await screen.findByRole("button", { name: "返回场景详情" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1"));
    expect(confirm).not.toHaveBeenCalled();
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
