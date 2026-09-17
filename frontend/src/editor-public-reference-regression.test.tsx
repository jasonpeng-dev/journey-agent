import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    metadata: { key: "scenario-1", name: "Test Scenario" },
    world: {
      key: "scenario-1",
      name: "Test World",
      node_types: [{ key: "region", name: "Region" }],
      nodes: [
        { key: "central_district", name: "Central District", node_type_key: "region" },
        { key: "east_district", name: "East District", node_type_key: "region" },
      ],
      relations: [],
      resources: [{ key: "water", name: "Water" }],
    },
    actors: {
      roles: [{ key: "coordinator", name: "Coordinator" }],
      actor_profiles: [{ key: "operator", name: "Operator", role_key: "coordinator" }],
    },
    rules: [{ key: "stabilize", phase: "RESOLVE", trigger: "STATE", action_key: "", priority: 1 }],
    public_references: [
      { term: "Central District", ref_type: "REGION", ref_key: "central_district" },
      { term: "Central Area", ref_type: "REGION", ref_key: "central_district" },
    ],
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

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/scenarios/scenario-1/edit/public-references"]}>
        <Routes>
          <Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} />
          <Route path="/scenarios/:scenarioId/edit/:section/:objectKey" element={<EditorPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Editor public-reference section isolation", () => {
  it("does not retain public references after navigating to world entities", async () => {
    renderEditor();

    await waitFor(() => expect(screen.getByRole("heading", { name: "公共引用" })).toBeInTheDocument());
    expect(screen.getAllByText(/公共引用/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("link", { name: "世界实体" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "世界实体" })).toBeInTheDocument());
    expect(screen.getByText("节点 · central_district")).toBeInTheDocument();
    expect(screen.queryByText("公共引用 · central_district")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "资源定义" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "资源定义" })).toBeInTheDocument());
    expect(screen.getByText("资源 · water")).toBeInTheDocument();
    expect(screen.queryByText(/公共引用 ·/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "规则" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "规则" })).toBeInTheDocument());
    expect(screen.getByText("规则 · stabilize")).toBeInTheDocument();
    expect(screen.queryByText("资源 · water")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "参与者" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "参与者" })).toBeInTheDocument());
    expect(screen.getByText("参与者 · operator")).toBeInTheDocument();
    expect(screen.queryByText("规则 · stabilize")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "公共引用" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "公共引用" })).toBeInTheDocument());
    expect(screen.getAllByText(/公共引用 · REGION:/)).toHaveLength(2);
    expect(screen.queryByText("参与者 · operator")).not.toBeInTheDocument();
  });
});
