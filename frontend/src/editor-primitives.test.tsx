import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AdvancedSection,
  EnumSelect,
  MultiValuePicker,
  NestedCard,
  NestedObjectHeader,
  ReorderControls,
  ReferencePicker,
  TextInput,
} from "./components/editor/FormPrimitives";

afterEach(cleanup);

describe("shared editor form primitives", () => {
  it("associates labels and applies the requested control sizing", () => {
    render(<TextInput value="Clinic" onChange={vi.fn()} path="node.name" label="Name" size="wide" help="玩家可见名称" />);

    const input = screen.getByLabelText("显示名称");
    expect(input).toHaveValue("Clinic");
    expect(input).toHaveClass("editor-control");
    expect(input.closest(".form-field")).toHaveClass("form-field-wide");
    expect(screen.getByText("玩家可见名称")).toBeInTheDocument();
  });

  it("uses the same enum and reference presentation contract", () => {
    render(<>
      <EnumSelect value="KNOWN" onChange={vi.fn()} path="fact.visibility" label="Visibility" choices={["KNOWN", "HIDDEN"]} />
      <ReferencePicker value="clinic" onChange={vi.fn()} path="action.target" label="Node" domain="node" document={{ world: { nodes: [{ key: "clinic", name: "中央医院" }] } }} />
    </>);

    expect(screen.getByLabelText("可见性")).toHaveValue("KNOWN");
    expect(screen.getByLabelText("节点")).toHaveValue("clinic");
    expect(screen.getByRole("option", { name: /中央医院.*clinic/ })).toBeInTheDocument();
  });

  it("replaces native multiple select with searchable chips", () => {
    const onChange = vi.fn();
    render(<MultiValuePicker value={["EXECUTE_ACTION"]} onChange={onChange} path="action.capabilities" label="Allowed capabilities" options={[{ key: "EXECUTE_ACTION", name: "执行行动" }, { key: "LOGISTICS", name: "物流" }]} />);

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /允许的能力.*添加/ }));
    fireEvent.change(screen.getByLabelText("搜索可选值"), { target: { value: "物流" } });
    fireEvent.click(screen.getByRole("option", { name: /物流/ }));
    expect(onChange).toHaveBeenLastCalledWith(["EXECUTE_ACTION", "LOGISTICS"]);
    fireEvent.click(screen.getByRole("button", { name: /执行行动 移除/ }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("keeps Advanced JSON collapsed and preserves the original value on parse error", () => {
    const onChange = vi.fn();
    render(<AdvancedSection value={{ keep: true }} onChange={onChange} path="action.authority_policy" label="Authority policy" />);

    expect(screen.queryByLabelText(/权限策略 JSON/)).not.toBeInTheDocument();
    const fallbackToggle = screen.getByRole("button", { name: /未识别结构/ });
    expect(fallbackToggle).toHaveTextContent("未支持的结构");
    expect(screen.getByText(/当前结构无法由可视编辑器完整编辑/)).toBeInTheDocument();
    fireEvent.click(fallbackToggle);
    const textarea = screen.getByLabelText(/权限策略 JSON/);
    fireEvent.change(textarea, { target: { value: "{" } });
    fireEvent.blur(textarea);
    expect(screen.getByRole("alert")).toHaveTextContent("JSON");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows a nested summary before opening its children", () => {
    render(<NestedCard title="Condition" summary="事实等于 · operational = true"><p>nested editor body</p></NestedCard>);

    expect(screen.getByText("事实等于 · operational = true")).toBeInTheDocument();
    expect(screen.queryByText("nested editor body")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /条件/ }));
    expect(screen.getByText("nested editor body")).toBeInTheDocument();
  });

  it("keeps nested object type and identity on separate hierarchy levels", () => {
    render(<NestedObjectHeader typeLabel="FACT" identity="operational" />);

    expect(screen.getByText("事实")).toHaveClass("nested-object-type");
    expect(screen.getByText("operational")).toHaveClass("nested-object-identity");
  });

  it("renders one compact shared reorder group with stable boundary buttons", () => {
    const onMove = vi.fn();
    render(<><ReorderControls index={0} count={3} onMove={onMove} /><ReorderControls index={1} count={3} onMove={onMove} /><ReorderControls index={2} count={3} onMove={onMove} /></>);

    const upButtons = screen.getAllByRole("button", { name: "上移" });
    const downButtons = screen.getAllByRole("button", { name: "下移" });
    expect(upButtons[0]).toBeDisabled();
    expect(downButtons[0]).toBeEnabled();
    expect(upButtons[1]).toBeEnabled();
    expect(downButtons[1]).toBeEnabled();
    expect(upButtons[2]).toBeEnabled();
    expect(downButtons[2]).toBeDisabled();
    expect(upButtons[1].parentElement).toHaveClass("reorder-controls");
    fireEvent.click(downButtons[1]);
    expect(onMove).toHaveBeenCalledWith("down");
  });

  it("does not render reorder affordances for a single item", () => {
    render(<ReorderControls index={0} count={1} onMove={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "上移" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "下移" })).not.toBeInTheDocument();
  });
});
