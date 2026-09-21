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
    const nativeConfirm = vi.spyOn(window, "confirm");

    renderEditor("/scenarios/scenario-1/edit/world-entities/north");
    const factDelete = await screen.findByRole("button", { name: "删除事实" });
    fireEvent.click(factDelete);

    await waitFor(() => expect(transform).toHaveBeenCalledWith("scenario-1", 1, expect.any(Object), { kind: "DELETE_FACT", object_kind: "node", node_key: "north", fact_key: "repair_type" }));
    expect(nativeConfirm).not.toHaveBeenCalled();
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
    const nativeConfirm = vi.spyOn(window, "confirm");

    renderEditor("/scenarios/scenario-1/edit/world-entities/north");
    fireEvent.click(await screen.findByRole("button", { name: "删除事实" }));

    const dialog = await screen.findByRole("dialog", { name: "无法删除「设施修复类型」" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText(/恢复通信/)).toBeInTheDocument();
    expect(within(dialog).getByText("conditions.0.fact_key")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "前往" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/rules/restore_communication?focus_path=conditions.0.fact_key");
    expect(within(dialog).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(nativeConfirm).not.toHaveBeenCalled();
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
    const nativeConfirm = vi.spyOn(window, "confirm");

    renderEditor("/scenarios/scenario-1/edit/world-entities/north");
    fireEvent.click(await screen.findByRole("button", { name: "删除事实" }));

    const dialog = await screen.findByRole("dialog", { name: "暂时无法检查是否可以删除" });
    expect(within(dialog).getByText("接口校验未通过（definition_document.world.nodes）：invalid node payload")).toBeInTheDocument();
    expect(within(dialog).getByText("错误代码：VALIDATION_ERROR")).toBeInTheDocument();
    expect(screen.queryByText("VALIDATION ERROR（VALIDATION_ERROR）")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除事实" })).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
    expect(nativeConfirm).not.toHaveBeenCalled();
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
