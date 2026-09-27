import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { IdentityCreationDialog } from "./IdentityCreationDialog";

afterEach(cleanup);

describe("IdentityCreationDialog required reference picker", () => {
  it("shows an explicit disabled prompt and creates only after selecting an existing target", () => {
    const onCreate = vi.fn();
    render(<MemoryRouter><IdentityCreationDialog
      title="添加区域库存情报"
      fields={[{
        key: "region_key",
        label: "区域",
        type: "select",
        required: true,
        omitEmptyOption: true,
        options: [{ key: "north_region", name: "北部区域" }],
      }]}
      onCancel={vi.fn()}
      onCreate={onCreate}
    /></MemoryRouter>);

    const region = screen.getByRole("combobox", { name: /区域/ }) as HTMLSelectElement;
    expect(region).toHaveValue("__identity_creation_unselected__");
    expect(region.selectedOptions[0]).toBeDisabled();
    expect(region.selectedOptions[0]).toHaveTextContent("请选择已有项");
    expect(Array.from(region.options).some((option) => option.value === "")).toBe(false);
    expect(onCreate).not.toHaveBeenCalled();

    const create = screen.getByRole("button", { name: "创建" });
    expect(create).toBeDisabled();
    fireEvent.click(create);
    expect(onCreate).not.toHaveBeenCalled();

    fireEvent.change(region, { target: { value: "north_region" } });
    expect(create).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    expect(onCreate).toHaveBeenCalledWith({ region_key: "north_region" });
  });

  it("cancels without creating an object", () => {
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(<MemoryRouter><IdentityCreationDialog
      title="添加区域库存情报"
      fields={[{
        key: "region_key",
        label: "区域",
        type: "select",
        required: true,
        omitEmptyOption: true,
        options: [{ key: "north_region", name: "北部区域" }],
      }]}
      onCancel={onCancel}
      onCreate={onCreate}
    /></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onCreate).not.toHaveBeenCalled();
  });
});
