import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./api";
import { EditorPage } from "./pages/EditorPage";

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
    scenario: vi.fn(),
    scenarioVersion: vi.fn(),
    analyzeWorkingCopyReferences: vi.fn(),
    completeness: vi.fn(),
    transformWorkingCopy: vi.fn(),
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
      nodes: [{ key: "north", name: "North", node_type_key: "scope", facts: [{ key: "repair_type", name: "设施修复类型", value_type: "STRING", initial_value: "standard", initial_visibility: "KNOWN", allowed_values: [] }] }, { key: "north_a", name: "North A", node_type_key: "entity" }],
      relations: [{ key: "north_a__belongs_to__north", source_node_key: "north_a", relation_type_key: "belongs_to", target_node_key: "north" }],
      resources: [],
    },
    rules: [{ key: "restore_communication", name: "恢复通信" }],
  },
  validation_status: "VALID",
  validation_issues: [],
  content_hash: "hash",
  base_scenario_version_id: null,
  updated_at: "2026-09-15T00:00:00Z",
};

beforeEach(() => {
  vi.mocked(api.draft).mockResolvedValue(draft);
  vi.mocked(api.scenario).mockResolvedValue({ id: "scenario-1", key: "scenario-1", name: "Test Scenario", status: "DRAFT", draft_revision: 1, current_published_version_id: null, current_published_version_number: null, created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z" });
  vi.mocked(api.scenarioVersion).mockReset();
  vi.mocked(api.analyzeWorkingCopyReferences).mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [] });
  vi.mocked(api.completeness).mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, items: [], required_missing: 0, recommended_missing: 0, validation_issue_count: 0, reference_edge_count: 0 });
  vi.mocked(api.transformWorkingCopy).mockReset();
  vi.mocked(api.saveDraft).mockResolvedValue(draft);
});

function LocationProbe() {
  return <output data-testid="editor-location">{useLocation().pathname}</output>;
}

function renderEditor(initialEntry = "/scenarios/scenario-1/edit/world") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initialEntry]}><Routes><Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} /><Route path="/scenarios/:scenarioId/edit/:section/:objectKey" element={<EditorPage />} /><Route path="/scenarios/:scenarioId" element={<div>Scenario Detail</div>} /></Routes><LocationProbe /></MemoryRouter></QueryClientProvider>);
}

