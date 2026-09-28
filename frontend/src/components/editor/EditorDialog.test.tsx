import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { EditorDialog } from "./EditorDialog";

function DialogHarness() {
  const [open, setOpen] = useState(true);
  return open ? <EditorDialog
    titleId="dialog-test-title"
    kicker="测试"
    title="共享弹窗"
    onClose={() => setOpen(false)}
    footer={<button type="button" onClick={() => setOpen(false)}>完成</button>}
  >
    <button type="button">正文操作</button>
  </EditorDialog> : <p>已关闭</p>;
}

afterEach(cleanup);

describe("EditorDialog shared keyboard behavior", () => {
  it("focuses into the dialog, traps Tab, and closes on Escape", () => {
    render(<DialogHarness />);
    const dialog = screen.getByRole("dialog", { name: "共享弹窗" });
    const close = within(dialog).getByRole("button", { name: "关闭" });
    const content = within(dialog).getByRole("button", { name: "正文操作" });
    const footer = within(dialog).getByRole("button", { name: "完成" });

    expect(close).toHaveFocus();
    expect(dialog.querySelector(":scope > .fact-delete-dialog-heading")).toBeInTheDocument();
    expect(dialog.querySelector(":scope > .fact-delete-dialog-body")).toHaveClass("fact-delete-dialog-body");
    expect(dialog.querySelector(":scope > .fact-delete-dialog-footer")).toBeInTheDocument();

    footer.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(footer).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByText("已关闭")).toBeInTheDocument();
    expect(content).not.toBeInTheDocument();
  });
});
