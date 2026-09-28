import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../../api";
import type { JsonObject } from "../../editor";
import type { CompletenessItem, CompletenessResult, InitializationPreview } from "../../types";
import { CompletenessPanel, CompletenessSummary } from "./CompletenessPanel";
import { completenessLocatorHref, findingsForObject } from "./completeness-navigation";

afterEach(cleanup);

const document: JsonObject = {
  world: { nodes: [{ key: "new_node", name: "新节点", node_type_key: "" }], node_types: [] },
  actions: [], initialization: {},
};

function item(overrides: Partial<CompletenessItem> = {}): CompletenessItem {
  return {
    key: "node:new_node:node-type",
    title: "配置节点「新节点」的节点类型",
    level: "INCOMPLETE_REQUIRED",
    dependency_kind: "HARD_REQUIRED",
    message: "节点必须关联一个现有节点类型。",
    path: "world.nodes.new_node.node_type_key",
    locator: { object_kind: "node", object_key: "new_node", field_path: "node_type_key" },
    action: "OPEN",
    ...overrides,
  };
}

function result(items: CompletenessItem[], validationIssues: CompletenessResult["validation_issues"] = []): CompletenessResult {
  return {
    scenario_id: "s1",
    base_revision: 1,
    items,
    validation_issues: validationIssues,
    required_missing: items.filter((entry) => entry.level === "INCOMPLETE_REQUIRED").length,
    recommended_missing: items.filter((entry) => entry.level === "OPTIONAL_ENHANCEMENT").length,
    validation_issue_count: validationIssues.length,
    reference_edge_count: 0,
  };
}

function renderPanel(props: { result: CompletenessResult; initializationPreview?: InitializationPreview | null; initializationError?: unknown }) {
  return render(<MemoryRouter><CompletenessPanel document={document} scenarioId="s1" {...props} /></MemoryRouter>);
}

