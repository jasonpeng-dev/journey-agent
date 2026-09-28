import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DraftObject, JsonObject } from "../../editor";
import type { InitializationFinding, InitializationPreview } from "../../types";
import { ApiError } from "../../api";
import { InitializationReadonlyPreview } from "./InitializationReadonlyPreview";

const document: JsonObject = {
  world: {
    nodes: [
      { key: "north", name: "北区医院", node_type_key: "facility" },
      { key: "south", name: "南区医院", node_type_key: "facility" },
    ],
    relations: [{ source_node_key: "north", relation_type_key: "connects", target_node_key: "south" }],
    resources: [{ key: "general_parts", name: "通用工程部件" }],
  },
  actors: { actor_profiles: [{ key: "operator", name: "现场操作员", initial_node_key: "north" }] },
};

const findings: InitializationFinding[] = [
  { identity: "node:north:initial_access", label: "Node access", canonical_path: "world.nodes[].initial_access", owner: "INITIALIZATION_ONLY", source: "EXPLICIT", severity: "INFO", value: "AVAILABLE", locator: { section: "world-entities", object_kind: "node", object_key: "north", field_path: "initial_access" }, message: "" },
  { identity: "node:north:initial_visibility", label: "Node visibility", canonical_path: "world.nodes[].initial_visibility", owner: "INITIALIZATION_ONLY", source: "DEFAULT", severity: "INFO", value: "KNOWN", locator: { section: "world-entities", object_kind: "node", object_key: "north", field_path: "initial_visibility" }, message: "" },
  { identity: "fact:north:ready:initial_value", label: "Fact initial value", canonical_path: "world.nodes[].facts[].initial_value", owner: "INITIALIZATION_ONLY", source: "EXPLICIT", severity: "INFO", value: true, locator: { section: "world-entities", object_kind: "node", object_key: "north", field_path: "facts.ready.initial_value" }, message: "" },
  { identity: "fact:north:ready:initial_visibility", label: "Fact visibility", canonical_path: "world.nodes[].facts[].initial_visibility", owner: "INITIALIZATION_ONLY", source: "DEFAULT", severity: "INFO", value: "KNOWN", locator: { section: "world-entities", object_kind: "node", object_key: "north", field_path: "facts.ready.initial_visibility" }, message: "" },
  { identity: "actor:operator:initial_node_key", label: "Initial node", canonical_path: "actors.actor_profiles[].initial_node_key", owner: "INITIALIZATION_ONLY", source: "EXPLICIT", severity: "INFO", value: "north", locator: { section: "actors", object_kind: "actor", object_key: "operator", field_path: "initial_node_key" }, message: "" },
  { identity: "actor:operator:command_reachability", label: "Command reachability", canonical_path: "actors.actor_profiles[].command_reachability", owner: "INITIALIZATION_ONLY", source: "DEFAULT", severity: "INFO", value: "ONLINE", locator: { section: "actors", object_kind: "actor", object_key: "operator", field_path: "command_reachability" }, message: "" },
  { identity: "actor:operator:runtime_status", label: "Runtime status", canonical_path: "actors.actor_profiles[].runtime_status", owner: "SYSTEM", source: "ENGINE", severity: "INFO", value: "ACTIVE", locator: { section: "actors", object_kind: "actor", object_key: "operator", field_path: "runtime_status" }, message: "" },
  { identity: "entry:primary_actor", label: "Primary actor", canonical_path: "initialization.primary_actor_key", owner: "INITIALIZATION_ONLY", source: "EXPLICIT", severity: "INFO", value: "operator", locator: { section: "initialization", object_kind: "initialization", object_key: null, field_path: "primary_actor_key" }, message: "" },
  { identity: "relation:north__connects__south:initial_visibility", label: "Relation visibility", canonical_path: "world.relations[].initial_visibility", owner: "INITIALIZATION_ONLY", source: "DEFAULT", severity: "INFO", value: "VISIBLE", locator: { section: "relations", object_kind: "relation", object_key: "north__connects__south", field_path: "initial_visibility" }, message: "" },
  { identity: "pool:main_stock:general_parts:west", label: "Resource pool", canonical_path: "initialization.resource_pools[]", owner: "INITIALIZATION_ONLY", source: "EXPLICIT", severity: "INFO", value: { quantity: 10, reserved: 2, availability: "AVAILABLE", visibility: "VISIBLE" }, locator: { section: "initialization", object_kind: "resource_pool", object_key: "main_stock", field_path: "initialization.resource_pools" }, message: "" },
];

