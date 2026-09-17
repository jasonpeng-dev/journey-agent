import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
    metadata: {
      key: "scenario-1",
      name: "测试场景",
      locality: {
        enabled: true,
        region_node_type_key: "scope",
        facility_node_type_key: "entity",
        transport_node_type_key: "link",
        located_in_relation_type_key: "belongs_to",
        transport_endpoint_relation_type_key: "connects",
      },
    },
    world: {
      key: "scenario-1",
      name: "测试世界",
      node_types: [{ key: "scope", name: "范围" }, { key: "entity", name: "实体" }, { key: "link", name: "连接" }],
      nodes: [{ key: "north", name: "北部范围", node_type_key: "scope" }, { key: "north_a", name: "北部实体", node_type_key: "entity" }],
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

afterEach(cleanup);

function renderEditor(section: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/scenarios/scenario-1/edit/${section}`]}><Routes><Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("world topology overview entry", () => {
  it("exposes world overview, renders WorldGraph, and stays clean during selection", async () => {
    renderEditor("overview");

    const overviewLink = await screen.findByRole("link", { name: "世界总览" });
    fireEvent.click(overviewLink);

    expect(await screen.findByRole("region", { name: "分层拓扑浏览器" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "范围总览拓扑" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "北部范围" }));

    await waitFor(() => expect(screen.getByText("拓扑检查器")).toBeInTheDocument());
    expect(screen.getByText("未修改")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps Relations as the relation authoring route", async () => {
    renderEditor("relations");

    await waitFor(() => expect(screen.getByRole("heading", { name: "关系与拓扑" })).toBeInTheDocument());
    expect(screen.queryByRole("region", { name: "分层拓扑浏览器" })).not.toBeInTheDocument();
  });
});
