import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InitializationWorkspace } from "./components/InitializationWorkspace";
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

function renderWorkspace(onChange = vi.fn(), path = "/initialization", workspaceDocument = document, workspacePreview = preview) {
  render(<MemoryRouter initialEntries={[path]}><InitializationWorkspace document={workspaceDocument} preview={workspacePreview} loading={false} error={null} scenarioId="scenario" onChange={onChange} onDeleteResourcePool={vi.fn()} /></MemoryRouter>);
  return onChange;
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
    fireEvent.change(access, { target: { value: "LOCKED" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      world: expect.objectContaining({ nodes: [expect.objectContaining({ key: "room", initial_access: "LOCKED" })] }),
    }));
  });

  it("keeps source badges and the full-definition handoff", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=nodes&group=node-type%3Aroom_type&item=node%3Aroom");
    expect(screen.getAllByText("显式配置").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "打开完整定义" })).toHaveAttribute("href", "/scenarios/scenario/edit/world-entities/room");
  });

  it("renders derived Truth and Knowledge as readonly preview values", () => {
    renderWorkspace(vi.fn(), "/initialization?domain=derived&item=derived%3Areadiness");
    expect(screen.getByText("不可用")).toBeInTheDocument();
    expect(screen.getByText("未知")).toBeInTheDocument();
    expect(screen.getByText(/只读计算结果/)).toBeInTheDocument();
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
    expect(screen.queryByText("区域资源知识 · west_region")).not.toBeInTheDocument();
  });

  it("marks exactly one drill-down panel active for narrow-screen CSS", () => {
    renderWorkspace();
    const panels = ["类别", "分类", "对象", "初始化配置"].map((name) => screen.getByRole("region", { name }));
    expect(panels.filter((panel) => panel.classList.contains("mobile-active"))).toEqual([panels[0]]);
  });
});