const projectionItem = (id: string, kind: string, key: string, fieldIds: string[], context: Record<string, unknown> = {}) => ({
  id,
  label: id,
  locator: { section: kind === "actor" ? "actors" : kind === "relation" ? "relations" : "world-entities", object_kind: kind, object_key: key, field_path: null },
  field_ids: fieldIds,
  readonly: false,
  context,
});

const preview: InitializationPreview = {
  revision: 7,
  parity: { published: false, initialization_changes: [], design_changes: [] },
  projection: {
    summary: { nodes: 1, actors: 1, resource_pools: 1, relations: 1, derived_states: 0, warnings: 0 },
    findings,
    domains: [
      { id: "nodes", label: "节点", groups: [{ id: "node-type:facility", label: "设施", items: [projectionItem("node:north", "node", "north", ["node:north:initial_access", "node:north:initial_visibility", "fact:north:ready:initial_value", "fact:north:ready:initial_visibility"])] }] },
      { id: "actors", label: "参与者", groups: [{ id: "role:operator", label: "参与者", items: [projectionItem("actor:operator", "actor", "operator", ["actor:operator:initial_node_key", "actor:operator:command_reachability", "actor:operator:runtime_status"])] }] },
      { id: "relations", label: "关系", groups: [{ id: "relation-type:connects", label: "关系类型", items: [projectionItem("relation:north__connects__south", "relation", "north__connects__south", ["relation:north__connects__south:initial_visibility"])] }] },
      { id: "resources", label: "资源", groups: [{ id: "resource-pools", label: "资源池", items: [{ ...projectionItem("pool:main_stock:general_parts:west", "resource_pool", "main_stock", ["pool:main_stock:general_parts:west"], { resource_key: "general_parts", pool_key: "main_stock" }), locator: { section: "initialization", object_kind: "resource_pool", object_key: "main_stock", field_path: "initialization.resource_pools" } }] }] },
    ],
  },
};

function entity(kind: DraftObject["kind"], key: string, name: string, value: JsonObject = {}): DraftObject {
  return { kind, key, name, value, path: [kind, key] };
}

function renderPreview(object: DraftObject, fact?: JsonObject) {
  return render(<MemoryRouter><InitializationReadonlyPreview entity={object} fact={fact} document={document} preview={preview} loading={false} scenarioId="scenario-1" /></MemoryRouter>);
}

afterEach(cleanup);

