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
    initialization: {
      start_node_key: "central",
      primary_actor_key: "operator",
      resource_initial_states: [{ resource_key: "relief", scope_node_key: "central", value: 10, reserved_value: 0 }],
      resource_pools: [{ pool_key: "central_pool", resource_key: "relief", region_key: "central", quantity: 10, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false }],
      region_resource_knowledge: [{ region_key: "central", resource_inventory_visibility: "VISIBLE", resource_survey_completed: true }],
    },
    world: {
      key: "scenario-1",
      name: "测试世界",
      node_types: [{ key: "region", name: "区域" }],
      nodes: [{ key: "central", name: "中央区", node_type_key: "region" }],
      relation_types: [{ key: "located_in", name: "位于" }],
      relations: [],
      resources: [{ key: "relief", name: "救援物资" }],
    },
    actors: {
      roles: [{ key: "coordinator", name: "协调员" }],
      actor_profiles: [{ key: "operator", name: "值班员", role_key: "coordinator" }],
    },
    rules: [{ key: "stabilize", phase: "RESOLVE", trigger: "STATE", action_key: "", priority: 1 }],
    planning: { instructions: ["优先保障生命安全"], recovery_hints: [{ failure_code: "BLOCKED", hint: "重新检查道路" }] },
    public_knowledge: { resource_source_hints: [{ resource_key: "relief", primary_region_key: "central", candidate_region_keys: [] }] },
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

function renderEditor(initialEntry = "/scenarios/scenario-1/edit/public-references") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
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

  it("uses singleton and workflow shells without empty object navigation or technical titles", async () => {
    renderEditor("/scenarios/scenario-1/edit/overview");
    await waitFor(() => expect(screen.getByRole("heading", { name: "概览" })).toBeInTheDocument());
    expect(document.querySelector(".object-panel")).not.toBeInTheDocument();
    expect(screen.queryByText("Typed 编辑器")).not.toBeInTheDocument();
    expect(screen.queryByText("结构化 Scenario authoring")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("搜索")).not.toBeInTheDocument();

    cleanup();
    renderEditor("/scenarios/scenario-1/edit/validation");
    await waitFor(() => expect(screen.getByRole("heading", { name: "验证与发布" })).toBeInTheDocument());
    expect(document.querySelector(".object-panel")).not.toBeInTheDocument();
    expect(screen.queryByText("OBJECTS")).not.toBeInTheDocument();
    expect(screen.queryByText("Typed 编辑器")).not.toBeInTheDocument();
  });

  it("uses one active owner for hybrid sections and keeps selection clean", async () => {
    renderEditor("/scenarios/scenario-1/edit/initialization");
    await waitFor(() => expect(screen.getByRole("heading", { name: "初始化入口", level: 3 })).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "基础配置", level: 4 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "资源初始状态", level: 4 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "资源池", level: 4 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "区域资源知识", level: 4 })).toBeInTheDocument();
    expect(screen.getByLabelText("起始节点")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /central_pool/ }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "central_pool", level: 3 })).toBeInTheDocument());
    expect(screen.queryByLabelText("起始节点")).not.toBeInTheDocument();
    expect(screen.getByText("未修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "规划" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "规划指引", level: 3 })).toBeInTheDocument());
    expect(screen.getByDisplayValue("优先保障生命安全")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /BLOCKED/ }));
    await waitFor(() => expect(screen.getByDisplayValue("重新检查道路")).toBeInTheDocument());
    expect(screen.queryByDisplayValue("优先保障生命安全")).not.toBeInTheDocument();
    expect(screen.getByText("未修改")).toBeInTheDocument();
  });

  it("keeps public knowledge collection-only and groups relation kinds", async () => {
    renderEditor("/scenarios/scenario-1/edit/public-knowledge");
    await waitFor(() => expect(screen.getByRole("heading", { name: "公共知识" })).toBeInTheDocument());
    expect(screen.getByText("资源发现知识")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "公共知识配置" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "关系" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "关系" })).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "关系类型" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "关系实例" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "＋ 新增关系类型" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "＋ 新增关系实例" })).toBeInTheDocument();
  });
});
