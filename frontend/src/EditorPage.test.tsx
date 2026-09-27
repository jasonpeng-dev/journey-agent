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
    semanticDiff: vi.fn(),
    analyzeWorkingCopyReferences: vi.fn(),
    completeness: vi.fn(),
    initializationPreview: vi.fn(),
    transformWorkingCopy: vi.fn(),
    saveDraft: vi.fn(),
    renameKey: vi.fn(),
    validateDraft: vi.fn(),
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
    planning: { instructions: ["First instruction", "Second instruction"] },
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
  vi.mocked(api.semanticDiff).mockReset();
  vi.mocked(api.analyzeWorkingCopyReferences).mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [] });
  vi.mocked(api.completeness).mockResolvedValue({ scenario_id: "scenario-1", base_revision: 1, items: [], required_missing: 0, recommended_missing: 0, validation_issue_count: 0, reference_edge_count: 0 });
  vi.mocked(api.initializationPreview).mockResolvedValue({ revision: 1, projection: { domains: [], findings: [], summary: { nodes: 0, actors: 0, resource_pools: 0, relations: 0, derived_states: 0, warnings: 0 } }, parity: { published: false, initialization_changes: [], design_changes: [] } });
  vi.mocked(api.transformWorkingCopy).mockReset();
  vi.mocked(api.saveDraft).mockResolvedValue(draft);
  vi.mocked(api.renameKey).mockReset();
  vi.mocked(api.validateDraft).mockReset().mockResolvedValue({ scenario_id: "scenario-1", revision: 1, content_hash: "hash", publish_ready: true, issues: [], readiness: [] });
});

function LocationProbe() {
  return <output data-testid="editor-location">{useLocation().pathname}</output>;
}

function renderEditor(initialEntry = "/scenarios/scenario-1/edit/world") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initialEntry]}><Routes><Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} /><Route path="/scenarios/:scenarioId/edit/:section/:objectKey" element={<EditorPage />} /><Route path="/scenarios/:scenarioId" element={<div>Scenario Detail</div>} /></Routes><LocationProbe /></MemoryRouter></QueryClientProvider>);
}