describe("CompletenessPanel global issue center", () => {
  it("keeps the current field as the primary action and the canonical owner as a secondary action", () => {
    renderPanel({ result: result([item({ reference_locator: { object_kind: "node_type", object_key: "facility", field_path: null }, reference_owner: "node_type" })]) });

    expect(screen.getByRole("link", { name: /定位到节点类型/ })).toHaveAttribute("href", "/scenarios/s1/edit/world-entities/new_node?focus_path=node_type_key");
    expect(screen.getByRole("link", { name: "前往节点类型" })).toHaveAttribute("href", "/scenarios/s1/edit/node-types");
  });

  it("localizes schema errors and deduplicates them with the matching completeness target", () => {
    renderPanel({ result: result([item()], [{
      severity: "ERROR", code: "SCENARIO_DOCUMENT_SCHEMA_INVALID", path: "world.nodes.0.node_type_key",
      message: "Field required", type: "missing", locator: { object_kind: "node", object_key: "new_node", field_path: "node_type_key" },
    }]) });

    expect(screen.getByText("当前工作副本有 1 项待处理配置")).toBeInTheDocument();
    expect(screen.getByText("请填写节点类型。")).toBeInTheDocument();
    expect(screen.queryByText("Field required")).not.toBeInTheDocument();
    expect(screen.queryByText("world.nodes.0.node_type_key")).not.toBeInTheDocument();
  });

  it("merges completeness, schema, and initialization blockers for one empty field", () => {
    const initializationError = new ApiError("invalid", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{
        canonical_owner: "node", field_path: "node_type_key",
        locator: { object_kind: "node", object_key: "new_node", field_path: "node_type_key" },
        type: "string_too_short", msg: "String should have at least 1 character",
      }],
    });
    const { container } = renderPanel({
      result: result([item()], [{
        severity: "ERROR", code: "SCENARIO_DOCUMENT_SCHEMA_INVALID", path: "world.nodes.0.node_type_key",
        message: "Field required", type: "string_too_short", locator: { object_kind: "node", object_key: "new_node", field_path: "node_type_key" },
      }]),
      initializationError,
    });

    expect(container.querySelectorAll(".global-issue")).toHaveLength(1);
    expect(screen.getByText("当前工作副本有 1 项待处理配置")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "定位到节点类型" })).toHaveAttribute("href", "/scenarios/s1/edit/world-entities/new_node?focus_path=node_type_key");
    expect(screen.getByText("配置完整度 · 结构与发布检查 · 开局预览")).toBeInTheDocument();
  });

  it("projects publish readiness blockers to their canonical owner when no field exists", () => {
    renderPanel({ result: result([], [{
      severity: "ERROR", code: "SCENARIO_ACTION_REQUIRED", path: "actions",
      message: "A publishable Scenario needs at least one Action", locator: null,
    }]) });

    expect(screen.getByText("可发布的场景至少需要一个行动。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往行动" })).toHaveAttribute("href", "/scenarios/s1/edit/actions");
    expect(screen.queryByText("A publishable Scenario needs at least one Action")).not.toBeInTheDocument();
    expect(screen.queryByText("actions")).not.toBeInTheDocument();
  });

  it("shows exact Initialization 422 remediation in the same global list", () => {
    const initializationError = new ApiError("invalid", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{
        canonical_owner: "node", field_path: "initial_access",
        locator: { object_kind: "node", object_key: "new_node", field_path: "initial_access" },
        type: "missing", msg: "Field required",
      }],
    });
    renderPanel({ result: result([]), initializationError });

    expect(screen.getByText(/还需要填写初始访问状态/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往节点「新节点」" })).toHaveAttribute("href", "/scenarios/s1/edit/initialization?domain=nodes&group=node-type%3Aunassigned&item=node%3Anew_node&focus_path=world.nodes.new_node.initial_access");
  });

  it("keeps unlocatable initialization issues visible without inventing a route", () => {
    const initializationError = new ApiError("invalid", 422, "SCENARIO_INITIALIZATION_PREVIEW_INVALID", {
      issues: [{ canonical_owner: "unknown", loc: ["body", "unknown"], type: "value_error", msg: "technical detail" }],
    });
    renderPanel({ result: result([]), initializationError });

    expect(screen.getByText(/无法安全归属|无法自动定位/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "前往配置检查" })).not.toBeInTheDocument();
    expect(screen.queryByText("technical detail")).not.toBeInTheDocument();
  });

  it("groups findings by concept domain and author-facing severity", () => {
    renderPanel({ result: result([
      item(),
      item({ key: "resource:fuel:source", title: "补充资源来源", level: "OPTIONAL_ENHANCEMENT", dependency_kind: "RECOMMENDED", locator: { object_kind: "resource", object_key: "fuel", field_path: "source_hint" }, action: "OPEN" }),
    ]) });

    expect(screen.getByText("世界模型")).toBeInTheDocument();
    expect(screen.getByText("推荐完善")).toBeInTheDocument();
    expect(screen.queryByText("PUBLISH_REQUIRED")).not.toBeInTheDocument();
    expect(screen.queryByText("RECOMMENDED")).not.toBeInTheDocument();
    expect(screen.queryByText(/另有 .* 验证与发布/)).not.toBeInTheDocument();
  });

  it("keeps recommendations out of the blocker summary", () => {
    render(<MemoryRouter><CompletenessSummary result={result([item({ level: "OPTIONAL_ENHANCEMENT", dependency_kind: "RECOMMENDED" })])} scenarioId="s1" /></MemoryRouter>);

    expect(screen.getByText("没有必须处理项")).toBeInTheDocument();
    expect(screen.getByText("待配置 0 · 推荐完善 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看配置检查" })).toHaveAttribute("href", "/scenarios/s1/edit/configuration-check");
  });

  it("filters contextual guidance to the selected object", () => {
    const findings = result([
      item(),
      item({ key: "resource:fuel:pool", locator: { object_kind: "resource", object_key: "fuel", field_path: null } }),
    ]);
    expect(findingsForObject(findings, "node", "new_node").map((entry) => entry.key)).toEqual(["node:new_node:node-type"]);
  });

  it("builds the exact canonical locator link", () => {
    expect(completenessLocatorHref({ object_kind: "action", object_key: "inspect patient", field_path: "expected_outcomes" }, "s1")).toBe(
      "/scenarios/s1/edit/actions/inspect%20patient?focus_path=expected_outcomes",
    );
  });
});