describe("EditorPage topology interaction contract", () => {
  it("auto-opens the Inspector on topology selection without dirtying the Working Copy", async () => {
    renderEditor();
    await waitFor(() => expect(screen.getByRole("button", { name: "North" })).toBeInTheDocument());

    expect(screen.getByText("草稿 r1 · 已验证")).toBeInTheDocument();
    expect(screen.getByText("暂无发布版本")).toBeInTheDocument();
    expect(document.querySelector(".version-badges")).toBeInTheDocument();
    expect(document.querySelector(".version-status-panel")).toBeNull();
    expect(screen.queryByText(/SCENARIO VERSION BOUNDARY|DRAFT AHEAD|BASE VERSION UUID|content hash/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "North" }));

    await waitFor(() => expect(screen.getByText("拓扑检查器")).toBeInTheDocument());
    expect(screen.getByText("范围摘要")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
    expect(screen.getByText("未修改")).toBeInTheDocument();
  });

  it("relocates return to the action bar and reuses the Working Copy dirty guard", async () => {
    renderEditor("/scenarios/scenario-1/edit/overview");
    const returnButton = await screen.findByRole("button", { name: "返回场景详情" });

    expect(screen.queryByText("返回场景")).not.toBeInTheDocument();
    const discard = screen.getByRole("button", { name: "放弃修改" });
    expect(discard.compareDocumentPosition(returnButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("button", { name: "显示检查器" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("Test Scenario"), { target: { value: "Changed Scenario" } });
    fireEvent.click(returnButton);
    const discardDialog = await screen.findByRole("dialog", { name: "放弃当前修改？" });
    expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1/edit/overview");
    expect(screen.getByDisplayValue("Changed Scenario")).toBeVisible();

    fireEvent.click(within(discardDialog).getByRole("button", { name: "取消" }));
    fireEvent.click(returnButton);
    const secondDiscardDialog = await screen.findByRole("dialog", { name: "放弃当前修改？" });
    fireEvent.click(within(secondDiscardDialog).getByRole("button", { name: "放弃修改" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1"));
  });

  it("returns directly to Scenario Detail when the Working Copy is clean", async () => {
    renderEditor("/scenarios/scenario-1/edit/overview");
    fireEvent.click(await screen.findByRole("button", { name: "返回场景详情" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1"));
  });

  it("keeps saved-draft-ahead and unsaved-working-copy states distinct in the compact badges", async () => {
    const aheadDraft = structuredClone(draft);
    const aheadMetadata = aheadDraft.definition_document.metadata as Record<string, unknown>;
    aheadMetadata.name = "Ahead Draft";
    vi.mocked(api.draft).mockResolvedValue(aheadDraft);
    vi.mocked(api.scenario).mockResolvedValue({ id: "scenario-1", key: "scenario-1", name: "Test Scenario", status: "PUBLISHED", draft_revision: 1, current_published_version_id: "version-7", current_published_version_number: 7, created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z" });
    vi.mocked(api.scenarioVersion).mockResolvedValue({ id: "version-7", scenario_id: "scenario-1", version_number: 7, schema_version: 2, content_hash: "published-hash", published_at: "2026-09-15T00:00:00Z", definition_document: draft.definition_document });

    renderEditor("/scenarios/scenario-1/edit/overview");
    const draftBadge = await screen.findByText("草稿 r1 · 已验证");
    expect(draftBadge).toHaveClass("warning");
    expect(draftBadge).toHaveAttribute("title", "当前草稿与最新已发布版本不同。");
    expect(screen.getByText("已发布 v7")).toBeInTheDocument();

    fireEvent.change(await screen.findByDisplayValue("Ahead Draft"), { target: { value: "Unsaved Name" } });
    await waitFor(() => expect(screen.getByText("有未保存修改")).toBeInTheDocument());
    expect(screen.getByText("草稿 r1 · 已验证")).toHaveClass("warning");
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("preflights fact deletion and applies only after the custom confirmation", async () => {
    const transform = vi.mocked(api.transformWorkingCopy);
    const transformedDocument = structuredClone(draft.definition_document) as Record<string, unknown>;
    const world = transformedDocument.world as Record<string, unknown>;
    const nodes = world.nodes as Array<Record<string, unknown>>;
    nodes[0] = { ...nodes[0], facts: [] };
    transform.mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [], definition_document: transformedDocument });
    renderEditor("/scenarios/scenario-1/edit/world-entities/north");
    const factDelete = await screen.findByRole("button", { name: "删除事实" });
    fireEvent.click(factDelete);

    await waitFor(() => expect(transform).toHaveBeenCalledWith("scenario-1", 1, expect.any(Object), { kind: "DELETE_FACT", object_kind: "node", node_key: "north", fact_key: "repair_type" }));
    const confirmDialog = await screen.findByRole("dialog", { name: "删除「设施修复类型」？" });
    expect(confirmDialog).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除事实" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除事实" }));
    const secondConfirmDialog = await screen.findByRole("dialog", { name: "删除「设施修复类型」？" });
    fireEvent.click(within(secondConfirmDialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "删除事实" })).not.toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("shows the reference block from the transform preflight without force deletion", async () => {
    const transform = vi.mocked(api.transformWorkingCopy);
    transform.mockRejectedValue(new ApiError("fact referenced", 409, "SCENARIO_FACT_REFERENCED", {
      references: [{ source: { object_kind: "rule", object_key: "restore_communication", field_path: "conditions.0.fact_key" }, target: { object_kind: "node", object_key: "north", field_path: "facts.0.key" } }],
    }));
    renderEditor("/scenarios/scenario-1/edit/world-entities/north");
    fireEvent.click(await screen.findByRole("button", { name: "删除事实" }));

    const dialog = await screen.findByRole("dialog", { name: "无法删除「设施修复类型」" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/恢复通信/)).toBeInTheDocument();
    expect(within(dialog).getByText("conditions.0.fact_key")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "前往" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/rules/restore_communication?focus_path=conditions.0.fact_key");
    expect(within(dialog).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
  });

  it("shows a readable delete-preflight error without replacing the working copy", async () => {
    const transform = vi.mocked(api.transformWorkingCopy);
    const invalidDraft = structuredClone(draft);
    const invalidWorld = invalidDraft.definition_document.world as Record<string, unknown>;
    const invalidNodes = invalidWorld.nodes as Array<Record<string, unknown>>;
    const invalidFacts = invalidNodes[0].facts as Array<Record<string, unknown>>;
    invalidFacts[0].value_type = "NOT_A_SCHEMA_VALUE";
    vi.mocked(api.draft).mockResolvedValue(invalidDraft);
    transform.mockRejectedValue(new ApiError("Request validation failed", 422, "VALIDATION_ERROR", {
      errors: [{ loc: ["body", "definition_document", "world", "nodes"], msg: "invalid node payload" }],
    }));
    renderEditor("/scenarios/scenario-1/edit/world-entities/north");
    fireEvent.click(await screen.findByRole("button", { name: "删除事实" }));

    const dialog = await screen.findByRole("dialog", { name: "暂时无法检查是否可以删除" });
    expect(within(dialog).getByText("接口校验未通过（definition_document.world.nodes）：invalid node payload")).toBeInTheDocument();
    expect(within(dialog).getByText("错误代码：VALIDATION_ERROR")).toBeInTheDocument();
    expect(screen.queryByText("VALIDATION ERROR（VALIDATION_ERROR）")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除事实" })).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps an unsaved new Node across editor sections and can delete only that Node", async () => {
    renderEditor("/scenarios/scenario-1/edit/world-entities");
    fireEvent.click(await screen.findByRole("button", { name: "＋ 节点" }));
    await screen.findAllByDisplayValue("New node");
    const name = document.getElementById("editor-field-node-new_node-name")!;
    fireEvent.change(name, { target: { value: "未保存测试节点" } });
    fireEvent.change(screen.getByLabelText("节点类型 *"), { target: { value: "entity" } });

    expect(screen.getAllByRole("button", { name: "＋ 添加事实" })).toHaveLength(1);
    expect(screen.queryByText("接下来可以")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往初始化" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "前往关系实例" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "配置检查" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/configuration-check"));
    fireEvent.click(screen.getByRole("link", { name: "初始化" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/initialization"));
    fireEvent.click(screen.getByRole("link", { name: "全局规划指引" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/planning-instructions"));
    expect(screen.queryByRole("dialog", { name: "放弃当前修改？" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "世界实体" }));
    await waitFor(() => expect(screen.getByText("未保存测试节点", { selector: ".object-list-item span" })).toBeInTheDocument());
    fireEvent.click(screen.getByText("未保存测试节点", { selector: ".object-list-item span" }));
    expect((await screen.findAllByDisplayValue("未保存测试节点")).length).toBeGreaterThan(0);

    vi.mocked(api.transformWorkingCopy).mockImplementationOnce(async (_id, _revision, document) => {
      const definition = structuredClone(document) as typeof draft.definition_document;
      definition.world.nodes = definition.world.nodes.filter((node) => node.key !== "new_node");
      return { scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [], definition_document: definition };
    });
    fireEvent.click(screen.getByRole("button", { name: "删除节点" }));
    const dialog = await screen.findByRole("dialog", { name: "删除「未保存测试节点」？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(screen.queryByText("未保存测试节点", { selector: ".object-list-item span" })).not.toBeInTheDocument());
    expect(screen.getByText("North", { selector: ".object-list-item span" })).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps NodeType creation on its owner page while preserving the unsaved Node", async () => {
    vi.mocked(api.completeness).mockImplementation(async (_id, _revision, document) => {
      const world = document.world as { nodes?: Array<{ key?: string; node_type_key?: string }> } | undefined;
      const node = world?.nodes?.find((candidate) => candidate.key === "new_node");
      return {
        scenario_id: "scenario-1",
        base_revision: 1,
        items: node && !node.node_type_key ? [{
          key: "node:new_node:node-type",
          title: "配置节点类型",
          level: "INCOMPLETE_REQUIRED" as const,
          dependency_kind: "PUBLISH_REQUIRED" as const,
          message: "节点必须关联节点类型。",
          path: "world.nodes.new_node.node_type_key",
          locator: { object_kind: "node", object_key: "new_node", field_path: "node_type_key" },
          action: "CREATE" as const,
        }] : [],
        required_missing: node && !node.node_type_key ? 1 : 0,
        recommended_missing: 0,
        validation_issue_count: 0,
        reference_edge_count: 0,
      };
    });
    renderEditor("/scenarios/scenario-1/edit/world-entities");
    fireEvent.click(await screen.findByRole("button", { name: "＋ 节点" }));
    expect(screen.getByLabelText("节点类型 *")).toHaveValue("");
    expect(screen.getByRole("alert")).toHaveTextContent("请选择节点类型");
    expect(screen.queryByRole("button", { name: "创建并关联" })).not.toBeInTheDocument();
    const ownerLink = screen.getByRole("link", { name: "前往节点类型" });
    expect(ownerLink).toHaveAttribute("href", "/scenarios/scenario-1/edit/node-types");
    fireEvent.click(ownerLink);
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/node-types"));
    fireEvent.click(screen.getByRole("link", { name: "世界实体" }));
    await waitFor(() => expect(screen.getByText("New node", { selector: ".object-list-item span" })).toBeInTheDocument());
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("routes a blank generic working document to the NodeType owner without guessing a type", async () => {
    vi.mocked(api.draft).mockResolvedValue({
      ...draft,
      definition_document: {
        ...draft.definition_document,
        metadata: { key: "blank-scenario", name: "Blank Scenario" },
        world: { key: "blank-world", name: "Blank World", node_types: [], nodes: [], relations: [], resources: [] },
      },
    });
    renderEditor("/scenarios/scenario-1/edit/world-entities");
    fireEvent.click(await screen.findByRole("button", { name: "＋ 节点" }));
    expect(screen.getByLabelText("节点类型 *")).toHaveValue("");
    expect(screen.queryByRole("button", { name: /创建.*节点类型/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "前往节点类型" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/node-types"));
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
