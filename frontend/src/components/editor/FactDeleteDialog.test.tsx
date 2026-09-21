import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { FactDeleteDialog } from "./FactDeleteDialog";

afterEach(cleanup);

describe("FactDeleteDialog", () => {
  it("shows a blocked Chinese reference list without a force-delete action", () => {
    render(<MemoryRouter><FactDeleteDialog state={{ kind: "blocked", factName: "设施修复类型", nodeKey: "facility", factKey: "repair_type", references: [{ type: "规则", name: "恢复通信", path: "rules.0.condition.fact_key", href: "/scenarios/s1/edit/rules/r1" }] }} onClose={vi.fn()} /></MemoryRouter>);

    expect(screen.getByRole("dialog", { name: "无法删除「设施修复类型」" })).toBeInTheDocument();
    expect(screen.getByText("规则")).toBeInTheDocument();
    expect(screen.getByText(/恢复通信/)).toBeInTheDocument();
    expect(screen.getByText("rules.0.condition.fact_key")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往" })).toHaveAttribute("href", "/scenarios/s1/edit/rules/r1");
    expect(screen.getAllByRole("button", { name: "关闭" })).toHaveLength(2);
    expect(screen.queryByText(/强制|解除引用/)).not.toBeInTheDocument();
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
    render(<MemoryRouter><FactDeleteDialog state={{ kind: "error", factName: "设施修复类型", nodeKey: "facility", factKey: "repair_type", reason: "接口校验未通过（body.operation）", code: "VALIDATION_ERROR" }} onClose={onClose} /></MemoryRouter>);

    const dialog = screen.getByRole("dialog", { name: "暂时无法检查是否可以删除" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("接口校验未通过（body.operation）")).toBeInTheDocument();
    expect(screen.getByText("错误代码：VALIDATION_ERROR")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "关闭" })[1]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
