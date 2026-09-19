import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { EditorPage } from "./pages/EditorPage";
import { editorSectionTaxonomy } from "./ui";

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
    metadata: { key: "scenario-1", name: "测试场景" },
    world: {
      key: "scenario-1",
      name: "测试世界",
      node_types: [{ key: "region", name: "区域" }],
      nodes: [{ key: "central", name: "中央区", node_type_key: "region" }],
      relations: [],
      resources: [{ key: "relief", name: "救援物资" }],
    },
    actors: {
      roles: [{ key: "coordinator", name: "协调员" }],
      actor_profiles: [{ key: "operator", name: "值班员", role_key: "coordinator" }],
    },
    rules: [{ key: "stabilize", phase: "RESOLVE", trigger: "STATE", action_key: "", priority: 1 }],
    public_references: [{ term: "中央区", ref_type: "REGION", ref_key: "central" }],
  },
  validation_status: "VALID",
  validation_issues: [],
  content_hash: "hash",
  base_scenario_version_id: null,
  updated_at: "2026-09-15T00:00:00Z",
};

function HistoryControls() {
  const navigate = useNavigate();
  return <><button type="button" onClick={() => navigate(-1)}>测试后退</button><button type="button" onClick={() => navigate(1)}>测试前进</button></>;
}

beforeEach(() => {
  vi.mocked(api.draft).mockResolvedValue(draft);
  vi.mocked(api.analyzeWorkingCopyReferences).mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [] });
  vi.mocked(api.saveDraft).mockResolvedValue(draft);
});

afterEach(cleanup);

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/scenarios/scenario-1/edit/public-references"]}>
        <HistoryControls />
        <Routes>
          <Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} />
          <Route path="/scenarios/:scenarioId/edit/:section/:objectKey" element={<EditorPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function expectSection({ heading, item, kind, count, createButton }: { heading: string; item: string; kind: string; count: string; createButton: string }) {
  expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  expect(screen.getByText(item)).toBeInTheDocument();
  expect(screen.getByText(new RegExp(`${kind} ·`))).toBeInTheDocument();
  expect(document.querySelector(".object-panel-header .object-count")).toHaveTextContent(count);
  expect(screen.getByRole("button", { name: new RegExp(createButton) })).toBeInTheDocument();
  expect(screen.getByText("未修改")).toBeInTheDocument();
}

describe("Editor section state ownership", () => {
  it("uses the central taxonomy for the two-level editor heading without helper copy", async () => {
    expect(editorSectionTaxonomy).toMatchObject({
      overview: { category: "场景基础", label: "概览" },
      "node-types": { category: "世界模型", label: "节点类型" },
      "world-entities": { category: "世界模型", label: "世界实体" },
      relations: { category: "世界模型", label: "关系" },
      resources: { category: "世界模型", label: "资源定义" },
      roles: { category: "参与者与交互", label: "角色" },
      actors: { category: "参与者与交互", label: "参与者" },
      interactions: { category: "参与者与交互", label: "交互" },
      actions: { category: "行动系统", label: "行动" },
      rules: { category: "行动系统", label: "规则" },
      objectives: { category: "目标系统", label: "目标" },
      "derived-states": { category: "目标系统", label: "派生状态" },
      "public-knowledge": { category: "公开信息", label: "公共知识" },
      "public-references": { category: "公开信息", label: "公共引用" },
    });

    renderEditor();
    const heading = await screen.findByTestId("editor-taxonomy-heading");
    expect(heading).toHaveTextContent("公开信息/公共引用");
    expect(screen.queryByText("Working copy")).not.toBeInTheDocument();
    expect(document.querySelector(".editor-toolbar-subtitle")).not.toBeInTheDocument();
    expect(screen.queryByText("编辑 公共引用 配置")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "资源定义" }));
    await waitFor(() => expect(screen.getByTestId("editor-taxonomy-heading")).toHaveTextContent("世界模型/资源定义"));
  });

  it("derives master list, selection, and create affordance from every active section", async () => {
    renderEditor();

    await waitFor(() => expect(screen.getByRole("heading", { name: "公共引用" })).toBeInTheDocument());
    expectSection({ heading: "公共引用", item: "中央区", kind: "公共引用", count: "1", createButton: "公共引用" });

    fireEvent.click(screen.getByRole("link", { name: "资源定义" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "资源定义" })).toBeInTheDocument());
    expectSection({ heading: "资源定义", item: "救援物资", kind: "资源", count: "1", createButton: "资源" });
    expect(screen.queryByRole("link", { name: "中央区" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "规则" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "规则" })).toBeInTheDocument());
    expectSection({ heading: "规则", item: "stabilize", kind: "规则", count: "1", createButton: "规则" });

    fireEvent.click(screen.getByRole("link", { name: "参与者" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "参与者" })).toBeInTheDocument());
    expectSection({ heading: "参与者", item: "值班员", kind: "参与者", count: "1", createButton: "参与者" });
    expect(screen.getByRole("button", { name: /参与者/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /角色/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "公共引用" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "公共引用" })).toBeInTheDocument());
    expectSection({ heading: "公共引用", item: "中央区", kind: "公共引用", count: "1", createButton: "公共引用" });
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps browser history navigation section-scoped and clean", async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByRole("heading", { name: "公共引用" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("link", { name: "资源定义" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "资源定义" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("link", { name: "规则" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "规则" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "测试后退" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "资源定义" })).toBeInTheDocument());
    expect(screen.getByText("救援物资")).toBeInTheDocument();
    expect(screen.getByText("未修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "测试前进" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "规则" })).toBeInTheDocument());
    expect(screen.getByText("stabilize")).toBeInTheDocument();
    expect(screen.getByText("未修改")).toBeInTheDocument();
  });
});
