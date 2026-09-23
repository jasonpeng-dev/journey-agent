import { fireEvent, render, screen, waitFor, cleanup, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./api";
import { EditorPage } from "./pages/EditorPage";
import type { InitializationPreview } from "./types";
import { editorSectionTaxonomy } from "./ui";

vi.mock("./api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    code: string;
    details: unknown;
    constructor(message = "test", status = 500, code = "TEST_ERROR", details: unknown = null) {
      super(message);
      this.status = status;
      this.code = code;
      this.details = details;
    }
  },
  api: {
    draft: vi.fn(),
    analyzeWorkingCopyReferences: vi.fn(),
    transformWorkingCopy: vi.fn(),
    saveDraft: vi.fn(),
    validateDraft: vi.fn(),
    initializationPreview: vi.fn(),
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
      relations: [{ key: "located_in", source_node_key: "central", relation_type_key: "located_in", target_node_key: "central", initial_visibility: "VISIBLE" }],
      resources: [{ key: "relief", name: "救援物资" }],
    },
    actors: {
      roles: [{ key: "coordinator", name: "协调员" }],
      actor_profiles: [{ key: "operator", name: "值班员", role_key: "coordinator" }],
    },
    rules: [{ key: "stabilize", phase: "RESOLVE", trigger: "STATE", action_key: "", priority: 1 }],
    planning: { instructions: ["优先保障生命安全", "保留替代方案"], recovery_hints: [{ failure_code: "BLOCKED", hint: "重新检查道路" }] },
    public_knowledge: { resource_source_hints: [{ resource_key: "relief", primary_region_key: "central", candidate_region_keys: [] }] },
    public_references: [{ term: "中央区", ref_type: "REGION", ref_key: "central" }],
  },
  validation_status: "VALID",
  validation_issues: [],
  content_hash: "hash",
  base_scenario_version_id: null,
  updated_at: "2026-09-15T00:00:00Z",
};

const initializationPreview: InitializationPreview = {
  revision: 1,
  parity: { published: false, initialization_changes: [], design_changes: [] },
  projection: {
    summary: { nodes: 1, actors: 1, resource_pools: 2, relations: 1, derived_states: 0, warnings: 1 },
    findings: [
      { identity: "pool:central_pool:relief:central", label: "Resource pool · central_pool", canonical_path: "initialization.resource_pools[]", owner: "INITIALIZATION_ONLY", source: "EXPLICIT", severity: "INFO", value: { quantity: 10 }, locator: { section: "initialization", object_kind: "resource_pool", object_key: "central_pool", field_path: "initialization.resource_pools" }, message: "" },
    ],
    domains: [
      { id: "basic", label: "Basic configuration", groups: [{ id: "entry", label: "Bootstrap entry", items: [{ id: "bootstrap-entry", label: "Start node & primary actor", locator: { section: "initialization", object_kind: null, object_key: null, field_path: null }, field_ids: [], readonly: false, context: {} }] }] },
      { id: "nodes", label: "Nodes", groups: [{ id: "node-type:region", label: "区域", items: [{ id: "node:central", label: "中央区", locator: { section: "world-entities", object_kind: "node", object_key: "central", field_path: null }, field_ids: [], readonly: false, context: {} }] }] },
      { id: "actors", label: "Actors", groups: [] },
      { id: "resources", label: "Resources", groups: [{ id: "resource-pools", label: "Resource pools", items: [{ id: "pool:central_pool:relief:central", label: "Resource pool · central_pool", locator: { section: "initialization", object_kind: "resource_pool", object_key: "central_pool", field_path: "initialization.resource_pools" }, field_ids: ["pool:central_pool:relief:central"], readonly: false, context: {} }] }, { id: "region-resource-knowledge", label: "Region resource knowledge", items: [] }, { id: "compatibility-resources", label: "Compatibility sources", items: [] }] },
      { id: "relations", label: "Relations", groups: [] },
      { id: "derived", label: "Derived state preview", groups: [] },
    ],
  },
};

