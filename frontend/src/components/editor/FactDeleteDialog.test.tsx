import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { FactDeleteDialog } from "./FactDeleteDialog";

afterEach(cleanup);

describe("FactDeleteDialog", () => {
  it("shows a blocked Chinese reference list without a force-delete action", () => {
    render(<MemoryRouter><FactDeleteDialog state={{ kind: "blocked", factName: "设施修复类型", nodeKey: "facility", factKey: "repair_type", references: [{ type: "规则", name: "恢复通信", path: "rules.0.condition.fact_key", href: "/scenarios/s1/edit/rules/r1" }] }} onClose={vi.fn()} /></MemoryRouter>);

    expect(screen.getByRole("dialog", { name: "无法删除「设施修复类型」" })).toBeInTheDocument();
    expect(screen.getByText("规则")).toBeInTheDocument();
    expect(screen.getAllByText(/恢复通信/)).toHaveLength(2);
    expect(screen.queryByText("rules.0.condition.fact_key")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往恢复通信" })).toHaveAttribute("href", "/scenarios/s1/edit/rules/r1");
    expect(screen.queryByText(/技术详情|Schema path/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "关闭" })).toHaveLength(2);
    expect(screen.queryByText(/强制|解除引用/)).not.toBeInTheDocument();
  });

  it("groups a large reference set while hiding raw field paths and keeping navigation", () => {
    render(<MemoryRouter><FactDeleteDialog state={{ kind: "blocked", factName: "状态", nodeKey: "target", factKey: "status", references: [
      { type: "规则", name: "规则一", path: "rules.0.condition.fact_key", href: "/rules/r1" },
      { type: "规则", name: "规则二", path: "rules.1.condition.fact_key", href: "/rules/r2" },
      { type: "派生状态", name: "可运行", path: "derived_states.0.dependencies.0.fact_key", href: "/derived/d1" },
    ] }} onClose={vi.fn()} /></MemoryRouter>);

    const dialog = screen.getByRole("dialog", { name: "无法删除「状态」" });
    expect(within(dialog).getAllByText("规则", { selector: "h5" })).toHaveLength(1);
    expect(within(dialog).getByText("2 个对象")).toBeInTheDocument();
    const derivedRow = within(dialog).getByText("可运行").closest("li")!;
    expect(within(derivedRow).queryByText("derived_states.0.dependencies.0.fact_key")).not.toBeInTheDocument();
    expect(within(derivedRow).getByRole("link", { name: "前往可运行" })).toHaveAttribute("href", "/derived/d1");
    expect(within(derivedRow).queryByText(/技术详情|Schema path/)).not.toBeInTheDocument();
    expect(dialog.querySelector(":scope > .fact-delete-dialog-heading")).toBeInTheDocument();
    expect(dialog.querySelector(":scope > .fact-delete-dialog-body")).toBeInTheDocument();
    expect(dialog.querySelector(":scope > .fact-delete-dialog-footer")).toBeInTheDocument();
  });

  it("keeps long identities inside the shared scrollable dialog frame", () => {
    const longKey = "city_distribution_center__located_in__west_logistics_district__with_a_very_long_relation_identity";
    render(<MemoryRouter><FactDeleteDialog state={{ kind: "blocked", factName: longKey, nodeKey: longKey, factKey: "operational_state_with_a_long_key", references: [{ type: "关系", name: longKey, path: `world.relations.${longKey}.condition.conditions.0.fact_key`, href: null }] }} onClose={vi.fn()} /></MemoryRouter>);

    const dialog = screen.getByRole("dialog", { name: `无法删除「${longKey}」` });
    expect(within(dialog).getByText(longKey)).toBeInTheDocument();
    expect(within(dialog).queryByText(`world.relations.${longKey}.condition.conditions.0.fact_key`)).not.toBeInTheDocument();
    expect(within(dialog).getAllByRole("button", { name: "关闭" })).toHaveLength(2);
    expect(dialog.querySelector(":scope > .fact-delete-dialog-body")).toHaveClass("fact-delete-dialog-body");
  });

  it("keeps a safe delete behind custom cancel and confirm actions", () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    const nativeConfirm = vi.spyOn(window, "confirm");
    render(<MemoryRouter><FactDeleteDialog state={{ kind: "confirm", factName: "设施修复类型", nodeKey: "facility", factKey: "repair_type", document: {}, transformedDocument: {} }} onClose={onClose} onConfirm={onConfirm} /></MemoryRouter>);

    expect(screen.getByRole("dialog", { name: "删除「设施修复类型」？" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(nativeConfirm).not.toHaveBeenCalled();
  });

  it("explains a failed delete preflight without offering deletion", () => {
    const onClose = vi.fn();
    render(<MemoryRouter><FactDeleteDialog state={{ kind: "error", factName: "设施修复类型", nodeKey: "facility", factKey: "repair_type", reason: "当前工作副本未通过校验，暂时无法确认是否可以安全删除。", technical: "字段位置：body.operation", code: "VALIDATION_ERROR" }} onClose={onClose} /></MemoryRouter>);

    const dialog = screen.getByRole("dialog", { name: "暂时无法检查是否可以删除" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("当前工作副本未通过校验，暂时无法确认是否可以安全删除。")).toBeInTheDocument();
    expect(screen.queryByText(/body\.operation|VALIDATION_ERROR|技术详情/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "关闭" })[1]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
