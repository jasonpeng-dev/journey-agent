import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InitializationWorkspace } from "./components/InitializationWorkspace";
import { ApiError } from "./api";
import type { JsonObject } from "./editor";
import type { InitializationPreview } from "./types";

const document: JsonObject = {
  initialization: { start_node_key: "room", primary_actor_key: "medic", resource_pools: [], region_resource_knowledge: [] },
  world: {
    node_types: [{ key: "room_type", name: "房间" }],
    nodes: [{ key: "room", name: "诊疗室", node_type_key: "room_type", initial_access: "AVAILABLE", initial_visibility: "KNOWN", facts: [{ key: "ready", name: "准备完成", value_type: "BOOLEAN", initial_value: false, initial_visibility: "KNOWN" }] }],
    relations: [], resources: [],
  },
  actors: { roles: [{ key: "medic_role", name: "医护" }], actor_profiles: [{ key: "medic", name: "值班医护", role_key: "medic_role", initial_node_key: "room" }] },
  derived_states: [{ key: "readiness", name: "整体准备度", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [{ kind: "FACT", node_key: "room", fact_key: "ready", accepted_values: [true] }] }],
};

const preview: InitializationPreview = {
  revision: 1,
  parity: { published: true, initialization_changes: ["node:room:initial_access"], design_changes: [] },
  projection: {
    summary: { nodes: 1, actors: 1, resource_pools: 0, relations: 0, derived_states: 1, warnings: 0 },
    findings: [
      { identity: "node:room:initial_access", label: "Room access", canonical_path: "world.nodes[].initial_access", owner: "INITIALIZATION_ONLY", source: "EXPLICIT", severity: "INFO", value: "AVAILABLE", locator: { section: "world-entities", object_kind: "node", object_key: "room", field_path: "initial_access" }, message: "" },
    ],
    domains: [
      { id: "basic", label: "Basic configuration", groups: [{ id: "entry", label: "Bootstrap entry", items: [{ id: "bootstrap-entry", label: "Start node & primary actor", locator: { section: "initialization", object_kind: null, object_key: null, field_path: null }, field_ids: [], readonly: false, context: {} }] }] },
      { id: "nodes", label: "Nodes", groups: [{ id: "node-type:room_type", label: "房间", items: [{ id: "node:room", label: "诊疗室", locator: { section: "world-entities", object_kind: "node", object_key: "room", field_path: null }, field_ids: ["node:room:initial_access"], readonly: false, context: { key: "room", type: "房间" } }] }] },
      { id: "actors", label: "Actors", groups: [] },
      { id: "resources", label: "Resources", groups: [] },
      { id: "relations", label: "Relations", groups: [] },
      { id: "derived", label: "Derived state preview", groups: [{ id: "derived-states", label: "Derived states", items: [{ id: "derived:readiness", label: "整体准备度", locator: { section: "derived-states", object_kind: "derived_state", object_key: "readiness", field_path: null }, field_ids: [], readonly: true, context: { truth: "UNAVAILABLE", knowledge: "UNKNOWN", dependencies: 1 } }] }] },
    ],
  },
};

const resourceDocument: JsonObject = {
  initialization: {
    resource_pools: [
      { pool_key: "west_general_stock", resource_key: "general_parts", region_key: "west_region", facility_key: null, quantity: 10, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false },
      { pool_key: "unresolved_pool", resource_key: "missing_resource", region_key: "missing_region", facility_key: null, quantity: 1, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false },
    ],
    region_resource_knowledge: [{ region_key: "west_region", resource_inventory_visibility: "VISIBLE", resource_survey_completed: true }],
  },
  world: {
    nodes: [{ key: "west_region", name: "西部物流区", node_type_key: "region" }],
    resources: [{ key: "general_parts", name: "通用工程部件" }],
  },
};

const resourcePreview: InitializationPreview = {
  ...preview,
  projection: {
    ...preview.projection,
    domains: preview.projection.domains.map((domain) => domain.id !== "resources" ? domain : {
      ...domain,
      groups: [
        {
          id: "resource-pools",
          label: "Resource pools",
          items: [
            { id: "pool:west_general_stock:general_parts:west_region", label: "Resource pool · west_general_stock", locator: { section: "initialization", object_kind: "resource_pool", object_key: "west_general_stock", field_path: "initialization.resource_pools" }, field_ids: [], readonly: false, context: { pool_key: "west_general_stock", resource_key: "general_parts", region_key: "west_region", facility_key: null } },
            { id: "pool:unresolved_pool:missing_resource:missing_region", label: "Resource pool · unresolved_pool", locator: { section: "initialization", object_kind: "resource_pool", object_key: "unresolved_pool", field_path: "initialization.resource_pools" }, field_ids: [], readonly: false, context: { pool_key: "unresolved_pool", resource_key: "missing_resource", region_key: "missing_region", facility_key: null } },
          ],
        },
        {
          id: "region-resource-knowledge",
          label: "Region resource knowledge",
          items: [{ id: "region-knowledge:west_region", label: "Region resource knowledge · west_region", locator: { section: "initialization", object_kind: "region_resource_knowledge", object_key: "west_region", field_path: "initialization.region_resource_knowledge" }, field_ids: [], readonly: false, context: { region_key: "west_region" } }],
        },
        { id: "compatibility-resources", label: "Compatibility sources", items: [] },
      ],
    }),
  },
};

afterEach(cleanup);

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="current-location">{location.pathname}{location.search}</output>;
}