function HistoryControls() {
  const navigate = useNavigate();
  return <><button type="button" onClick={() => navigate(-1)}>测试后退</button><button type="button" onClick={() => navigate(1)}>测试前进</button></>;
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="editor-location">{location.pathname}{location.search}</output>;
}

beforeEach(() => {
  vi.mocked(api.draft).mockResolvedValue(draft);
  vi.mocked(api.analyzeWorkingCopyReferences).mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [] });
  vi.mocked(api.transformWorkingCopy).mockImplementation(async (_id, _revision, document) => ({ scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [], definition_document: structuredClone(document) }));
  vi.mocked(api.saveDraft).mockResolvedValue(draft);
  vi.mocked(api.validateDraft).mockResolvedValue({ scenario_id: "scenario-1", revision: 1, content_hash: null, publish_ready: false, issues: [], readiness: [] });
  vi.mocked(api.initializationPreview).mockResolvedValue(initializationPreview);
});

afterEach(cleanup);

function renderEditor(initialEntry = "/scenarios/scenario-1/edit/public-references") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <HistoryControls />
        <LocationProbe />
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
  expect(screen.getByText(new RegExp(`${kind} ·`), { selector: "code" })).toBeInTheDocument();
  expect(document.querySelector(".object-panel-header .object-count")).toHaveTextContent(count);
  expect(screen.getByRole("button", { name: new RegExp(createButton) })).toBeInTheDocument();
  expect(screen.getByText("未修改")).toBeInTheDocument();
}

