import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Link, MemoryRouter, useLocation } from "react-router-dom";

import { useUnsavedChangesGuard } from "./useUnsavedChangesGuard";

function GuardHarness() {
  useUnsavedChangesGuard(true, "Unsaved changes");
  return <><Link to="/next">Leave</Link><output>{useLocation().pathname}</output></>;
}

afterEach(cleanup);

describe("unsaved route guard", () => {
  it("protects refresh and in-app links without owning draft state", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<MemoryRouter initialEntries={["/current"]}><GuardHarness /></MemoryRouter>);

    const beforeUnload = new Event("beforeunload", { cancelable: true });
    expect(window.dispatchEvent(beforeUnload)).toBe(false);
    fireEvent.click(screen.getByRole("link", { name: "Leave" }));
    expect(confirm).toHaveBeenCalledWith("Unsaved changes");
    expect(screen.getByRole("status")).toHaveTextContent("/current");

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("link", { name: "Leave" }));
    expect(screen.getByRole("status")).toHaveTextContent("/next");
  });
});
