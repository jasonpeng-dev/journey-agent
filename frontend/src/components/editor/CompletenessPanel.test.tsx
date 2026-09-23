import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CompletenessItem, CompletenessResult } from "../../types";
import { CompletenessPanel, CompletenessSummary } from "./CompletenessPanel";
import { completenessLocatorHref, findingsForObject } from "./completeness-navigation";

afterEach(cleanup);

function item(overrides: Partial<CompletenessItem> = {}): CompletenessItem {
  return {
    key: "action:inspect:resolve-rule",
    title: "为行动配置结算规则",
    level: "INCOMPLETE_REQUIRED",
    dependency_kind: "PUBLISH_REQUIRED",
    message: "可从当前行动上下文创建规则。",
    path: "actions.inspect",
    locator: { object_kind: "action", object_key: "inspect", field_path: null },
    action: "CREATE",
    ...overrides,
  };
}

function result(items: CompletenessItem[]): CompletenessResult {
  return {
    scenario_id: "s1",
    base_revision: 1,
    items,
    required_missing: items.filter((entry) => entry.level === "INCOMPLETE_REQUIRED").length,
    recommended_missing: items.filter((entry) => entry.level === "OPTIONAL_ENHANCEMENT").length,
    validation_issue_count: 0,
    reference_edge_count: 0,
  };
}

describe("CompletenessPanel", () => {
  it("exposes one deterministic create-and-link action", () => {
    const onCreate = vi.fn();
    const finding = item();
    render(<MemoryRouter><CompletenessPanel result={result([finding])} scenarioId="s1" onCreate={onCreate} /></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: "创建并关联" }));
    expect(onCreate).toHaveBeenCalledWith(finding);
    expect(screen.queryByRole("link", { name: "前往" })).not.toBeInTheDocument();
  });

  it("routes missing NodeType ownership to the NodeType page instead of creating inline", () => {
    const onCreate = vi.fn();
    render(<MemoryRouter><CompletenessPanel result={result([item({ key: "node:new_node:node-type", locator: { object_kind: "node", object_key: "new_node", field_path: "node_type_key" } })])} scenarioId="s1" onCreate={onCreate} /></MemoryRouter>);

    expect(screen.getByRole("link", { name: "前往节点类型" })).toHaveAttribute("href", "/scenarios/s1/edit/node-types");
    expect(screen.queryByRole("button", { name: "创建并关联" })).not.toBeInTheDocument();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("groups findings by domain and natural-language severity without leaking enums", () => {
    const findings = [
      item(),
      item({ key: "resource:fuel:source", title: "补充资源来源", level: "OPTIONAL_ENHANCEMENT", dependency_kind: "RECOMMENDED", locator: { object_kind: "resource", object_key: "fuel", field_path: null }, action: "OPEN" }),
    ];
    render(<MemoryRouter><CompletenessPanel result={result(findings)} scenarioId="s1" /></MemoryRouter>);

    expect(screen.getByText("行为系统")).toBeInTheDocument();
    expect(screen.getByText("世界模型")).toBeInTheDocument();
    expect(screen.getAllByText("必须处理").length).toBeGreaterThan(0);
    expect(screen.getAllByText("推荐完善").length).toBeGreaterThan(0);
    expect(screen.queryByText("PUBLISH_REQUIRED")).not.toBeInTheDocument();
    expect(screen.queryByText("RECOMMENDED")).not.toBeInTheDocument();
  });

  it("keeps recommendations out of the blocker summary", () => {
    render(<MemoryRouter><CompletenessSummary result={result([item({ level: "OPTIONAL_ENHANCEMENT", dependency_kind: "RECOMMENDED" })])} scenarioId="s1" /></MemoryRouter>);

    expect(screen.getByText("没有必须处理项")).toBeInTheDocument();
    expect(screen.getByText("待配置 0 · 推荐完善 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看配置检查" })).toHaveAttribute("href", "/scenarios/s1/edit/configuration-check");
  });

  it("shows real required findings as blockers", () => {
    render(<MemoryRouter><CompletenessSummary result={result([item()])} scenarioId="s1" /></MemoryRouter>);
    expect(screen.getByText("1 项必须处理")).toBeInTheDocument();
  });

  it("filters contextual guidance to the selected object", () => {
    const findings = result([
      item(),
      item({ key: "resource:fuel:pool", locator: { object_kind: "resource", object_key: "fuel", field_path: null } }),
    ]);
    expect(findingsForObject(findings, "action", "inspect").map((entry) => entry.key)).toEqual(["action:inspect:resolve-rule"]);
  });

  it("builds the exact canonical locator link", () => {
    expect(completenessLocatorHref({ object_kind: "action", object_key: "inspect patient", field_path: "expected_outcomes" }, "s1")).toBe(
      "/scenarios/s1/edit/actions/inspect%20patient?focus_path=expected_outcomes",
    );
  });
});