describe("EditorPage topology interaction contract", () => {
  it("selects and focuses positional Planning instruction locators without claiming a stable identity", async () => {
    renderEditor("/scenarios/scenario-1/edit/planning-instructions?owner=instruction&item=1&focus_path=instructions.1");

    const field = await waitFor(() => {
      const target = document.querySelector('[data-field-path="planning.instructions.1"]');
      expect(target).not.toBeNull();
      expect(target?.querySelector("textarea")).toHaveFocus();
      expect(target).toHaveClass("is-focus-highlighted");
      return target;
    });
    expect(field?.querySelector("textarea")).toHaveValue("Second instruction");
    expect(screen.getByRole("button", { name: /规划指引 2/ })).toHaveClass("selected");
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("marks the Scenario name required while keeping the stable metadata key static", async () => {
    const incompleteDraft = structuredClone(draft);
    (incompleteDraft.definition_document.metadata as Record<string, unknown>).name = "";
    vi.mocked(api.draft).mockResolvedValue(incompleteDraft);
    renderEditor("/scenarios/scenario-1/edit/overview");

    const nameField = await waitFor(() => {
      const field = document.querySelector('[data-field-path="metadata.name"]');
      expect(field).not.toBeNull();
      return field;
    });
    if (!nameField) throw new Error("Expected Scenario name field.");
    expect(nameField?.querySelector(".required-marker")).toBeInTheDocument();
    expect(nameField?.querySelector('[role="alert"]')).toHaveTextContent("此项为必填内容。");
    const nameInput = nameField?.querySelector<HTMLInputElement>("input");
    expect(nameInput).not.toBeNull();
    if (!nameInput) throw new Error("Expected Scenario name input.");
    fireEvent.change(nameInput, { target: { value: "Updated Scenario" } });
    expect(nameField.querySelector('[role="alert"]')).toBeNull();

    const keyField = document.querySelector('[data-field-path="metadata.key"]');
    expect(keyField?.querySelector(".required-marker")).toBeNull();
    expect(keyField?.querySelector("output.stable-identity-value")).toHaveTextContent("scenario-1");
    const descriptionField = document.querySelector('[data-field-path="metadata.description"]');
    expect(descriptionField?.querySelector(".required-marker")).toBeNull();
    expect(descriptionField?.querySelector('[role="alert"]')).toBeNull();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps the entity display name editable only in detail and Inspector identity-only", async () => {
    renderEditor("/scenarios/scenario-1/edit/world-entities/north?kind=node");

    const nameField = await waitFor(() => {
      const field = document.querySelector('[data-field-path="node.north.name"]');
      expect(field).not.toBeNull();
      return field;
    });
    expect(nameField?.querySelectorAll("input")).toHaveLength(1);

    const inspector = document.querySelector(".inspector");
    await waitFor(() => expect(inspector?.querySelector(".topology-inspector-title")).toHaveTextContent("North"));
    expect(inspector?.querySelectorAll("input, select, textarea")).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: "删除节点" })).toHaveLength(1);
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

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

  it("runs a clean stable-key rename through the safe API and validates the persisted result", async () => {
    const actionDraft = structuredClone(draft);
    const actionDocument = actionDraft.definition_document as Record<string, unknown>;
    actionDocument.actions = [{
      key: "inspect_patient",
      name: "Inspect patient",
      description: "",
      required_interaction_key: "diagnosable",
      execution_mode: "IMMEDIATE",
      parameters: [],
      expected_outcomes: [],
      planning: { terminal_effects: [], supporting_effects: [], success_outcome_codes: [], wait_success_outcome_codes: [], hints: [] },
    }];
    vi.mocked(api.draft).mockResolvedValue(actionDraft);
    const renamedDraft = structuredClone(actionDraft);
    renamedDraft.revision = 2;
    const renamedDocument = renamedDraft.definition_document as Record<string, unknown>;
    (renamedDocument.actions as Array<Record<string, unknown>>)[0].key = "inspect_patient_v2";
    vi.mocked(api.renameKey).mockResolvedValue(renamedDraft);
    vi.mocked(api.validateDraft).mockResolvedValue({ scenario_id: "scenario-1", revision: 2, content_hash: "renamed-hash", publish_ready: true, issues: [], readiness: [] });

    renderEditor("/scenarios/scenario-1/edit/actions/inspect_patient?kind=action");
    await waitFor(() => expect(screen.getAllByText("inspect_patient", { selector: "output.stable-identity-value" }).length).toBeGreaterThan(0));
    expect(screen.queryByDisplayValue("inspect_patient")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "重命名稳定键" }));
    const dialog = await screen.findByRole("dialog");
    const form = dialog.querySelector("form")!;
    const newKeyInput = form.querySelector("input[name='new-key']");
    expect(newKeyInput).not.toBeNull();
    fireEvent.change(newKeyInput!, { target: { value: "inspect_patient_v2" } });
    fireEvent.submit(form);

    await waitFor(() => expect(api.renameKey).toHaveBeenCalledWith("scenario-1", 1, "action", "inspect_patient", "inspect_patient_v2"));
    await waitFor(() => expect(api.validateDraft).toHaveBeenCalledWith("scenario-1", 2));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1/edit/actions/inspect_patient_v2"));
    expect(api.transformWorkingCopy).not.toHaveBeenCalled();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("adds an ExpectedOutcome to the unsaved Working Copy only after explicit identity submission", async () => {
    const actionDraft = structuredClone(draft);
    Object.assign(actionDraft.definition_document, {
      interactions: [{ key: "inspect", name: "Inspect" }],
      actions: [{
        key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE",
        parameters: [], expected_outcomes: [], operation_bindings: [],
        planning: { terminal_effects: [], supporting_effects: [], success_outcome_codes: [], wait_success_outcome_codes: [], hints: [] },
      }],
    });
    vi.mocked(api.draft).mockResolvedValue(actionDraft);
    renderEditor("/scenarios/scenario-1/edit/actions/repair?kind=action");

    fireEvent.click(await screen.findByRole("button", { name: "＋ 添加结果" }));
    const dialog = screen.getByRole("dialog", { name: "创建结果" });
    expect(within(dialog).getByLabelText(/结果代码/)).toHaveValue("");
    expect(screen.queryByText("SUCCESS", { selector: "output.stable-identity-value" })).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/结果代码/), { target: { value: "REPAIRED" } });
    expect(within(dialog).queryByLabelText(/Display name|Success/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));

    expect(await screen.findByText("REPAIRED", { selector: "output.stable-identity-value" })).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="action.repair.expected_outcomes.0.name"] .required-marker')).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="action.repair.expected_outcomes.0.name"] [role="alert"]')).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="action.repair.expected_outcomes.0.success"] .required-marker')).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="action.repair.expected_outcomes.0.success"] [role="alert"]')).toBeInTheDocument();
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
    expect(api.transformWorkingCopy).not.toHaveBeenCalled();
  });

  it("keeps saved-draft-ahead and unsaved-working-copy states distinct in the compact badges", async () => {
    const aheadDraft = structuredClone(draft);
    const aheadMetadata = aheadDraft.definition_document.metadata as Record<string, unknown>;
    aheadMetadata.name = "Ahead Draft";
    vi.mocked(api.draft).mockResolvedValue(aheadDraft);
    vi.mocked(api.scenario).mockResolvedValue({ id: "scenario-1", key: "scenario-1", name: "Test Scenario", status: "PUBLISHED", draft_revision: 1, current_published_version_id: "version-7", current_published_version_number: 7, created_at: "2026-09-15T00:00:00Z", updated_at: "2026-09-15T00:00:00Z" });
    vi.mocked(api.scenarioVersion).mockResolvedValue({ id: "version-7", scenario_id: "scenario-1", version_number: 7, schema_version: 2, content_hash: "published-hash", published_at: "2026-09-15T00:00:00Z", definition_document: draft.definition_document });
    vi.mocked(api.semanticDiff).mockResolvedValue({ published: true, compared_version: { id: "version-7", version_number: 7, schema_version: 2 }, published_version_id: "version-7", published_version_number: 7, published_schema_version: 2, is_equal: false, comparable: true, total_changed_objects: 1, total_changed_items: 1, section_summaries: [{ section: "世界模型", count: 1, subsections: [{ subsection: "场景信息", count: 1 }] }], entries: [] });

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
    expect(within(dialog).getAllByText(/恢复通信/)).toHaveLength(2);
    expect(within(dialog).queryByText("conditions.0.fact_key")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "前往恢复通信" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/rules/restore_communication?focus_path=conditions.0.fact_key");
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
    expect(within(dialog).getByText("当前工作副本中有字段尚未通过校验，暂时无法确认是否可以安全删除。")).toBeInTheDocument();
    expect(within(dialog).queryByText(/definition_document\.world\.nodes/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/invalid node payload/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/问题技术详情|VALIDATION_ERROR/)).not.toBeInTheDocument();
    expect(screen.queryByText("VALIDATION ERROR（VALIDATION_ERROR）")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除事实" })).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("shows canonical Node backreferences and opens exact Relation consumers", async () => {
    const relationDraft = structuredClone(draft);
    relationDraft.definition_document.world.nodes.push({ key: "north_b", name: "North B", node_type_key: "entity" });
    relationDraft.definition_document.world.relations.push({ key: "north_b__belongs_to__north", source_node_key: "north_b", relation_type_key: "belongs_to", target_node_key: "north" });
    vi.mocked(api.draft).mockResolvedValue(relationDraft);
    vi.mocked(api.analyzeWorkingCopyReferences).mockResolvedValue({
      scenario_id: "scenario-1",
      base_revision: 1,
      source: "WORKING_COPY",
      references: [
        { source: { object_kind: "relation", object_key: "north_a__belongs_to__north", field_path: "target_node_key" }, target: { object_kind: "node", object_key: "north", field_path: null } },
        { source: { object_kind: "relation", object_key: "north_b__belongs_to__north", field_path: "target_node_key" }, target: { object_kind: "node", object_key: "north", field_path: null } },
      ],
    });
    renderEditor("/scenarios/scenario-1/edit/world-entities/north?kind=node");
    const usage = await waitFor(() => {
      const section = document.querySelector(".backreference-usage-section");
      expect(section).not.toBeNull();
      return section as HTMLElement;
    });
    const detailShell = usage.closest(".detail-content-shell");
    expect(detailShell).not.toBeNull();
    expect(detailShell?.querySelector(".typed-entity-editor")).not.toBeNull();
    expect(detailShell?.querySelector(".backreference-usage-section")).toBe(usage);
    const trigger = within(usage).getByRole("button", { name: /2.*关系实例/ });
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("link", { name: /前往North A/ })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/relations/north_a__belongs_to__north?focus_path=target_node_key&kind=relation",
    );
    expect(within(dialog).getByRole("link", { name: /前往North B/ })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/relations/north_b__belongs_to__north?focus_path=target_node_key&kind=relation",
    );
  });

  it("does not show a reverse-reference requirement when the selected object has no consumers", async () => {
    renderEditor("/scenarios/scenario-1/edit/world-entities/north?kind=node");
    fireEvent.click(await screen.findByRole("button", { name: "显示检查器" }));
    await waitFor(() => expect(api.analyzeWorkingCopyReferences).toHaveBeenCalled());
    expect(document.querySelector(".backreference-usage-section")).toBeNull();
  });

  it("maps conditional requiredness and keeps schema-required empty reference sets valid", async () => {
    const requirednessDraft = structuredClone(draft);
    (requirednessDraft.definition_document as Record<string, unknown>).rules = [{ key: "state_rule", phase: "PREFLIGHT", trigger: "STATE", priority: 1, effects: [{ kind: "EMIT_OUTCOME", outcome_code: "READY" }] }];
    vi.mocked(api.draft).mockResolvedValue(requirednessDraft);
    renderEditor("/scenarios/scenario-1/edit/rules/state_rule?kind=rule");

    await waitFor(() => expect(document.querySelector('[data-field-path="rule.state_rule.trigger"]')).toBeInTheDocument());
    const actionReference = document.querySelector('[data-field-path="rule.state_rule.action_key"]');
    expect(actionReference).toBeNull();
    fireEvent.change(document.querySelector('[data-field-path="rule.state_rule.trigger"] select') as HTMLSelectElement, { target: { value: "ACTION" } });
    const activeActionReference = document.querySelector('[data-field-path="rule.state_rule.action_key"]');
    expect(activeActionReference?.querySelector(".required-marker")).toBeInTheDocument();
    expect(activeActionReference?.querySelector('[role="alert"]')).toBeInTheDocument();

    cleanup();
    const actorDraft = structuredClone(draft);
    (actorDraft.definition_document as Record<string, unknown>).actors = { roles: [{ key: "medic", name: "Medic", capabilities: ["PLAN"] }], actor_profiles: [{ key: "medic_actor", name: "Medic", role_key: "medic", persona: "A field medic", allowed_action_keys: [] }] };
    vi.mocked(api.draft).mockResolvedValue(actorDraft);
    renderEditor("/scenarios/scenario-1/edit/actors/medic_actor?kind=actor");
    await waitFor(() => expect(document.querySelector('[data-field-path$="allowed_action_keys"]')).toBeInTheDocument());
    const allowedActions = document.querySelector('[data-field-path$="allowed_action_keys"]');
    expect(allowedActions?.querySelector(".required-marker")).toBeNull();
    expect(allowedActions?.querySelector('[role="alert"]')).toBeNull();
    expect(allowedActions?.querySelector('[role="alert"]')).toBeNull();
  });

  it("keeps defaulted Action behavior optional and activates SUPPLY_POWER requirements only for that variant", async () => {
    const actionDraft = structuredClone(draft);
    const actionDocument = actionDraft.definition_document as Record<string, unknown>;
    actionDocument.interactions = [{ key: "inspect", name: "Inspect" }];
    actionDocument.actions = [{ key: "repair", name: "Repair", required_interaction_key: "inspect", execution_mode: "IMMEDIATE", behavior: "RULE", allowed_actor_capabilities: ["PLAN"], expected_outcomes: [{ code: "DONE", name: "Done", success: true }], parameters: [], operation_bindings: [], target_actor_roles: [], planning: { terminal_effects: [], supporting_effects: [], success_outcome_codes: [], wait_success_outcome_codes: [], hints: [] } }];
    vi.mocked(api.draft).mockResolvedValue(actionDraft);
    renderEditor("/scenarios/scenario-1/edit/actions/repair?kind=action");
    await waitFor(() => expect(document.querySelector('[data-field-path="action.repair.behavior"]')).toBeInTheDocument());
    const behaviorField = document.querySelector('[data-field-path="action.repair.behavior"]');
    expect(behaviorField?.querySelector(".required-marker")).toBeNull();
    expect(behaviorField?.querySelector('[role="alert"]')).toBeNull();

    const behaviorSelect = behaviorField?.querySelector<HTMLSelectElement>("select");
    expect(behaviorSelect).not.toBeNull();
    if (!behaviorSelect) throw new Error("Expected Action behavior selector.");
    fireEvent.change(behaviorSelect, { target: { value: "SUPPLY_POWER" } });
    const sourceRelationFields = document.querySelectorAll('[data-field-path="action.repair.source_relation_type_key"]');
    expect(sourceRelationFields).toHaveLength(1);
    sourceRelationFields.forEach((field) => {
      expect(field.querySelector(".required-marker")).toBeInTheDocument();
      expect(field.querySelector('[role="alert"]')).toHaveTextContent("此项为必填内容。");
    });
    expect(document.querySelector(".action-semantic-fields")).toBeInTheDocument();
    expect(document.querySelectorAll(".required-marker").length).toBeGreaterThan(0);

    fireEvent.change(behaviorSelect, { target: { value: "RULE" } });
    sourceRelationFields.forEach((field) => {
      expect(field.querySelector(".required-marker")).toBeNull();
      expect(field.querySelector('[role="alert"]')).toBeNull();
    });
  });

  it("preserves a dirty Working Copy when preview remediation navigates to a missing canonical owner", async () => {
    vi.mocked(api.initializationPreview).mockRejectedValue(new ApiError("cannot preview", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "value_error", loc: [], msg: "Value error, Node north type references unknown key missing_type" }],
    }));
    renderEditor("/scenarios/scenario-1/edit/world-entities/north?kind=node");

    const name = await screen.findByDisplayValue("North");
    fireEvent.change(name, { target: { value: "North, revised" } });
    expect(await screen.findByText("有未保存修改")).toBeInTheDocument();
    expect(await screen.findByText(/节点类型：此字段引用的节点类型/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往节点类型" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/node-types");

    fireEvent.click(screen.getByRole("link", { name: "前往节点类型" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1/edit/node-types"));
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "世界实体" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/scenarios/scenario-1/edit/world-entities"));
    fireEvent.click(await screen.findByRole("link", { name: /North, revised/ }));
    expect(await screen.findByDisplayValue("North, revised")).toBeInTheDocument();
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("keeps an unsaved new Node across editor sections and can delete only that Node", async () => {
    renderEditor("/scenarios/scenario-1/edit/world-entities");
    fireEvent.click(await screen.findByRole("button", { name: /节点/ }));
    const cancelledDialog = screen.getByRole("dialog", { name: /创建/ });
    expect(within(cancelledDialog).getByLabelText(/\u7a33\u5b9a\u952e/)).toHaveValue("");
    expect(within(cancelledDialog).queryByLabelText(/Display name|Node type/)).not.toBeInTheDocument();
    fireEvent.change(within(cancelledDialog).getByLabelText(/\u7a33\u5b9a\u952e/), { target: { value: "north" } });
    fireEvent.click(within(cancelledDialog).getByRole("button", { name: "创建" }));
    expect(within(cancelledDialog).getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
    fireEvent.click(within(cancelledDialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog", { name: /创建/ })).not.toBeInTheDocument();
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /节点/ }));
    const createDialog = screen.getByRole("dialog", { name: /创建/ });
    fireEvent.change(within(createDialog).getByLabelText(/\u7a33\u5b9a\u952e/), { target: { value: "unsaved_node" } });
    fireEvent.click(within(createDialog).getByRole("button", { name: "创建" }));
    expect(await screen.findByText("unsaved_node", { selector: ".object-list-item span" })).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="node.unsaved_node.name"] .required-marker')).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="node.unsaved_node.name"] [role="alert"]')).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="node.unsaved_node.node_type_key"] .required-marker')).toBeInTheDocument();
    expect(document.querySelector('[data-field-path="node.unsaved_node.node_type_key"] [role="alert"]')).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "＋ 添加事实" })).toHaveLength(1);
    expect(screen.queryByText("接下来可以")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往初始化" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "前往关系实例" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "配置检查" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/configuration-check"));
    fireEvent.click(screen.getByRole("link", { name: "初始化" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/initialization"));
    fireEvent.click(screen.getByRole("link", { name: "规划指引" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/planning-instructions"));
    expect(screen.queryByRole("dialog", { name: "放弃当前修改？" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "世界实体" }));
    await waitFor(() => expect(screen.getByText("unsaved_node", { selector: ".object-list-item span" })).toBeInTheDocument());
    fireEvent.click(screen.getByText("unsaved_node", { selector: ".object-list-item span" }));
    expect(document.querySelector('[data-field-path="node.unsaved_node.name"] input')).toHaveValue("");

    vi.mocked(api.transformWorkingCopy).mockImplementationOnce(async (_id, _revision, document) => {
      const definition = structuredClone(document) as typeof draft.definition_document;
      definition.world.nodes = definition.world.nodes.filter((node) => node.key !== "unsaved_node");
      return { scenario_id: "scenario-1", base_revision: 1, source: "WORKING_COPY", references: [], definition_document: definition };
    });
    fireEvent.click(screen.getByRole("button", { name: "删除节点" }));
    const dialog = await screen.findByRole("dialog", { name: /unsaved_node/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(screen.queryByText("unsaved_node", { selector: ".object-list-item span" })).not.toBeInTheDocument());
    expect(screen.getByText("North", { selector: ".object-list-item span" })).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("allows an incomplete Node Draft and links its required Node type field to the canonical owner", async () => {
    const noTypes = structuredClone(draft);
    noTypes.definition_document.world.node_types = [];
    vi.mocked(api.draft).mockResolvedValue(noTypes);
    renderEditor("/scenarios/scenario-1/edit/world-entities");
    fireEvent.click(await screen.findByRole("button", { name: "＋ 节点" }));
    const dialog = screen.getByRole("dialog", { name: /创建/ });
    expect(within(dialog).queryByRole("combobox", { name: /Node type/ })).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/\u7a33\u5b9a\u952e/), { target: { value: "new_node" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    const nodeType = await screen.findByRole("combobox", { name: /节点类型/ });
    expect(nodeType).toHaveValue("");
    expect(document.querySelector('[data-field-path="node.new_node.node_type_key"] [role="alert"]')).toBeInTheDocument();
    const ownerLink = screen.getByRole("link", { name: "前往节点类型" });
    expect(ownerLink).toHaveAttribute("href", "/scenarios/scenario-1/edit/node-types");
    fireEvent.click(ownerLink);
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/node-types"));
    expect(screen.queryByRole("dialog", { name: /创建/ })).not.toBeInTheDocument();
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("creates an Actor from its stable key, then marks display fields required in its detail owner", async () => {
    const actorDraft = structuredClone(draft);
    (actorDraft.definition_document as Record<string, unknown>).actors = { roles: [], actor_profiles: [] };
    vi.mocked(api.draft).mockResolvedValue(actorDraft);
    renderEditor("/scenarios/scenario-1/edit/actors");
    fireEvent.click(await screen.findByRole("button", { name: "＋ 参与者" }));
    const dialog = screen.getByRole("dialog", { name: "创建参与者" });
    expect(within(dialog).getByLabelText(/\u7a33\u5b9a\u952e/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/Display name|Role|Persona|Initial Node/)).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/\u7a33\u5b9a\u952e/), { target: { value: "field_agent" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));

    expect(await screen.findByText("field_agent", { selector: ".object-list-item span" })).toBeInTheDocument();
    for (const path of ["actor.field_agent.name", "actor.field_agent.role_key", "actor.field_agent.persona"]) {
      expect(document.querySelector(`[data-field-path="${path}"] .required-marker`)).toBeInTheDocument();
      expect(document.querySelector(`[data-field-path="${path}"] [role="alert"]`)).toBeInTheDocument();
    }
    expect(screen.getByRole("region", { name: "开局配置只读预览" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往初始化" })).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("preserves a dirty Working Copy while navigating from Configuration Check to a canonical owner", async () => {
    vi.mocked(api.completeness).mockResolvedValue({
      scenario_id: "scenario-1",
      base_revision: 1,
      items: [{
        key: "node:north:node-type",
        title: "Choose a Node type",
        level: "INCOMPLETE_REQUIRED",
        dependency_kind: "PUBLISH_REQUIRED",
        message: "Create or choose the NodeType in its owner.",
        path: "world.nodes.north.node_type_key",
        locator: { object_kind: "node", object_key: "north", field_path: "node_type_key" },
        action: "CREATE",
      }],
      required_missing: 1,
      recommended_missing: 0,
      validation_issue_count: 0,
      reference_edge_count: 0,
    });
    renderEditor("/scenarios/scenario-1/edit/overview");
    fireEvent.change(await screen.findByDisplayValue("Test Scenario"), { target: { value: "Unsaved Scenario Name" } });
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "配置检查" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/configuration-check"));
    fireEvent.click(await screen.findByRole("link", { name: "前往节点类型" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/node-types"));
    fireEvent.click(screen.getByRole("link", { name: "概览" }));

    expect(await screen.findByDisplayValue("Unsaved Scenario Name")).toBeInTheDocument();
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });

  it("projects current validation and Initialization issues globally and focuses the exact Node field without saving", async () => {
    vi.mocked(api.completeness).mockResolvedValue({
      scenario_id: "scenario-1",
      base_revision: 1,
      items: [{
        key: "node:north_a:node-type",
        title: "配置节点「North A」的节点类型",
        level: "INCOMPLETE_REQUIRED",
        dependency_kind: "HARD_REQUIRED",
        message: "节点必须关联一个现有节点类型。",
        path: "world.nodes.north_a.node_type_key",
        locator: { object_kind: "node", object_key: "north_a", field_path: "node_type_key" },
        reference_locator: { object_kind: "node_type", object_key: null, field_path: null },
        reference_owner: "node_type",
        action: "OPEN",
      }],
      validation_issues: [{
        severity: "ERROR", code: "SCENARIO_DOCUMENT_SCHEMA_INVALID", path: "world.nodes.1.node_type_key",
        message: "Field required", type: "missing",
        locator: { object_kind: "node", object_key: "north_a", field_path: "node_type_key" },
      }],
      required_missing: 1,
      recommended_missing: 0,
      validation_issue_count: 1,
      reference_edge_count: 0,
    });
    vi.mocked(api.initializationPreview).mockRejectedValue(new ApiError("invalid", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{
        canonical_owner: "node", field_path: "initial_access",
        locator: { object_kind: "node", object_key: "north_a", field_path: "initial_access" },
        type: "missing", msg: "Field required",
      }],
    }));
    renderEditor("/scenarios/scenario-1/edit/world-entities/north_a?kind=node");
    fireEvent.change(await screen.findByDisplayValue("North A"), { target: { value: "Unsaved North A" } });
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "配置检查" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/configuration-check"));
    expect(screen.getByText("当前工作副本有 2 项待处理配置")).toBeInTheDocument();
    expect(screen.getByText("请填写节点类型。")).toBeInTheDocument();
    expect(screen.getByText("还需要填写初始访问状态。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "定位到节点类型" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/edit/world-entities/north_a"));
    const nodeType = document.querySelector<HTMLSelectElement>('[data-field-path="node.north_a.node_type_key"] select');
    expect(nodeType).toHaveFocus();
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    expect(api.initializationPreview).toHaveBeenCalled();
    expect(api.saveDraft).not.toHaveBeenCalled();
    expect(api.validateDraft).not.toHaveBeenCalled();
  });

  it("creates a Relation only after its composite identity is selected", async () => {
    const relationDraft = structuredClone(draft);
    Object.assign(relationDraft.definition_document.world, { relation_types: [{ key: "belongs_to", name: "Located in" }] });
    vi.mocked(api.draft).mockResolvedValue(relationDraft);
    renderEditor("/scenarios/scenario-1/edit/relations");
    fireEvent.click(await screen.findByRole("button", { name: /关系实例/ }));
    const dialog = screen.getByRole("dialog", { name: /创建/ });
    const source = within(dialog).getByRole("combobox", { name: /\u6765\u6e90\u8282\u70b9/ });
    const relationType = within(dialog).getByRole("combobox", { name: /\u5173\u7cfb\u7c7b\u578b/ });
    const target = within(dialog).getByRole("combobox", { name: /\u76ee\u6807\u8282\u70b9/ });
    expect(source).toHaveValue("");
    expect(relationType).toHaveValue("");
    expect(target).toHaveValue("");
    fireEvent.change(source, { target: { value: "north" } });
    fireEvent.change(relationType, { target: { value: "belongs_to" } });
    fireEvent.change(target, { target: { value: "north_a" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    await waitFor(() => expect(screen.getByTestId("editor-location")).toHaveTextContent("/relations/north__belongs_to__north_a"));
    expect(screen.getAllByText("north__belongs_to__north_a", { selector: "output.stable-identity-value" }).length).toBeGreaterThan(0);
    expect(screen.getByText("North → Located in → North A")).toBeInTheDocument();
    expect(screen.getAllByText("north__belongs_to__north_a", { selector: "output.stable-identity-value" })).toHaveLength(2);
    expect(screen.queryByLabelText(/\u6765\u6e90\u8282\u70b9/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重命名稳定键" })).not.toBeInTheDocument();
    expect(screen.queryByText("new_relation")).not.toBeInTheDocument();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