function renderWorkspace(onChange = vi.fn(), path = "/initialization", workspaceDocument = document, workspacePreview = preview) {
  render(<MemoryRouter initialEntries={[path]}><InitializationWorkspace document={workspaceDocument} preview={workspacePreview} loading={false} error={null} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
  return onChange;
}

function documentWithFacts(facts: JsonObject[]): JsonObject {
  const copy = structuredClone(document) as JsonObject;
  const world = copy.world as JsonObject;
  const nodes = world.nodes as JsonObject[];
  nodes[0] = { ...nodes[0], facts };
  return copy;
}

describe("InitializationWorkspace", () => {
  it("starts with four panels, no selection, and only the domain choices populated", () => {
    renderWorkspace();
    const breadcrumb = screen.getByRole("navigation", { name: "初始化层级" });
    expect(breadcrumb.closest("header")).toHaveClass("initialization-workspace-header");
    expect(screen.getByText("配置当前版本的开局状态")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "类别" })).toBeInTheDocument();
    const panels = ["类别", "分类", "对象", "初始化配置"].map((name) => screen.getByRole("region", { name }));
    expect(panels).toHaveLength(4);
    expect(within(panels[0]).getByRole("button", { name: /节点/ })).toBeInTheDocument();
    expect(within(panels[1]).getByText("请选择一个类别")).toBeInTheDocument();
    expect(within(panels[2]).getByText("请选择一个分类")).toBeInTheDocument();
    expect(within(panels[3]).getByText("请选择一个对象以配置初始化状态")).toBeInTheDocument();
    expect(panels[0].querySelector('[aria-current="true"]')).toBeNull();
    expect(screen.queryByRole("region", { name: "初始化概览" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看问题/ })).not.toBeInTheDocument();
  });

  it("progressively populates panels without dirtying the document", () => {
    const onChange = renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: /节点/ }));
    expect(screen.getByRole("heading", { name: "节点类型" })).toBeInTheDocument();
    expect(screen.getByText("请选择一个分类")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /房间/ }));
    expect(screen.getByRole("button", { name: /诊疗室/ })).toBeInTheDocument();
    expect(screen.getByText("请选择一个对象以配置初始化状态")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /诊疗室/ }));
    expect(screen.getByRole("region", { name: "初始化配置" })).toHaveClass("mobile-active");
    expect(screen.getByText("真实初始状态")).toBeInTheDocument();
    expect(screen.getByText("玩家初始知识")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("uses the generic Other overview taxonomy for Derived State", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=derived");
    const category = screen.getByRole("region", { name: "类别" });
    fireEvent.click(within(category).getByRole("button", { name: /其他/ }));
    const overview = screen.getByRole("region", { name: "分类" });
    expect(within(overview).getByRole("heading", { name: "其他概览" })).toBeInTheDocument();
    const derivedGroup = within(overview).getByRole("button", { name: /派生状态/ });
    expect(derivedGroup).toHaveTextContent("1");
    fireEvent.click(derivedGroup);
    expect(screen.getByRole("heading", { name: "具体派生状态" })).toBeInTheDocument();
  });

  it("uses the same Initialization field wrapper for Boolean and enum selects", () => {
    const typedDocument = documentWithFacts([
      { key: "ready", name: "准备完成", value_type: "BOOLEAN", initial_value: false, initial_visibility: "KNOWN" },
      { key: "power_state", name: "供电状态", value_type: "ENUM", allowed_values: ["AVAILABLE", "UNAVAILABLE"], initial_value: "AVAILABLE", initial_visibility: "KNOWN" },
      { key: "alert_level", name: "警报等级", value_type: "ENUM", allowed_values: ["RED_ALERT", "BLUE_ALERT"], initial_value: "RED_ALERT", initial_visibility: "KNOWN" },
    ]);
    const onChange = vi.fn();
    const rendered = render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom"]}><InitializationWorkspace document={typedDocument} preview={preview} loading={false} error={null} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    const booleanField = rendered.container.querySelector('[data-field-path="world.nodes.room.facts.ready.initial_value"] select') as HTMLSelectElement;
    const factSection = booleanField.closest(".initialization-fact");
    expect(factSection).not.toBeNull();
    const booleanFieldContainer = booleanField.closest(".initialization-field");
    expect(booleanFieldContainer).not.toBeNull();
    const booleanHeading = booleanFieldContainer?.querySelector(":scope > span");
    const powerField = rendered.container.querySelector('[data-field-path="world.nodes.room.facts.power_state.initial_value"] select') as HTMLSelectElement;
    const powerFieldContainer = powerField.closest(".initialization-field");
    const powerHeading = powerFieldContainer?.querySelector(":scope > span");
    expect(booleanHeading?.firstChild?.textContent).toBe("真实值 · 初始值");
    expect(booleanHeading?.children[0]).toHaveClass("required-marker");
    expect(booleanHeading?.children[1]).toHaveClass("source-badge");
    expect(booleanHeading?.children[1]).toHaveTextContent("显式配置");
    expect(powerHeading?.firstChild?.textContent).toBe("真实值 · 初始值");
    expect(powerHeading?.children[0]).toHaveClass("required-marker");
    expect(powerHeading?.children[1]).toHaveClass("source-badge");
    expect(booleanFieldContainer?.classList.contains("initialization-boolean-field")).toBe(false);
    expect(booleanField).toHaveValue("false");
    expect(Array.from(booleanField.options).map((option) => option.textContent)).toEqual(["是", "否"]);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(within(factSection as HTMLElement).getByDisplayValue("已知")).toBeInTheDocument();
    expect(powerFieldContainer).not.toBeNull();
    expect(powerField.className).toBe(booleanField.className);

    fireEvent.change(booleanField, { target: { value: "true" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ world: expect.objectContaining({ nodes: [expect.objectContaining({ facts: expect.arrayContaining([expect.objectContaining({ initial_value: true })]) })] }) }));
  });

  it("uses authored enum value labels in Initialization while preserving wire tokens", () => {
    const typedDocument = documentWithFacts([
      {
        key: "power_state",
        name: "供电状态",
        value_type: "ENUM",
        allowed_values: ["AVAILABLE", "UNAVAILABLE"],
        value_labels: [
          { value: "AVAILABLE", label: "已供电" },
          { value: "UNAVAILABLE", label: "未供电" },
        ],
        initial_value: "AVAILABLE",
        initial_visibility: "KNOWN",
      },
    ]);
    const onChange = vi.fn();
    const rendered = render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom"]}><InitializationWorkspace document={typedDocument} preview={preview} loading={false} error={null} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    const powerField = rendered.container.querySelector('[data-field-path="world.nodes.room.facts.power_state.initial_value"] select') as HTMLSelectElement;

    expect(Array.from(powerField.options).map((option) => option.textContent)).toEqual(["已供电", "未供电"]);
    fireEvent.change(powerField, { target: { value: "choice:1" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ world: expect.objectContaining({ nodes: [expect.objectContaining({ facts: [expect.objectContaining({ initial_value: "UNAVAILABLE" })] })] }) }));
  });

  it("shows unlabeled binary authored enums as 是/否 and keeps their wire tokens", () => {
    const typedDocument = documentWithFacts([
      { key: "power_state", name: "供电状态", value_type: "ENUM", allowed_values: ["AVAILABLE", "UNAVAILABLE"], initial_value: "AVAILABLE", initial_visibility: "KNOWN" },
      { key: "alert_level", name: "警报等级", value_type: "ENUM", allowed_values: ["RED_ALERT", "BLUE_ALERT"], initial_value: "RED_ALERT", initial_visibility: "KNOWN" },
    ]);
    const onChange = vi.fn();
    const rendered = render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom"]}><InitializationWorkspace document={typedDocument} preview={preview} loading={false} error={null} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    const powerField = rendered.container.querySelector('[data-field-path="world.nodes.room.facts.power_state.initial_value"] select') as HTMLSelectElement;
    const alertField = rendered.container.querySelector('[data-field-path="world.nodes.room.facts.alert_level.initial_value"] select') as HTMLSelectElement;
    expect(Array.from(powerField.options).map((option) => option.textContent)).toEqual(["是", "否"]);
    expect(Array.from(alertField.options).map((option) => option.textContent)).toEqual(["RED_ALERT", "BLUE_ALERT"]);

    fireEvent.change(powerField, { target: { value: "choice:1" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ world: expect.objectContaining({ nodes: [expect.objectContaining({ facts: [expect.objectContaining({ initial_value: "UNAVAILABLE" }), expect.anything()] })] }) }));
  });

  it("clears every downstream selection when the domain changes", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom");
    expect(screen.getByRole("heading", { name: "诊疗室" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /参与者/ }));
    expect(screen.queryByRole("heading", { name: "诊疗室" })).not.toBeInTheDocument();
    expect(screen.getByText("请选择一个分类")).toBeInTheDocument();
    expect(screen.getByText("请选择一个对象以配置初始化状态")).toBeInTheDocument();
  });

  it("restores a valid deep link and safely normalizes a stale one", async () => {
    const { unmount } = render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom"]}><InitializationWorkspace document={document} preview={preview} loading={false} error={null} scenarioId="scenario" onChange={vi.fn()} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "诊疗室" })).toBeInTheDocument();
    unmount();
    renderWorkspace(vi.fn(), "/initialization?domain=nodes&group=stale&item=node%3Aroom");
    await waitFor(() => expect(screen.queryByRole("heading", { name: "诊疗室" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "节点类型" })).toBeInTheDocument();
    expect(screen.getByText("请选择一个分类")).toBeInTheDocument();
  });

  it("keeps overview closed by default and preserves counts, warnings, and parity", () => {
    renderWorkspace();
    const trigger = screen.getByRole("button", { name: "初始化概览" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    const overview = screen.getByRole("complementary", { name: "初始化概览" });
    expect(within(overview).getByText("节点")).toBeInTheDocument();
    expect(within(overview).getByText("警告")).toBeInTheDocument();
    expect(within(overview).getByText("开局变化")).toBeInTheDocument();
    expect(within(overview).getByText("设计变化")).toBeInTheDocument();
  });

  it("writes initialization edits directly to the canonical working document", () => {
    const onChange = renderWorkspace(vi.fn(), "/initialization?domain=nodes&item=node%3Aroom");
    const access = screen.getByLabelText(/节点访问状态/);
    fireEvent.change(access, { target: { value: "choice:1" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      world: expect.objectContaining({ nodes: [expect.objectContaining({ key: "room", initial_access: "LOCKED" })] }),
    }));
  });

  it("keeps required enum selects free of empty choices", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom");
    const access = screen.getByRole("combobox", { name: /节点访问状态/ }) as HTMLSelectElement;
    expect(access.value).toBe("choice:0");
    expect(Array.from(access.options).map((option) => option.textContent)).toEqual(["可用", "锁定"]);
    expect(Array.from(access.options).some((option) => option.value === "")).toBe(false);
  });

  it("writes a true nullable unset as null and a model default by removing the override", () => {
    const onChange = vi.fn();
    renderWorkspace(onChange, "/initialization?domain=resources&group=resource-pools&item=pool%3Awest_general_stock%3Ageneral_parts%3Awest_region", resourceDocument, resourcePreview);
    const region = screen.getByRole("combobox", { name: /所属区域/ });
    fireEvent.change(region, { target: { value: "__initialization_nullable_unset__" } });
    let updated = onChange.mock.calls.at(-1)?.[0] as JsonObject;
    let pool = (updated.initialization as JsonObject).resource_pools instanceof Array
      ? ((updated.initialization as JsonObject).resource_pools as JsonObject[]).find((candidate) => candidate.pool_key === "west_general_stock")
      : undefined;
    expect(pool).toHaveProperty("region_key", null);

    const visibility = screen.getByRole("combobox", { name: /可见性/ });
    fireEvent.change(visibility, { target: { value: "__initialization_model_default__" } });
    updated = onChange.mock.calls.at(-1)?.[0] as JsonObject;
    pool = (updated.initialization as JsonObject).resource_pools instanceof Array
      ? ((updated.initialization as JsonObject).resource_pools as JsonObject[]).find((candidate) => candidate.pool_key === "west_general_stock")
      : undefined;
    expect(pool).not.toHaveProperty("visibility");
    expect(resourceDocument.initialization).toEqual(expect.objectContaining({ resource_pools: expect.arrayContaining([expect.objectContaining({ pool_key: "west_general_stock", visibility: "VISIBLE" })]) }));
  });

  it("recovers a stale required reference without changing unrelated Working Copy data", () => {
    const onChange = vi.fn();
    renderWorkspace(onChange, "/initialization?domain=resources&group=resource-pools&item=pool%3Aunresolved_pool%3Amissing_resource%3Amissing_region", resourceDocument, resourcePreview);
    expect(screen.getAllByText("当前引用无效，请选择一个有效项。").length).toBeGreaterThan(0);
    const resource = screen.getByRole("combobox", { name: /资源定义/ });
    fireEvent.change(resource, { target: { value: "choice:0" } });
    const updated = onChange.mock.calls.at(-1)?.[0] as JsonObject;
    const pools = (updated.initialization as JsonObject).resource_pools as JsonObject[];
    expect(pools.find((candidate) => candidate.pool_key === "unresolved_pool")).toHaveProperty("resource_key", "general_parts");
    expect(resourceDocument.initialization).toEqual(expect.objectContaining({ resource_pools: expect.arrayContaining([expect.objectContaining({ pool_key: "unresolved_pool", resource_key: "missing_resource" })]) }));
  });

  it("keeps a newly-created incomplete Resource Pool selected while its reference identity is repaired", () => {
    const incomplete = structuredClone(resourceDocument) as JsonObject;
    const initialization = incomplete.initialization as JsonObject;
    initialization.resource_pools = [{ pool_key: "pending_pool", resource_key: "", region_key: null, facility_key: null, reserved_value: 0, visibility: "VISIBLE", availability: "AVAILABLE", survey_discoverable: false }];
    const error = new ApiError("invalid working copy", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "string_too_short", loc: ["initialization", "resource_pools", 0, "resource_key"], msg: "String should have at least 1 character" }],
    });
    const onChange = vi.fn();
    const rendered = render(<MemoryRouter initialEntries={["/initialization?domain=resources&group=resource-pools&item=pool%3Apending_pool"]}><InitializationWorkspace document={incomplete} preview={null} loading={false} error={error} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "pending_pool" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: /资源定义/ }), { target: { value: "choice:0" } });
    const updated = onChange.mock.calls.at(-1)?.[0] as JsonObject;
    expect((updated.initialization as JsonObject).resource_pools).toEqual([expect.objectContaining({ pool_key: "pending_pool", resource_key: "general_parts" })]);

    rendered.rerender(<MemoryRouter initialEntries={["/initialization?domain=resources&group=resource-pools&item=pool%3Apending_pool"]}><InitializationWorkspace document={updated} preview={null} loading={false} error={error} scenarioId="scenario" onChange={vi.fn()} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "通用工程部件" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /资源定义/ })).toHaveValue("choice:0");
  });

  it("keeps the workspace and a valid selected Resource Pool available while another object is invalid", () => {
    const error = new ApiError("invalid working copy", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "missing", loc: ["initialization", "resource_pools", 1, "resource_key"], msg: "Field required" }],
    });
    render(<MemoryRouter initialEntries={["/initialization?domain=resources&group=resource-pools&item=pool%3Awest_general_stock%3Ageneral_parts%3Awest_region"]}><InitializationWorkspace document={resourceDocument} preview={null} loading={false} error={error} scenarioId="scenario" onChange={vi.fn()} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    expect(screen.getByRole("region", { name: "初始化配置" })).toHaveTextContent("西部物流区 · 通用工程部件");
    expect(screen.getByRole("combobox", { name: /资源定义/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看问题（1）" })).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "待完善配置" })).not.toBeInTheDocument();
    expect(screen.getByTestId("initialization-four-panel-workspace").previousElementSibling).toHaveClass("initialization-workspace-header");
    fireEvent.click(screen.getByRole("button", { name: "查看问题（1）" }));
    const issueDialog = screen.getByRole("dialog", { name: "待完善配置" });
    expect(within(issueDialog).getByRole("link", { name: "前往" })).toHaveAttribute("href", "/scenarios/scenario/edit/initialization?domain=resources&group=resource-pools&item=pool%3Aunresolved_pool%3Amissing_resource%3Amissing_region&focus_path=initialization.resource_pools.unresolved_pool.resource_key");
  });

  it("renders an incomplete newly-created Node from the unsaved document and exposes its field recovery", () => {
    const incomplete = structuredClone(document) as JsonObject;
    const world = incomplete.world as JsonObject;
    world.nodes = [...(world.nodes as JsonObject[]), { key: "new_node", name: "", node_type_key: "", facts: [] }];
    const original = structuredClone(incomplete);
    const onChange = vi.fn();
    const error = new ApiError("invalid working copy", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "string_too_short", loc: ["world", "nodes", 1, "name"], msg: "String should have at least 1 character" }],
    });
    render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Aunassigned&item=node%3Anew_node"]}><InitializationWorkspace document={incomplete} preview={null} loading={false} error={error} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);

    expect(screen.getByRole("heading", { name: "new_node" })).toBeInTheDocument();
    const access = screen.getByRole("combobox", { name: /节点访问状态/ }) as HTMLSelectElement;
    expect(access).toHaveValue("__initialization_missing_required_value__");
    expect(access.selectedOptions[0]).toBeDisabled();
    expect(access.selectedOptions[0]).toHaveTextContent("尚未设置，请选择有效值");
    expect(Array.from(access.options).some((option) => option.value === "")).toBe(false);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "查看问题（1）" })).toBeInTheDocument();
    expect(screen.getByTestId("initialization-four-panel-workspace").previousElementSibling).toHaveClass("initialization-workspace-header");
    expect(screen.queryByText("world.nodes.1.name")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看问题（1）" }));
    const issueDialog = screen.getByRole("dialog", { name: "待完善配置" });
    expect(within(issueDialog).getByText("当前草稿还有 1 项需要处理。")).toBeInTheDocument();
    expect(within(issueDialog).getByRole("region", { name: "世界实体 · new_node" })).toBeInTheDocument();
    expect(within(issueDialog).getByText("显示名称")).toBeInTheDocument();
    expect(within(issueDialog).getByRole("link", { name: "前往" })).toHaveAttribute("href", "/scenarios/scenario/edit/world-entities/new_node?focus_path=name");
    expect(within(issueDialog).queryByText("String should have at least 1 character")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: /节点访问状态/ }), { target: { value: "choice:1" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ world: expect.objectContaining({ nodes: expect.arrayContaining([expect.objectContaining({ key: "new_node", initial_access: "LOCKED" })]) }) }));
    expect(incomplete).toEqual(original);
  });

  it("offers current field and canonical owner navigation for a missing cross-owner dependency", () => {
    const incomplete = structuredClone(document) as JsonObject;
    const world = incomplete.world as JsonObject;
    world.nodes = [...(world.nodes as JsonObject[]), { key: "new_node", name: "新节点", node_type_key: "missing_type", facts: [] }];
    const error = new ApiError("invalid working copy", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "value_error", loc: [], msg: "Value error, Node new_node type references unknown key missing_type" }],
    });
    render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Amissing_type&item=node%3Anew_node"]}><InitializationWorkspace document={incomplete} preview={null} loading={false} error={error} scenarioId="scenario" onChange={vi.fn()} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);

    expect(screen.getByRole("button", { name: "查看问题（1）" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看问题（1）" }));
    const issueDialog = screen.getByRole("dialog", { name: "待完善配置" });
    const group = within(issueDialog).getByRole("region", { name: "世界实体 · 新节点" });
    expect(within(group).getByText("节点类型")).toBeInTheDocument();
    expect(within(group).getByRole("link", { name: /^前往$/ })).toHaveAttribute("href", "/scenarios/scenario/edit/world-entities/new_node?focus_path=node_type_key");
    expect(within(group).getByRole("link", { name: "前往节点类型" })).toHaveAttribute("href", "/scenarios/scenario/edit/node-types");
  });

  it("keeps source badges and the full-definition handoff", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom");
    expect(screen.getAllByText("显式配置").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "前往完整定义" })).toHaveAttribute("href", "/scenarios/scenario/edit/world-entities/room");
  });

  it("renders derived Truth and Knowledge as typed readonly values with composition", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=derived&item=derived%3Areadiness");
    expect(screen.getAllByText("否").length).toBeGreaterThan(0);
    expect(screen.getByText("未知")).toBeInTheDocument();
    expect(screen.getByText("真实初始结果")).toBeInTheDocument();
    expect(screen.getByText("玩家可知结果")).toBeInTheDocument();
    expect(screen.queryByText("真实初始状态")).not.toBeInTheDocument();
    expect(screen.getByText("组成条件")).toBeInTheDocument();
    expect(screen.getByText("事实条件")).toBeInTheDocument();
    expect(screen.getByText("准备完成")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往完整定义" })).toHaveAttribute("href", "/scenarios/scenario/edit/derived-states/readiness");
    expect(screen.queryByText("UNAVAILABLE")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.getByText(/只读计算结果/)).toBeInTheDocument();
  });

  it("keeps evaluator-provided east truth/knowledge as 否 / 否", () => {
    const eastDocument = structuredClone(document) as JsonObject;
    eastDocument.derived_states = [{ key: "east_emergency_power_network", name: "东部应急供电网络", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] }];
    const eastPreview = structuredClone(preview) as InitializationPreview;
    const derivedDomain = eastPreview.projection.domains.find((candidate) => candidate.id === "derived");
    const derivedItem = derivedDomain?.groups[0]?.items[0];
    if (!derivedItem) throw new Error("derived fixture item missing");
    derivedItem.id = "derived:east_emergency_power_network";
    derivedItem.locator.object_key = "east_emergency_power_network";
    derivedItem.label = "东部应急供电网络";
    derivedItem.context = { truth: "UNAVAILABLE", knowledge: "UNAVAILABLE", dependencies: 4 };
    renderWorkspace(vi.fn(), "/initialization?domain=derived&item=derived%3Aeast_emergency_power_network", eastDocument, eastPreview);
    const truthField = screen.getByText("真实初始结果").closest(".initialization-readonly-field");
    const knowledgeField = screen.getByText("玩家可知结果").closest(".initialization-readonly-field");
    expect(truthField?.querySelector("output")).toHaveTextContent("否");
    expect(knowledgeField?.querySelector("output")).toHaveTextContent("否");
  });

  it("keeps evaluator-provided north truth/knowledge as 否 / 未知 when the fact is hidden", () => {
    const northDocument = structuredClone(document) as JsonObject;
    northDocument.derived_states = [{ key: "north_basic_engineering_support", name: "北部基础工程支援", value_type: "BOOLEAN", available_value: true, unavailable_value: false, dependencies: [] }];
    const northPreview = structuredClone(preview) as InitializationPreview;
    const derivedDomain = northPreview.projection.domains.find((candidate) => candidate.id === "derived");
    const derivedItem = derivedDomain?.groups[0]?.items[0];
    if (!derivedItem) throw new Error("derived fixture item missing");
    derivedItem.id = "derived:north_basic_engineering_support";
    derivedItem.locator.object_key = "north_basic_engineering_support";
    derivedItem.label = "北部基础工程支援";
    derivedItem.context = { truth: "UNAVAILABLE", knowledge: "UNKNOWN", dependencies: 1 };
    renderWorkspace(vi.fn(), "/initialization?domain=derived&item=derived%3Anorth_basic_engineering_support", northDocument, northPreview);
    const truthField = screen.getByText("真实初始结果").closest(".initialization-readonly-field");
    const knowledgeField = screen.getByText("玩家可知结果").closest(".initialization-readonly-field");
    expect(truthField?.querySelector("output")).toHaveTextContent("否");
    expect(knowledgeField?.querySelector("output")).toHaveTextContent("未知");
  });

  it("deduplicates identical knowledge gates without changing dependency semantics", () => {
    const gatedDocument = structuredClone(document) as JsonObject;
    const gate = { node_key: "room", fact_key: "ready", accepted_values: [true] };
    gatedDocument.derived_states = [{
      key: "gated_readiness",
      name: "门槛准备度",
      value_type: "BOOLEAN",
      available_value: true,
      unavailable_value: false,
      dependencies: [
        { kind: "FACT", node_key: "room", fact_key: "ready", accepted_values: [true], knowledge_gate: gate },
        { kind: "FACT", node_key: "room", fact_key: "ready", accepted_values: [true], knowledge_gate: gate },
        { kind: "FACT", node_key: "room", fact_key: "ready", accepted_values: [true], knowledge_gate: gate },
      ],
    }];
    const gatedPreview = structuredClone(preview) as InitializationPreview;
    const derivedDomain = gatedPreview.projection.domains.find((candidate) => candidate.id === "derived");
    const derivedItem = derivedDomain?.groups[0]?.items[0];
    if (!derivedItem) throw new Error("derived fixture item missing");
    derivedItem.id = "derived:gated_readiness";
    derivedItem.locator.object_key = "gated_readiness";
    derivedItem.label = "门槛准备度";
    derivedItem.context = { truth: "UNAVAILABLE", knowledge: "UNKNOWN", dependencies: 3 };
    renderWorkspace(vi.fn(), "/initialization?domain=derived&item=derived%3Agated_readiness", gatedDocument, gatedPreview);
    expect(screen.getByText("共同揭示条件")).toBeInTheDocument();
    expect(screen.getByText("影响条件")).toBeInTheDocument();
    expect(screen.getByText("3 项")).toBeInTheDocument();
    expect(screen.getAllByText("满足后，玩家/智能体才能获知以下条件；不影响该派生状态的真实计算。")).toHaveLength(1);
    expect(screen.queryByText("揭示条件", { exact: true })).not.toBeInTheDocument();
  });

  it("renders Fact, Resource, and Derived State dependencies as readonly field groups", () => {
    const fullDocument = structuredClone(document) as JsonObject;
    fullDocument.world = {
      ...(fullDocument.world as JsonObject),
      resources: [{ key: "parts", name: "应急部件" }],
    };
    fullDocument.initialization = {
      ...(fullDocument.initialization as JsonObject),
      resource_pools: [{ pool_key: "room_parts", resource_key: "parts", region_key: "room", quantity: 5, reserved_value: 0, availability: "AVAILABLE" }],
    };
    fullDocument.derived_states = [{
      key: "full_readiness",
      name: "完整准备度",
      value_type: "BOOLEAN",
      available_value: true,
      unavailable_value: false,
      dependencies: [
        { kind: "FACT", node_key: "room", fact_key: "ready", accepted_values: [true] },
        { kind: "RESOURCE_AT_LEAST", region_key: "room", resource_key: "parts", minimum: 3 },
        { kind: "DERIVED_STATE", derived_key: "full_readiness", accepted_values: [true] },
      ],
    }];
    const fullPreview = structuredClone(preview) as InitializationPreview;
    const derivedDomain = fullPreview.projection.domains.find((candidate) => candidate.id === "derived");
    const derivedItem = derivedDomain?.groups[0]?.items[0];
    if (!derivedItem) throw new Error("derived fixture item missing");
    derivedItem.id = "derived:full_readiness";
    derivedItem.locator.object_key = "full_readiness";
    derivedItem.label = "完整准备度";
    derivedItem.context = { truth: "UNAVAILABLE", knowledge: "UNKNOWN", dependencies: 3 };
    renderWorkspace(vi.fn(), "/initialization?domain=derived&item=derived%3Afull_readiness", fullDocument, fullPreview);
    expect(screen.getByText("事实条件")).toBeInTheDocument();
    expect(screen.getByText("资源条件")).toBeInTheDocument();
    expect(screen.getByText("派生状态条件")).toBeInTheDocument();
    expect(screen.getByText("真实初始数量")).toBeInTheDocument();
    expect(screen.getByText("应急部件")).toBeInTheDocument();
    expect(screen.getAllByText("完整准备度").length).toBeGreaterThan(0);
    const composition = screen.getByText("组成条件").closest("section");
    expect(composition?.querySelectorAll("input, select, textarea")).toHaveLength(0);
  });

  it("resolves Resource Pool labels from authored location and resource names", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=resources&group=resource-pools&item=pool%3Awest_general_stock%3Ageneral_parts%3Awest_region", resourceDocument, resourcePreview);
    expect(screen.getByRole("heading", { name: "西部物流区 · 通用工程部件" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "初始化层级" })).toHaveTextContent("类别/资源/资源池/西部物流区 · 通用工程部件");
    expect(screen.getAllByText("west_general_stock").length).toBeGreaterThan(0);
    expect(screen.getByText("pool:west_general_stock")).toBeInTheDocument();
    expect(screen.queryByText("资源池 · west_general_stock")).not.toBeInTheDocument();
  });

  it("falls back to the stable pool key and resolves Region Knowledge names without a prefix", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=resources&group=resource-pools&item=pool%3Aunresolved_pool%3Amissing_resource%3Amissing_region", resourceDocument, resourcePreview);
    expect(screen.getByRole("heading", { name: "unresolved_pool" })).toBeInTheDocument();
    expect(screen.getByText("pool:unresolved_pool")).toBeInTheDocument();
    expect(screen.queryByText("资源池 · unresolved_pool")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /区域资源知识/ }));
    fireEvent.click(screen.getByRole("button", { name: /西部物流区/ }));
    expect(screen.getByRole("heading", { name: "西部物流区" })).toBeInTheDocument();
    expect(screen.getAllByText("west_region").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "前往西部物流区" })).toHaveAttribute("href", "/scenarios/scenario/edit/world-entities/west_region");
    expect(screen.queryByText("区域资源知识 · west_region")).not.toBeInTheDocument();
  });

  it("creates a Resource Pool availability selector from an existing Fact without an implicit selection", () => {
    const availabilityDocument = structuredClone(resourceDocument) as JsonObject;
    const world = availabilityDocument.world as JsonObject;
    world.nodes = [{ key: "west_region", name: "West Region", facts: [{ key: "ready", name: "Ready", value_type: "BOOLEAN", allowed_values: [] }] }];
    const onChange = renderWorkspace(vi.fn(), "/initialization?domain=resources&group=resource-pools&item=pool%3Awest_general_stock%3Ageneral_parts%3Awest_region", availabilityDocument, resourcePreview);

    fireEvent.click(screen.getByRole("button", { name: "添加可用性要求" }));
    const dialog = screen.getByRole("dialog", { name: "添加可用性要求" });
    const factPicker = within(dialog).getByRole("combobox", { name: /事实/ }) as HTMLSelectElement;
    expect(factPicker.value).toBe("__identity_creation_unselected__");
    expect(Array.from(factPicker.options).map((option) => option.value)).toEqual(["__identity_creation_unselected__", "west_region.ready"]);
    expect(within(dialog).queryByRole("option", { name: "请选择" })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "添加可用性要求" }));
    const confirmedDialog = screen.getByRole("dialog", { name: "添加可用性要求" });
    fireEvent.change(within(confirmedDialog).getByRole("combobox", { name: /事实/ }), { target: { value: "west_region.ready" } });
    expect(within(confirmedDialog).queryByLabelText(/要求值/)).not.toBeInTheDocument();
    fireEvent.click(within(confirmedDialog).getByRole("button", { name: "创建" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      initialization: expect.objectContaining({ resource_pools: expect.arrayContaining([expect.objectContaining({ pool_key: "west_general_stock", availability_requirement: { node_key: "west_region", fact_key: "ready" } })]) }),
    }));

    const updatedDocument = onChange.mock.calls.at(-1)?.[0] as JsonObject;
    const controlledOnChange = vi.fn();
    cleanup();
    const rerendered = render(<MemoryRouter initialEntries={["/initialization?domain=resources&group=resource-pools&item=pool%3Awest_general_stock%3Ageneral_parts%3Awest_region"]}><InitializationWorkspace document={updatedDocument} preview={resourcePreview} loading={false} error={null} scenarioId="scenario" onChange={controlledOnChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    const requiredValueField = rerendered.container.querySelector('[data-field-path="initialization.resource_pools.west_general_stock.availability_requirement.value"]');
    expect(requiredValueField).toBeInTheDocument();
    expect(requiredValueField?.querySelector(".required-marker")).toBeInTheDocument();
    expect(requiredValueField?.querySelector('[role="alert"]')).toBeInTheDocument();
    const booleanRequirement = within(requiredValueField as HTMLElement).getByRole("combobox", { name: "要求值" });
    expect(within(requiredValueField as HTMLElement).queryByRole("radio")).not.toBeInTheDocument();
    fireEvent.change(booleanRequirement, { target: { value: "true" } });
    expect(controlledOnChange).toHaveBeenCalledWith(expect.objectContaining({
      initialization: expect.objectContaining({ resource_pools: expect.arrayContaining([expect.objectContaining({ pool_key: "west_general_stock", availability_requirement: { node_key: "west_region", fact_key: "ready", value: true } })]) }),
    }));
    rerendered.unmount();
  });

  it("routes an empty Resource Pool Fact picker to the Fact owner without mutation", () => {
    const noFactsDocument = structuredClone(resourceDocument) as JsonObject;
    const world = noFactsDocument.world as JsonObject;
    world.nodes = [{ key: "west_region", name: "West Region", facts: [] }];
    const onChange = renderWorkspace(vi.fn(), "/initialization?domain=resources&group=resource-pools&item=pool%3Awest_general_stock%3Ageneral_parts%3Awest_region", noFactsDocument, resourcePreview);
    fireEvent.click(screen.getByRole("button", { name: "添加可用性要求" }));
    const dialog = screen.getByRole("dialog", { name: "添加可用性要求" });
    const factPicker = within(dialog).getByRole("combobox", { name: /事实/ }) as HTMLSelectElement;
    expect(factPicker.value).toBe("__identity_creation_unselected__");
    expect(Array.from(factPicker.options).map((option) => option.value)).toEqual(["__identity_creation_unselected__"]);
    const ownerLink = within(dialog).getByRole("link", { name: "前往世界实体" });
    expect(ownerLink).toHaveAttribute("href", "/world-entities");
    fireEvent.click(ownerLink);
    expect(screen.queryByRole("dialog", { name: "添加可用性要求" })).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("groups structured issues in the shared dialog and focuses the exact Initialization field without changing the Working Copy", async () => {
      const incomplete = structuredClone(document) as JsonObject;
      const world = incomplete.world as JsonObject;
      const node = (world.nodes as JsonObject[])[0];
      delete node.initial_access;
      delete node.initial_visibility;
      (world.nodes as JsonObject[]).push({ key: "other_node", name: "其他节点", node_type_key: "room_type" });
      const original = structuredClone(incomplete);
      const onChange = vi.fn();
      const error = new ApiError("invalid working copy", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [
        { type: "missing", loc: ["world", "nodes", 0, "initial_access"], msg: "Field required" },
        { type: "missing", loc: ["world", "nodes", 0, "initial_visibility"], msg: "Field required" },
        { type: "missing", loc: ["world", "nodes", 1, "name"], msg: "Field required" },
      ],
      });
    render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom"]}><LocationProbe /><InitializationWorkspace document={incomplete} preview={null} loading={false} error={error} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);

      const trigger = screen.getByRole("button", { name: "查看问题（3）" });
    expect(screen.getByTestId("initialization-four-panel-workspace").previousElementSibling).toHaveClass("initialization-workspace-header");
    expect(screen.queryByRole("region", { name: "待完善配置" })).not.toBeInTheDocument();
    fireEvent.click(trigger);

      const dialog = screen.getByRole("dialog", { name: "待完善配置" });
      expect(dialog).toHaveAttribute("aria-modal", "true");
      expect(within(dialog).getByText("当前草稿还有 3 项需要处理。")).toBeInTheDocument();
      expect(dialog.querySelector(":scope > .fact-delete-dialog-body")).toBeInTheDocument();
    expect(dialog.querySelector(":scope > .fact-delete-dialog-body")).toHaveClass("fact-delete-dialog-body");
    const group = within(dialog).getByRole("region", { name: "世界实体 · 诊疗室" });
    expect(within(group).getByText("初始访问状态")).toBeInTheDocument();
      expect(within(group).getByText("初始可见性")).toBeInTheDocument();
      expect(within(group).getAllByRole("link", { name: /^前往$/ })).toHaveLength(2);
      const otherGroup = within(dialog).getByRole("region", { name: "世界实体 · 其他节点" });
      expect(within(otherGroup).getByText("显示名称")).toBeInTheDocument();
    expect(within(dialog).queryByText("world.nodes.0.initial_access")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Field required")).not.toBeInTheDocument();

    fireEvent.click(within(group).getAllByRole("link", { name: /^前往$/ })[0]);
    await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent("/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom&focus_path=world.nodes.room.initial_access"));
    expect(screen.queryByRole("dialog", { name: "待完善配置" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox", { name: /节点访问状态/ })).toHaveFocus());
    expect(screen.getByRole("combobox", { name: /节点访问状态/ }).closest("[data-field-path]")).toHaveClass("is-focus-highlighted");
    expect(incomplete).toEqual(original);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("uses the same issue destination for preview and dialog actions, and supports Escape and close", async () => {
    const error = new ApiError("invalid working copy", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "missing", loc: ["world", "nodes", 0, "initial_visibility"], msg: "Field required" }],
    });
    render(<MemoryRouter initialEntries={["/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom"]}><InitializationWorkspace document={document} preview={null} loading={false} error={error} scenarioId="scenario" onChange={vi.fn()} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
    const trigger = screen.getByRole("button", { name: "查看问题（1）" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "待完善配置" });
    expect(within(dialog).getByRole("link", { name: /^前往$/ })).toHaveAttribute("href", "/scenarios/scenario/edit/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom&focus_path=world.nodes.room.initial_visibility");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "待完善配置" })).not.toBeInTheDocument();
    fireEvent.click(trigger);
    const reopened = screen.getByRole("dialog", { name: "待完善配置" });
    fireEvent.click(within(reopened).getAllByRole("button", { name: "关闭" })[0]);
    expect(screen.queryByRole("dialog", { name: "待完善配置" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("marks exactly one drill-down panel active for narrow-screen CSS", () => {
    renderWorkspace();
    const panels = ["类别", "分类", "对象", "初始化配置"].map((name) => screen.getByRole("region", { name }));
    expect(panels.filter((panel) => panel.classList.contains("mobile-active"))).toEqual([panels[0]]);
  });
});