describe("Editor section state ownership", () => {
  it("uses the central taxonomy for the two-level editor heading without helper copy", async () => {
    expect(editorSectionTaxonomy).toMatchObject({
      overview: { category: "概览", label: "概览" },
      "node-types": { category: "世界模型", label: "节点类型" },
      "world-entities": { category: "世界模型", label: "世界实体" },
      "relation-types": { category: "世界模型", label: "关系类型" },
      relations: { category: "世界模型", label: "关系实例" },
      resources: { category: "世界模型", label: "资源定义" },
      roles: { category: "参与者", label: "角色" },
      actors: { category: "参与者", label: "参与者" },
      interactions: { category: "行为系统", label: "交互" },
      actions: { category: "行为系统", label: "行动" },
      rules: { category: "行为系统", label: "规则" },
      "derived-states": { category: "目标系统", label: "派生状态" },
      "goal-resolution": { category: "目标系统", label: "目标解析" },
      "planning-instructions": { category: "规划策略", label: "全局规划指引" },
      "planning-recovery": { category: "规划策略", label: "失败恢复策略" },
      "public-knowledge": { category: "公开信息", label: "资源来源提示" },
      "public-references": { category: "公开信息", label: "公共引用" },
    });
    expect(editorSectionTaxonomy).not.toHaveProperty("objectives");

    renderEditor();
    const heading = await screen.findByTestId("editor-taxonomy-heading");
    expect(screen.queryByRole("link", { name: "目标" })).not.toBeInTheDocument();
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
    expectSection({ heading: "规则", item: "状态规则 · 状态处理", kind: "规则", count: "1", createButton: "规则" });

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
    expect(screen.getByText("状态规则 · 状态处理")).toBeInTheDocument();
    expect(screen.getByText("规则 · stabilize")).toBeInTheDocument();
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
    expect(screen.getByRole("heading", { name: "草稿检查与发布" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "运行准备度" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "问题" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "预览/测试当前草稿" })).toBeInTheDocument();
  });

  it("uses the initialization hierarchy and keeps navigation clean", async () => {
    renderEditor("/scenarios/scenario-1/edit/initialization");
    await waitFor(() => expect(screen.getByRole("heading", { name: "初始化", level: 3 })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: /资源/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /资源/ }));
    fireEvent.click(screen.getByRole("button", { name: /资源池/ }));
    fireEvent.click(screen.getByRole("button", { name: /central_pool/ }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "中央区 · 救援物资", level: 4 })).toBeInTheDocument());
    expect(screen.getByLabelText(/数量/)).toBeInTheDocument();
    expect(screen.getByText("未修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "全局规划指引" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/planning-instructions"));
    expect(screen.getByTestId("editor-taxonomy-heading")).toHaveTextContent("规划策略");
    const instructionMaster = document.querySelector<HTMLElement>(".object-panel")!;
    expect(within(instructionMaster).getByText("全局规划指引")).toBeInTheDocument();
    expect(within(instructionMaster).getByText("2", { selector: ".object-count" })).toBeInTheDocument();
    expect(within(instructionMaster).getByRole("button", { name: "＋ 规划指引" })).toBeInTheDocument();
    expect(instructionMaster.querySelector(".collection-list-group")).toBeNull();
    expect(screen.getByText("请选择或新增一条规划指引。")).toBeInTheDocument();
    expect(instructionMaster.querySelector(".collection-list-item.selected")).toBeNull();
    expect(screen.getByRole("button", { name: /规划指引 1 优先保障生命安全/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /规划指引 2 保留替代方案/ }));
    expect(screen.getByRole("heading", { name: "规划指引 2", level: 3 })).toBeInTheDocument();
    expect(screen.getByText("规划指引 · 可编辑对象")).toBeInTheDocument();
    expect(screen.getByDisplayValue("保留替代方案")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("优先保障生命安全")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "↑" }));
    expect(screen.getByRole("heading", { name: "规划指引 1", level: 3 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "↓" }));
    expect(screen.getByRole("heading", { name: "规划指引 2", level: 3 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "失败恢复策略" }));
    await waitFor(() => expect(screen.getByText("请选择或新增一条失败恢复策略。")).toBeInTheDocument());
    const recoveryMaster = document.querySelector<HTMLElement>(".object-panel")!;
    expect(within(recoveryMaster).getByRole("button", { name: "＋ 失败恢复策略" })).toBeInTheDocument();
    expect(recoveryMaster.querySelector(".collection-list-group")).toBeNull();
    expect(recoveryMaster.querySelector(".collection-list-item.selected")).toBeNull();
    fireEvent.click(within(recoveryMaster).getByRole("button", { name: /BLOCKED/ }));
    expect(screen.getByRole("heading", { name: "BLOCKED", level: 3 })).toBeInTheDocument();
    expect(screen.getByText("失败恢复策略 · 可编辑对象")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("优先保障生命安全")).not.toBeInTheDocument();
    expect(screen.getByText("未修改")).toBeInTheDocument();
  });

  it("restores a root collection selection from a durable deep link", async () => {
    const identity = encodeURIComponent(JSON.stringify(["BLOCKED"]));
    renderEditor(`/scenarios/scenario-1/edit/planning?owner=collection&collection=recovery_hints&item=${identity}`);

    await waitFor(() => expect(screen.getByRole("heading", { name: "BLOCKED", level: 3 })).toBeInTheDocument());
    expect(screen.getByDisplayValue("重新检查道路")).toBeInTheDocument();
    expect(screen.getByText("未修改")).toBeInTheDocument();
  });

  it("adds, reorders, edits, and deletes planning instructions through the standard detail header", async () => {
    renderEditor("/scenarios/scenario-1/edit/planning-instructions");
    await waitFor(() => expect(screen.getByText("请选择或新增一条规划指引。")).toBeInTheDocument());
    expect(document.querySelector(".collection-list-item.selected")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /规划指引 1/ }));
    expect(screen.getByRole("heading", { name: "规划指引 1", level: 3 })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "＋ 规划指引" }));
    expect(screen.getByRole("heading", { name: "规划指引 3", level: 3 })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("指引内容"), { target: { value: "新的通用指引" } });
    fireEvent.click(screen.getByRole("button", { name: "↑" }));
    expect(screen.getByRole("heading", { name: "规划指引 2", level: 3 })).toBeInTheDocument();
    expect(screen.getByDisplayValue("新的通用指引")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("dialog", { name: "删除「规划指引 2」？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    expect(screen.queryByDisplayValue("新的通用指引")).not.toBeInTheDocument();
    expect(document.querySelector(".object-count")).toHaveTextContent("2");
  });

  it("creates and deletes a uniquely addressable recovery strategy", async () => {
    renderEditor("/scenarios/scenario-1/edit/planning-recovery");
    await waitFor(() => expect(screen.getByText("请选择或新增一条失败恢复策略。")).toBeInTheDocument());
    expect(document.querySelector(".collection-list-item.selected")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /BLOCKED/ }));
    expect(screen.getByRole("heading", { name: "BLOCKED", level: 3 })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "＋ 失败恢复策略" }));
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(screen.getByDisplayValue("请填写失败恢复策略。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "↑" }));
    expect(screen.getByRole("heading", { name: "FAILURE", level: 3 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "↓" }));

    vi.mocked(api.transformWorkingCopy).mockImplementationOnce(async (_id, _revision, document) => {
      const definition = structuredClone(document) as typeof draft.definition_document;
      definition.planning.recovery_hints = definition.planning.recovery_hints.filter((item) => item.failure_code !== "FAILURE");
      return { scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [], definition_document: definition };
    });
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("dialog", { name: "删除「失败代码 · FAILURE」？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(screen.getByText("请选择或新增一条失败恢复策略。")).toBeInTheDocument());
    expect(screen.queryByDisplayValue("请填写失败恢复策略。")).not.toBeInTheDocument();
  });

  it("replays a validation locator into the matching root collection item", async () => {
    vi.mocked(api.validateDraft).mockResolvedValue({
      scenario_id: "scenario-1",
      revision: 1,
      content_hash: null,
      publish_ready: false,
      readiness: [],
      issues: [{ severity: "ERROR", code: "POOL_QUANTITY", path: "initialization.resource_pools.0.quantity", message: "Invalid quantity", locator: { object_kind: "initialization", object_key: null, field_path: "resource_pools.0.quantity" } }],
    });
    renderEditor("/scenarios/scenario-1/edit/validation");
    await waitFor(() => expect(screen.getByRole("heading", { name: "验证与发布" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "验证当前草稿" }));
    fireEvent.click(await screen.findByText(/POOL_QUANTITY/));

    await waitFor(() => expect(screen.getByRole("heading", { name: "中央区 · 救援物资", level: 4 })).toBeInTheDocument());
    expect(screen.getByLabelText(/数量/)).toBeInTheDocument();
  });

  it("guards deletion of a referenced resource pool", async () => {
    vi.mocked(api.transformWorkingCopy).mockRejectedValue(new ApiError("该集合项仍被其他配置引用，不能删除。请先移除相关引用。", 409, "SCENARIO_ROOT_COLLECTION_ITEM_REFERENCED", {
      references: [{
        source: { object_kind: "rule", object_key: "stabilize", field_path: "rules.0.condition.resource_pool_key" },
        target: { object_kind: "initialization", object_key: null, field_path: "resource_pools.central_pool" },
      }],
    }));
    renderEditor("/scenarios/scenario-1/edit/initialization");
    await waitFor(() => expect(screen.getByRole("button", { name: /资源/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /资源/ }));
    fireEvent.click(screen.getByRole("button", { name: /资源池/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /central_pool/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /central_pool/ }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "中央区 · 救援物资", level: 4 })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "删除此资源池" }));
    const dialog = await screen.findByRole("dialog", { name: "无法删除「central_pool」" });
    expect(within(dialog).getByText("规则")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("stabilize");
    expect(within(dialog).getByText("rules.0.condition.resource_pool_key")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "前往" })).toHaveAttribute("href", expect.stringContaining("/scenarios/scenario-1/edit/rules/stabilize"));
    expect(within(dialog).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
  });

  it("keeps Resource Source Hint authoring collection-only and splits relation authoring", async () => {
    renderEditor("/scenarios/scenario-1/edit/public-knowledge");
    await waitFor(() => expect(screen.getByRole("heading", { name: "资源来源提示" })).toBeInTheDocument());
    const publicKnowledgeMaster = document.querySelector<HTMLElement>(".object-panel")!;
    expect(within(publicKnowledgeMaster).getByText("1", { selector: ".object-count" })).toBeInTheDocument();
    expect(within(publicKnowledgeMaster).getByRole("button", { name: "＋ 资源来源提示" })).toBeInTheDocument();
    expect(publicKnowledgeMaster.querySelector(".collection-list-group")).toBeNull();
    expect(screen.queryByText("资源发现知识")).not.toBeInTheDocument();
    expect(screen.getByText("救援物资")).toBeInTheDocument();
    expect(screen.getByText("主要来源：中央区")).toBeInTheDocument();
    expect(screen.getByText("资源 · relief")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "资源来源提示配置" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("搜索"), { target: { value: "救援物资" } });
    expect(screen.getByText("资源 · relief")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("搜索"), { target: { value: "relief" } });
    expect(screen.getByText("救援物资")).toBeInTheDocument();
    fireEvent.click(within(publicKnowledgeMaster).getByRole("button", { name: /救援物资/ }));
    expect(screen.getByRole("heading", { name: "救援物资", level: 3 })).toBeInTheDocument();
    expect(screen.getByText("资源来源提示 · 可编辑对象")).toBeInTheDocument();
    expect(screen.queryByText("集合项")).not.toBeInTheDocument();
    expect(screen.queryByText(/public_knowledge\.resource_source_hints\.\d+/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("资源")).toBeDisabled();
    expect(screen.getByText(/资源在创建来源提示时确定/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "关系类型" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "关系类型" })).toBeInTheDocument());
    expect(screen.getByText("位于", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "＋ 新增关系类型" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "关系实例" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "关系实例" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "＋ 新增关系实例" })).toBeInTheDocument();
    expect(screen.getByText("关系实例 · located_in")).toBeInTheDocument();
  });

  it("creates a Resource Source Hint only after choosing an unused Resource and keeps its semantic identity", async () => {
    const customDraft = structuredClone(draft);
    customDraft.definition_document.world.resources = [...customDraft.definition_document.world.resources, { key: "water", name: "饮用水" }];
    customDraft.definition_document.world.nodes = [...customDraft.definition_document.world.nodes, { key: "north", name: "北部区域", node_type_key: "region" }];
    vi.mocked(api.draft).mockResolvedValueOnce(customDraft);

    renderEditor("/scenarios/scenario-1/edit/public-knowledge");
    await waitFor(() => expect(screen.getByRole("button", { name: "＋ 资源来源提示" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "＋ 资源来源提示" }));

    const dialog = screen.getByRole("dialog", { name: "新增资源来源提示" });
    expect(document.querySelector(".object-count")).toHaveTextContent("1");
    expect(dialog).not.toHaveTextContent("new_resource");
    expect(dialog).not.toHaveTextContent("new_region");
    expect(within(dialog).getByRole("button", { name: "创建" })).toBeDisabled();
    const resourceField = within(dialog).getByLabelText(/资源/);
    expect(resourceField).toHaveDisplayValue("选择已有资源…");
    expect(within(resourceField).queryByRole("option", { name: /救援物资/ })).not.toBeInTheDocument();

    fireEvent.change(resourceField, { target: { value: "water" } });
    fireEvent.change(within(dialog).getByLabelText("主要区域"), { target: { value: "central" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));

    expect(screen.queryByRole("dialog", { name: "新增资源来源提示" })).not.toBeInTheDocument();
    expect(document.querySelector(".object-count")).toHaveTextContent("2");
    expect(screen.getByRole("heading", { name: "饮用水", level: 3 })).toBeInTheDocument();
    expect(screen.getByText("资源来源提示 · 可编辑对象")).toBeInTheDocument();
    expect(screen.getByLabelText("资源")).toBeDisabled();
    expect(screen.getByLabelText("资源")).toHaveValue("water");
    expect(screen.getByLabelText("主要区域")).toHaveValue("central");
    expect(screen.queryByText(/public_knowledge\.resource_source_hints\.\d+/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("主要区域"), { target: { value: "north" } });
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "候选区域：添加" }));
    fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: /中央区/ }));
    expect(screen.getByText("中央区", { selector: ".value-chip" })).toBeInTheDocument();

    vi.mocked(api.transformWorkingCopy).mockImplementationOnce(async (_id, _revision, document) => {
      const transformed = structuredClone(document);
      const knowledge = transformed.public_knowledge as { resource_source_hints: Array<{ resource_key: string }> };
      knowledge.resource_source_hints = knowledge.resource_source_hints.filter((item) => item.resource_key !== "water");
      return { scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [], definition_document: transformed };
    });
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const deleteDialog = await screen.findByRole("dialog", { name: "删除「饮用水」？" });
    fireEvent.click(within(deleteDialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "救援物资", level: 3 })).toBeInTheDocument());
    expect(document.querySelector(".object-count")).toHaveTextContent("1");
  });

  it("routes Resource ownership correctly when Resource Source Hints have no Resource candidates", async () => {
    const customDraft = structuredClone(draft);
    customDraft.definition_document.world.resources = [];
    customDraft.definition_document.public_knowledge.resource_source_hints = [];
    vi.mocked(api.draft).mockResolvedValueOnce(customDraft);

    renderEditor("/scenarios/scenario-1/edit/public-knowledge");
    await waitFor(() => expect(screen.getByRole("button", { name: "＋ 资源来源提示" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "＋ 资源来源提示" }));
    const dialog = screen.getByRole("dialog", { name: "新增资源来源提示" });
    expect(within(dialog).getByText("暂无可选资源")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "前往资源定义" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/resources");
    expect(within(dialog).getByRole("button", { name: "创建" })).toBeDisabled();
    expect(within(dialog).queryByLabelText(/资源/)).not.toBeInTheDocument();
    expect(document.querySelector(".object-count")).toHaveTextContent("0");
  });

  it("normalizes unqualified relation type links to the dedicated route", async () => {
    renderEditor("/scenarios/scenario-1/edit/relations/located_in");
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/relation-types/located_in"));
    expect(screen.getAllByLabelText("显示名称").some((element) => (element as HTMLInputElement).value === "位于")).toBe(true);
    expect(screen.queryByLabelText("来源节点")).not.toBeInTheDocument();
  });

  it("normalizes a kind-qualified relation type link to the dedicated route", async () => {
    renderEditor("/scenarios/scenario-1/edit/relations/located_in?kind=relation_type");
    await waitFor(() => {
      expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/relation-types/located_in");
      expect(screen.getAllByLabelText("显示名称").some((element) => (element as HTMLInputElement).value === "位于")).toBe(true);
    });
    expect(screen.getByTestId("editor-location")).not.toHaveTextContent("kind=");
  });

  it("normalizes the legacy relation instance query without changing its route", async () => {
    renderEditor("/scenarios/scenario-1/edit/relations/located_in?kind=relation");
    await waitFor(() => {
      expect(screen.getByLabelText("来源节点")).toBeInTheDocument();
      expect(screen.queryAllByLabelText("显示名称")).toHaveLength(0);
      expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/relations/located_in");
      expect(screen.getByTestId("editor-location")).not.toHaveTextContent("kind=");
    });
  });

  it("keeps the current relation type deep link canonical", async () => {
    renderEditor("/scenarios/scenario-1/edit/relation-types/located_in");
    await waitFor(() => expect(screen.getByRole("heading", { name: "位于" })).toBeInTheDocument());
    expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/relation-types/located_in");
  });
});