describe("InitializationReadonlyPreview", () => {
  it("uses projected Node findings and routes to the exact Initialization item", () => {
    renderPreview(entity("node", "north", "北区医院"));
    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("节点访问状态")).toBeInTheDocument();
    expect(within(previewRegion).getByText("可用")).toBeInTheDocument();
    expect(within(previewRegion).getByText("玩家知识", { exact: false })).toBeInTheDocument();
    expect(within(previewRegion).getByText("已知")).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/initialization?domain=nodes&group=node-type%3Afacility&item=node%3Anorth",
    );
    expect(previewRegion.querySelectorAll("input, select, textarea")).toHaveLength(0);
  });

  it("shows exact Fact findings rather than copied design values", () => {
    renderPreview(entity("node", "north", "北区医院"), { key: "ready", name: "准备完成", value_type: "BOOLEAN" });
    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("真实初始值", { exact: false })).toBeInTheDocument();
    expect(within(previewRegion).getByText("是")).toBeInTheDocument();
    expect(within(previewRegion).getByText("玩家知识", { exact: false })).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/initialization?domain=nodes&group=node-type%3Afacility&item=node%3Anorth&fact=ready",
    );
  });

  it("shows Actor and Relation source badges and exact Initialization locators", () => {
    const { rerender } = renderPreview(entity("actor", "operator", "现场操作员"));
    let previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("初始位置")).toBeInTheDocument();
    expect(within(previewRegion).getByText("北区医院")).toBeInTheDocument();
    expect(within(previewRegion).getByText("主要参与者")).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/initialization?domain=actors&group=role%3Aoperator&item=actor%3Aoperator",
    );

    rerender(<MemoryRouter><InitializationReadonlyPreview entity={entity("relation", "north__connects__south", "关系")} document={document} preview={preview} loading={false} scenarioId="scenario-1" /></MemoryRouter>);
    previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("关系可见性")).toBeInTheDocument();
    expect(within(previewRegion).getByText("可见")).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/initialization?domain=relations&group=relation-type%3Aconnects&item=relation%3Anorth__connects__south",
    );
  });

  it("shows Resource pool projection values and links to that projected pool", () => {
    renderPreview(entity("resource", "general_parts", "通用工程部件"));
    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getAllByText(/数量/)).toHaveLength(2);
    expect(within(previewRegion).getByText("10")).toBeInTheDocument();
    expect(within(previewRegion).getByText("2")).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/initialization?domain=resources&group=resource-pools&item=pool%3Amain_stock%3Ageneral_parts%3Awest",
    );
    expect(previewRegion.querySelectorAll("input, select, textarea")).toHaveLength(0);
  });

  it("shows actionable current-Node reasons when a new incomplete Node blocks preview", () => {
    const error = new ApiError("cannot preview", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "missing", loc: ["world", "nodes", 0, "name"], msg: "Field required" }],
    });
    render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "north", "北区医院")} document={document} preview={null} loading={false} scenarioId="scenario-1" previewError={error} /></MemoryRouter>);

    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("暂时无法生成完整开局配置")).toBeInTheDocument();
    expect(within(previewRegion).getByText("该对象还有 1 项配置未完成")).toBeInTheDocument();
    expect(within(previewRegion).getByText("还需要填写显示名称。")).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/world-entities/north?focus_path=name");
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toBeInTheDocument();
    expect(within(previewRegion).queryByText("world.nodes.0.name")).not.toBeInTheDocument();
    expect(previewRegion.querySelectorAll("input, select, textarea")).toHaveLength(0);

    expect(within(previewRegion).queryByText("world.nodes.0.name")).not.toBeInTheDocument();
    expect(within(previewRegion).queryByText("Field required")).not.toBeInTheDocument();
    expect(within(previewRegion).queryByText(/技术详情|Schema path/)).not.toBeInTheDocument();
  });

  it("routes an Initialization-owned missing Node field to the same typed destination as the workspace issue dialog", () => {
    const error = new ApiError("cannot preview", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ type: "missing", loc: ["world", "nodes", 0, "initial_access"], msg: "Field required" }],
    });
    render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "north", "北区医院")} document={document} preview={null} loading={false} scenarioId="scenario-1" previewError={error} /></MemoryRouter>);
    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByRole("link", { name: "前往" })).toHaveAttribute(
      "href",
      "/scenarios/scenario-1/edit/initialization?domain=nodes&group=node-type%3Afacility&item=node%3Anorth&focus_path=world.nodes.north.initial_access",
    );
  });

  it("keeps the computable focused projection visible without leaking omitted global issues", () => {
    const partialPreview = {
      ...preview,
      partial: true,
      omitted_issue_count: 2,
      issues: [{ type: "missing", loc: ["world", "nodes", 1, "name"], msg: "Field required" }],
    };
    render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "north", "北区医院")} document={document} preview={partialPreview} loading={false} scenarioId="scenario-1" /></MemoryRouter>);
    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("节点访问状态")).toBeInTheDocument();
    expect(within(previewRegion).queryByText(/草稿还有/)).not.toBeInTheDocument();
    expect(within(previewRegion).queryByRole("region", { name: "当前对象待完善配置" })).not.toBeInTheDocument();
    expect(within(previewRegion).queryByText("南区医院")).not.toBeInTheDocument();
    expect(previewRegion.querySelectorAll("input, select, textarea")).toHaveLength(0);
  });

  it("shows only each focused Node's issues and leaves a healthy Node warning-free", () => {
    const globalIssues = [
      { type: "missing", loc: ["world", "nodes", 0, "initial_access"], msg: "Field required" },
      { type: "missing", loc: ["world", "nodes", 0, "initial_visibility"], msg: "Field required" },
      { type: "missing", loc: ["world", "nodes", 1, "name"], msg: "Field required" },
    ];
    const partialPreview: InitializationPreview = { ...preview, partial: true, omitted_issue_count: 3, issues: globalIssues };

    const view = render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "north", "北区医院")} document={document} preview={partialPreview} loading={false} scenarioId="scenario-1" /></MemoryRouter>);
    let previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("该对象还有 2 项配置未完成")).toBeInTheDocument();
    expect(previewRegion.querySelectorAll(".initialization-preview-issue-row")).toHaveLength(2);
    expect(previewRegion.querySelectorAll(".initialization-preview-issue")).toHaveLength(0);
    expect(within(previewRegion).queryByText("还需要填写显示名称。")).not.toBeInTheDocument();
    expect(within(previewRegion).getAllByRole("link", { name: "前往" })).toHaveLength(2);

    view.unmount();
    render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "south", "南区医院")} document={document} preview={partialPreview} loading={false} scenarioId="scenario-1" /></MemoryRouter>);
    previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("该对象还有 1 项配置未完成")).toBeInTheDocument();
    expect(within(previewRegion).getByText("还需要填写显示名称。")).toBeInTheDocument();
    expect(within(previewRegion).queryByText("还需要填写初始访问状态。")).not.toBeInTheDocument();
    expect(previewRegion.querySelectorAll(".initialization-preview-issue-row")).toHaveLength(1);
    cleanup();

    const healthyDocument = structuredClone(document) as JsonObject;
    const world = healthyDocument.world as JsonObject;
    const centralNode: JsonObject = { key: "central", name: "中央区", node_type_key: "facility" };
    world.nodes = [...(world.nodes as JsonObject[]), centralNode];
    const centralFinding: InitializationFinding = {
      ...findings[0],
      identity: "node:central:initial_access",
      value: "LOCKED",
      locator: { section: "world-entities", object_kind: "node", object_key: "central", field_path: "initial_access" },
    };
    const centralItem = projectionItem("node:central", "node", "central", ["node:central:initial_access"], { key: "central", type: "设施" });
    const healthyPreview: InitializationPreview = {
      ...partialPreview,
      projection: {
        ...partialPreview.projection,
        findings: [...partialPreview.projection.findings, centralFinding],
        domains: partialPreview.projection.domains.map((domain) => domain.id === "nodes"
          ? { ...domain, groups: domain.groups.map((group, index) => index === 0 ? { ...group, items: [...group.items, centralItem] } : group) }
          : domain),
      },
    };
    render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "central", "中央区", centralNode)} document={healthyDocument} preview={healthyPreview} loading={false} scenarioId="scenario-1" /></MemoryRouter>);
    previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("节点访问状态")).toBeInTheDocument();
    expect(within(previewRegion).getByText("锁定")).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toBeInTheDocument();
    expect(previewRegion.querySelector(".initialization-preview-object-issues")).toBeNull();
    expect(within(previewRegion).queryByText(/草稿还有/)).not.toBeInTheDocument();
    expect(within(previewRegion).queryByText("还需要填写初始访问状态。")).not.toBeInTheDocument();
    expect(within(previewRegion).queryByText("还需要填写显示名称。")).not.toBeInTheDocument();
  });

  it("keeps a direct cross-owner blocker on its source object and links to the canonical owner", () => {
    const partialPreview: InitializationPreview = {
      ...preview,
      partial: true,
      issues: [
        {
          canonical_owner: "world-entities",
          reference_owner: "node-types",
          field_path: "node_type_key",
          locator: { section: "world-entities", object_kind: "node", object_key: "north", field_path: "node_type_key" },
          loc: [],
          msg: "A reference is not valid",
        },
        { type: "missing", loc: ["world", "nodes", 1, "name"], msg: "Field required" },
      ],
    };
    render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "north", "北区医院")} document={document} preview={partialPreview} loading={false} scenarioId="scenario-1" /></MemoryRouter>);
    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText("该对象还有 1 项配置未完成")).toBeInTheDocument();
    expect(within(previewRegion).getByText(/节点类型：此字段引用的节点类型/)).toBeInTheDocument();
    expect(within(previewRegion).getByRole("link", { name: "前往节点类型" })).toHaveAttribute("href", "/scenarios/scenario-1/edit/node-types");
    expect(within(previewRegion).queryByText("还需要填写显示名称。")).not.toBeInTheDocument();
  });

  it("offers retry and the Initialization owner for non-structured preview failures", () => {
    const onRetry = vi.fn();
    render(<MemoryRouter><InitializationReadonlyPreview entity={entity("node", "north", "北区医院")} document={document} preview={null} loading={false} scenarioId="scenario-1" previewError={new Error("network down")} onRetry={onRetry} /></MemoryRouter>);
    const previewRegion = screen.getByRole("region", { name: "开局配置只读预览" });
    expect(within(previewRegion).getByText(/预览服务暂未返回可定位的问题/)).toBeInTheDocument();
    fireEvent.click(within(previewRegion).getByRole("button", { name: "重新读取预览" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(within(previewRegion).getByRole("link", { name: "前往初始化" })).toBeInTheDocument();
  });
});
